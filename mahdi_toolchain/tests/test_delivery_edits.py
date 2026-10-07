"""Review the received delivery artifacts without new model calls or quality rewrites."""

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from test_parts import settings_with_keys

from mh_toolchain import cli
from mh_toolchain.config import TOOLCHAIN, WorkflowError
from mh_toolchain.files import apply, commit, git, init_run, write_json
from mh_toolchain.graph import build_graph
from mh_toolchain.review_edits import content_hash, stage_review_edits

FIXTURES = Path(__file__).parent / "fixtures"
DELIVERY = json.loads((FIXTURES / "qwen_run01_delivery.json").read_text())
CODE = json.loads((FIXTURES / "qwen_run01_review.json").read_text())
DELIVERY_PATHS = {item["path"] for item in DELIVERY["patch"]["edits"]}


class NoModelCalls:
    """An amendment and final local smoke must not request model inference."""

    def file(self, *_args):
        raise AssertionError("Delivery review unexpectedly generated a new model response")

    def close(self):
        pass


def setup_delivery(run, settings):
    """Reconstruct the received pause, not the unprovided historical checkpoint database."""
    init_run(run, settings.snapshot())
    original = {name: text for name, text in DELIVERY["artifacts"].items()
                if name not in DELIVERY_PATHS and name != "reports/quality.md"}
    apply(run, original, "feat: previously reviewed source, tests and architecture")
    (run / "demo/reports").mkdir(exist_ok=True)
    (run / "demo/reports/quality.md").write_text(DELIVERY["artifacts"]["reports/quality.md"])
    write_json(run / "demo/reports/quality-round-0.json", DELIVERY["quality"])
    commit(run / "demo", "test: received Windows quality results")
    for group in (CODE["attempt_logs"], DELIVERY["attempt_logs"], DELIVERY["cache_logs"],
                  DELIVERY["measured_logs"]):
        for name, raw in group.items():
            (run / "logs" / name).write_bytes(raw.encode("utf-8"))
    (run / "logs/review-edits-001.json").write_bytes(
        DELIVERY["previous_review_record"].encode("utf-8")
    )
    patch = run.parent / "delivery-patch.json"
    patch.write_text(json.dumps(DELIVERY["patch"]))
    config = {"configurable": {"thread_id": "run-01"}, "max_concurrency": 2}
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, NoModelCalls(), saver)
        graph.update_state(config, {
            "run_dir": str(run), "artifacts": DELIVERY["artifacts"], "round": 0,
            "quality": DELIVERY["quality"], "approved": True, "status": "running",
        }, as_node="deploy")
        result = graph.invoke(None, config)
        assert result["__interrupt__"][0].value["stage"] == "delivery"
        (run / "logs/delivery.diff").write_bytes(
            DELIVERY["original_delivery_diff"].encode("utf-8")
        )
    return config, patch


def test_received_delivery_amendment_preserves_quality_and_requires_new_approval(
    tmp_path, monkeypatch, capsys,
):
    settings = settings_with_keys()
    run = tmp_path / "run-01"
    config, patch = setup_delivery(run, settings)
    old_logs = {path: path.read_bytes() for path in (run / "logs").iterdir()}
    old_configuration = (run / "configuration.json").read_bytes()
    old_demo = {path: path.read_bytes() for path in (run / "demo").rglob("*")
                if path.is_file() and ".git" not in path.relative_to(run / "demo").parts}
    monkeypatch.setattr(cli, "run_path", lambda _name: run)
    monkeypatch.setattr(cli, "Generator", lambda _settings, _run: NoModelCalls())
    args = argparse.Namespace(command="amend-review", id="run-01", patch=str(patch))
    assert cli.run_command(args, settings) == 0
    assert "needs_review" in capsys.readouterr().out
    summary = json.loads((run / "summary.json").read_text())
    assert summary["quality"] == DELIVERY["quality"]
    assert summary["model_calls"] == 58 and summary["rejected_outputs"] == 18
    assert summary["model_seconds"] == 3079.924 and summary["review_edits"] == 2
    assert summary["pending"][0]["stage"] == "delivery"
    assert summary["next"] == ["review_delivery"]
    assert not (run / "demo/Dockerfile").exists()
    assert not (run / "demo/reports/deployment.json").exists()
    assert not (run / "logs/deployment.txt").exists()
    assert all(path.read_bytes() == raw for path, raw in old_demo.items())
    for path, raw in old_logs.items():
        if path.name != "delivery.diff":
            assert path.read_bytes() == raw
    assert (run / "logs/review-before-edit-002.diff").read_bytes() == old_logs[
        run / "logs/delivery.diff"
    ]
    record = json.loads((run / "logs/review-edits-002.json").read_text())
    assert record["stage"] == "delivery" and record["checkpoint_updated"]
    assert {edit["path"] for edit in record["edits"]} == DELIVERY_PATHS
    assert "not a local model response" in record["kind"]
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, NoModelCalls(), saver)
        state = graph.get_state(config)
        for name, old in DELIVERY["artifacts"].items():
            if name not in DELIVERY_PATHS:
                assert state.values["artifacts"][name] == old
    assert cli.run_command(args, settings) == 0  # Repeating does not append or auto-approve.
    assert len(list((run / "logs").glob("review-edits-*.json"))) == 2
    assert not (run / "demo/reports/deployment.json").exists()
    resume = argparse.Namespace(command="resume", id="run-01", decision="approve")
    assert cli.run_command(resume, settings) == 0
    summary = json.loads((run / "summary.json").read_text())
    assert summary["status"] == "passed" and summary["quality"] == DELIVERY["quality"]
    assert summary["model_calls"] == 58 and summary["model_seconds"] == 3079.924
    deployment = json.loads((run / "demo/reports/deployment.json").read_text())
    assert deployment["passed"] and deployment["container_build"] == "NOT_RUN"
    assert "PASS: import, health, SQLite creation and persistence across app restart" in (
        run / "logs/deployment.txt"
    ).read_text()
    assert all(path.read_bytes() == raw for path, raw in old_demo.items()
               if path.name != "README.md")
    assert (run / "configuration.json").read_bytes() == old_configuration
    assert len(list((run / "logs").glob("*-attempt-*.json"))) == 58
    assert all(path.read_bytes() == raw for path, raw in old_logs.items()
               if path.name != "delivery.diff")
    assert git(run / "demo", "remote") == "" and git(run / "demo", "status", "--porcelain") == ""


