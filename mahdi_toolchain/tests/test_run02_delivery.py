"""Actual run-02 delivery edits preserve Windows quality and approved source/tests."""

import argparse
import json
import re
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from test_delivery_edits import NoModelCalls
from test_parts import settings_with_keys

from mh_toolchain import cli
from mh_toolchain.config import TOOLCHAIN
from mh_toolchain.files import apply, commit, git, init_run
from mh_toolchain.graph import build_graph

RECEIVED = json.loads((Path(__file__).parent / "fixtures/qwen_run02_delivery.json").read_text())
DELIVERY_PATHS = {item["path"] for item in RECEIVED["patch"]["edits"]}


def test_actual_run02_delivery_review_cli_keeps_quality_and_runs_only_final_smoke(
    tmp_path, monkeypatch, capsys,
):
    settings = settings_with_keys()
    assert settings.snapshot() == RECEIVED["configuration"]
    run = tmp_path / "run-02"
    init_run(run, settings.snapshot())
    (run / "configuration.json").write_bytes(RECEIVED["configuration_raw"].encode("utf-8"))
    approved = {name: text for name, text in RECEIVED["artifacts"].items()
                if name not in DELIVERY_PATHS}
    apply(run, approved, "feat: previously approved run-02 source, tests and architecture")
    for name, raw in RECEIVED["raw_reports"].items():
        (run / "demo/reports" / name).write_bytes(raw.encode("utf-8"))
    commit(run / "demo", "test: received Windows quality evidence")
    for name, raw in RECEIVED["raw_logs"].items():
        (run / "logs" / name).write_bytes(raw.encode("utf-8"))
    patch = run.parent / "reviewed-run-02-delivery.json"
    patch.write_text(json.dumps(RECEIVED["patch"]))
    config = {"configurable": {"thread_id": "run-02"}, "max_concurrency": 2}
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, NoModelCalls(), saver)
        # The user's checkpoint DB was not uploaded; reconstruct only the received pause.
        graph.update_state(config, {
            "run_dir": str(run), "artifacts": RECEIVED["artifacts"], "round": 0,
            "quality": RECEIVED["quality"], "approved": True, "status": "running",
        }, as_node="deploy")
        result = graph.invoke(None, config)
        assert result["__interrupt__"][0].value["stage"] == "delivery"
        (run / "logs/delivery.diff").write_bytes(RECEIVED["original_delivery_diff"].encode("utf-8"))
    configuration = (run / "configuration.json").read_bytes()
    raw_logs = {p: p.read_bytes() for p in (run / "logs").iterdir()}
    raw_demo = {p: p.read_bytes() for p in (run / "demo").rglob("*")
                if p.is_file() and ".git" not in p.relative_to(run / "demo").parts}
    monkeypatch.setattr(cli, "run_path", lambda _name: run)
    monkeypatch.setattr(cli, "Generator", lambda _settings, _run: NoModelCalls())
    amend = argparse.Namespace(command="amend-review", id="run-02", patch=str(patch))
    assert cli.run_command(amend, settings) == 0
    assert "needs_review" in capsys.readouterr().out
    summary = json.loads((run / "summary.json").read_text())
    assert summary["quality"] == RECEIVED["quality"]
    assert summary["model_calls"] == 40 and summary["model_seconds"] == 1271.971
    assert summary["rejected_outputs"] == 7 and summary["contract_repairs"] == 1
    assert summary["review_edits"] == 2 and summary["next"] == ["review_delivery"]
    assert all(path.read_bytes() == raw for path, raw in raw_demo.items())
    assert not (run / "demo/Dockerfile").exists()
    assert not (run / "demo/reports/deployment.json").exists()
    assert (run / "logs/review-before-edit-002.diff").read_bytes() == raw_logs[
        run / "logs/delivery.diff"
    ]
    assert all(path.read_bytes() == raw for path, raw in raw_logs.items()
               if path.name != "delivery.diff")
    assert cli.run_command(amend, settings) == 0
    assert len(list((run / "logs").glob("review-edits-*.json"))) == 2
    # Simulated approval in an isolated test; actual persistence check, no model inference.
    approve = argparse.Namespace(command="resume", id="run-02", decision="approve")
    assert cli.run_command(approve, settings) == 0
    summary = json.loads((run / "summary.json").read_text())
    assert summary["status"] == "passed" and summary["quality"] == RECEIVED["quality"]
    assert summary["model_calls"] == 40 and summary["model_seconds"] == 1271.971
    assert summary["deployment_retries"] == 0
    deployment = json.loads((run / "demo/reports/deployment.json").read_text())
    assert deployment["passed"] and deployment["container_build"] == "NOT_RUN"
    # Applying the same text uses native line endings: CRLF on Windows, LF in this Linux test.
    assert all(path.read_bytes() == raw for path, raw in raw_demo.items()
               if path != run / "demo/reports/quality.md")
    assert (run / "demo/reports/quality.md").read_text() == RECEIVED["artifacts"][
        "reports/quality.md"
    ]
    assert all(path.read_bytes() == raw for path, raw in raw_logs.items()
               if path.name != "delivery.diff")
    assert (run / "configuration.json").read_bytes() == configuration
    assert git(run / "demo", "status", "--porcelain") == ""


def test_run02_delivery_links_pins_and_measured_claims_match_received_files():
    edited = {item["path"]: item["content"] for item in RECEIVED["patch"]["edits"]}
    requirements = (TOOLCHAIN / "template/requirements.txt").read_text()
    for pin in ("fastapi==0.142.2", "pydantic==2.13.5", "uvicorn==0.54.0"):
        assert pin in requirements and pin.split("==")[1] in edited["docs/deployment-checklist.md"]
    assert "sqlite3==" not in edited["docs/deployment-checklist.md"]
    assert "adduser -D" not in edited["Dockerfile"]
    assert "ENV BOOKING_DB=/data/booking.db" in edited["Dockerfile"]
    available = set(RECEIVED["artifacts"]) | {"requirements.txt", "requirements-dev.txt"}
    for name, content in edited.items():
        for link in re.findall(r"\]\(([^)]+)\)", content):
            if link.startswith("http"):
                continue
            parts = []
            for part in (Path(name).parent / link).parts:
                if part == "..":
                    parts.pop()
                elif part != ".":
                    parts.append(part)
            assert "/".join(parts) in available
    analysis = edited["reports/analysis.md"]
    assert "21 tests" in analysis and "1271.971 s" in analysis
    for check in RECEIVED["quality"]["checks"]:
        assert f"{check['seconds']:.3f} s" in analysis
    assert "NOT been verified" in analysis and "assistant-authored review edits" in analysis