@pytest.mark.parametrize("protected", [
    "src/booking/api.py", "tests/test_bookings.py", "reports/quality.md", "SPEC.md",
])
def test_delivery_review_cannot_edit_source_tests_measured_quality_or_product_input(
    tmp_path, protected,
):
    settings = settings_with_keys()
    run = tmp_path / "run-01"
    config, patch_path = setup_delivery(run, settings)
    patch = deepcopy(DELIVERY["patch"])
    patch["edits"][0]["path"] = protected
    patch_path.write_text(json.dumps(patch))
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, NoModelCalls(), saver)
        before = graph.get_state(config)
        raw_diff = (run / "logs/delivery.diff").read_bytes()
        with pytest.raises(WorkflowError, match="Ugyldig"):
            stage_review_edits(graph, run, config, patch_path)
        after = graph.get_state(config)
        assert before.config == after.config and before.values == after.values
        assert (run / "logs/delivery.diff").read_bytes() == raw_diff
        assert not (run / "logs/review-edits-002.json").exists()


def test_reviewed_delivery_commands_links_pins_and_claims_match_received_project():
    edited = {item["path"]: item["content"] for item in DELIVERY["patch"]["edits"]}
    limits = {"README.md": 28, "Dockerfile": 16, "reports/analysis.md": 18,
              "docs/runbook.md": 20, "docs/deployment-checklist.md": 22}
    assert all(len(edited[name].splitlines()) <= maximum for name, maximum in limits.items())
    docker = edited["Dockerfile"]
    assert "adduser -D" not in docker and "groupadd --gid 10001 booking" in docker
    assert "useradd --uid 10001 --gid booking --no-create-home booking" in docker
    assert docker.index("mkdir -p /data") < docker.index("chown booking:booking /data")
    assert docker.index("chown booking:booking /data") < docker.index("USER booking")
    assert "ENV BOOKING_DB=/data/booking.db" in docker
    command = json.loads(next(line[4:] for line in docker.splitlines() if line.startswith("CMD ")))
    assert command == ["uvicorn", "booking.api:app", "--app-dir", "src", "--host", "0.0.0.0",
                       "--port", "8000"]
    requirements = (TOOLCHAIN / "template/requirements.txt").read_text()
    checklist = edited["docs/deployment-checklist.md"]
    for name, version in (("fastapi", "0.142.2"), ("pydantic", "2.13.5"), ("uvicorn", "0.54.0")):
        assert f"{name}=={version}" in requirements and version in checklist
    assert "sqlite3==" not in checklist and "127.0.0.1:8000:8000" in checklist
    assert "--entrypoint chown booking-api -R 10001:10001 /data" in checklist
    available = set(DELIVERY["artifacts"]) | {"requirements.txt", "requirements-dev.txt"}
    for name, content in edited.items():
        for link in re.findall(r"\]\(([^)]+)\)", content):
            if not link.startswith("http"):
                target = Path(name).parent / link
                parts = []
                for part in target.parts:
                    if part == "..":
                        parts.pop()
                    elif part != ".":
                        parts.append(part)
                assert "/".join(parts) in available
    analysis = edited["reports/analysis.md"]
    assert "21 tests" in analysis and "3079.924 s" in analysis
    assert "NOT been verified" in analysis and "assistant-authored review edits" in analysis


def test_delivery_manifest_has_exact_received_artifact_and_corrected_content_hashes():
    patch = DELIVERY["patch"]
    assert patch["stage"] == "delivery"
    assert patch["base_artifacts_sha256"] == {
        name: content_hash(text) for name, text in DELIVERY["artifacts"].items()
    }
    for edit in patch["edits"]:
        assert edit["before_sha256"] == content_hash(DELIVERY["artifacts"][edit["path"]])
        assert edit["after_sha256"] == content_hash(edit["content"])
