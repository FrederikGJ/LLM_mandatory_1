"""Actual Qwen review errors, recorded amendments and execution only after approval."""

import argparse
import ast
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.openapi.models import OpenAPI
from langgraph.checkpoint.sqlite import SqliteSaver
from test_parts import settings_with_keys
from test_workflow import FixtureGenerator

from mh_toolchain import cli
from mh_toolchain.config import WorkflowError
from mh_toolchain.files import init_run
from mh_toolchain.graph import build_graph
from mh_toolchain.llm import clean_file
from mh_toolchain.parts import BOOKING_TESTS, ROOM_TESTS, validate_part
from mh_toolchain.review_edits import content_hash, stage_review_edits

REVIEW = json.loads((Path(__file__).parent / "fixtures/qwen_run01_review.json").read_text())


class ReceivedArtifacts(FixtureGenerator):
    """Return received artifacts only; this test provider is never used by run.py."""

    def file(self, role, path, task, context):
        if path in REVIEW["artifacts"]:
            self.calls.append((role, path))
            return REVIEW["artifacts"][path]
        return super().file(role, path, task, context)


def setup_review(run, settings):
    init_run(run, settings.snapshot())
    for name, raw in REVIEW["attempt_logs"].items():
        (run / "logs" / name).write_bytes(raw.encode("utf-8"))
    patch = run.parent / "review-patch.json"
    patch.write_text(json.dumps(REVIEW["patch"]))
    return patch


def test_actual_review_amendment_cli_preserves_logs_and_passes_real_quality(
    tmp_path, monkeypatch, capsys,
):
    settings = settings_with_keys()
    run = tmp_path / "run-01"
    patch = setup_review(run, settings)
    before_config = (run / "configuration.json").read_bytes()
    config = {"configurable": {"thread_id": "run-01"}, "max_concurrency": 2}
    provider = ReceivedArtifacts()
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, provider, saver)
        result = graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
        assert result["__interrupt__"][0].value["stage"] == "code"
        summary = cli.summarize(run, graph.get_state(config))
        assert summary["model_calls"] == 53 and summary["rejected_outputs"] == 18
        assert summary["model_seconds"] == 2772.628
    old_diff = (run / "logs/code-round-0.diff").read_bytes()
    logs = {path: path.read_bytes() for path in (run / "logs").glob("*-attempt-*.json")}
    count = len(provider.calls)
    monkeypatch.setattr(cli, "run_path", lambda _name: run)
    monkeypatch.setattr(cli, "Generator", lambda _settings, _run: provider)
    args = argparse.Namespace(command="amend-review", id="run-01", patch=str(patch))
    assert cli.run_command(args, settings) == 0
    assert "needs_review" in capsys.readouterr().out
    assert len(provider.calls) == count
    assert not (run / "demo/src/booking/api.py").exists()
    assert not (run / "demo/tests/test_rooms.py").exists()
    assert not list((run / "logs").glob("pytest*.txt"))
    assert (run / "logs/review-before-edit-001.diff").read_bytes() == old_diff
    record = json.loads((run / "logs/review-edits-001.json").read_text())
    assert record["checkpoint_updated"] and len(record["edits"]) == 7
    assert "not a local model response" in record["kind"]
    assert "awaiting user review" in record["approval"]
    assert all(path.read_bytes() == raw for path, raw in logs.items())
    assert (run / "configuration.json").read_bytes() == before_config
    assert cli.run_command(args, settings) == 0  # Idempotent, still awaiting approval.
    assert len(list((run / "logs").glob("review-edits-*.json"))) == 1
    assert len(provider.calls) == count
    resume = argparse.Namespace(command="resume", id="run-01", decision="approve")
    assert cli.run_command(resume, settings) == 0
    summary = json.loads((run / "summary.json").read_text())
    assert summary["quality"]["passed"] and summary["quality"]["tests"] == 21
    assert summary["model_calls"] == 53 and summary["model_seconds"] == 2772.628
    assert summary["review_edits"] == 1
    assert summary["pending"][0]["stage"] == "delivery"
    assert cli.run_command(resume, settings) == 0
    assert json.loads((run / "summary.json").read_text())["status"] == "passed"
    assert json.loads((run / "demo/reports/deployment.json").read_text())["passed"]
    assert all(path.read_bytes() == raw for path, raw in logs.items())
    assert (run / "configuration.json").read_bytes() == before_config
    for name in ("src/booking/api.py", "src/booking/models.py", "src/booking/storage.py"):
        assert (run / "demo" / name).read_text() == REVIEW["artifacts"][name]
    assert max(len((run / "demo" / item["path"]).read_text().splitlines())
               for item in REVIEW["patch"]["edits"] if item["path"].endswith(".py")) == 72


@pytest.mark.parametrize("problem", ["edited_source", "wrong_config", "readonly_file", "bad_test"])
def test_review_patch_rejection_does_not_modify_checkpoint_or_files(tmp_path, problem):
    settings = settings_with_keys()
    run = tmp_path / "run-01"
    patch_path = setup_review(run, settings)
    config = {"configurable": {"thread_id": "run-01"}, "max_concurrency": 2}
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, ReceivedArtifacts(), saver)
        graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
        patch = deepcopy(REVIEW["patch"])
        if problem == "edited_source":
            changed = REVIEW["artifacts"]["src/booking/api.py"] + "# unrelated user's edit\n"
            graph.update_state(config, {"artifacts": {"src/booking/api.py": changed}},
                               as_node="tester")
            graph.invoke(None, config)
        elif problem == "wrong_config":
            patch["configuration_sha256"] = "0" * 64
        elif problem == "readonly_file":
            patch["edits"][0]["path"] = "SPEC.md"
        else:
            item = patch["edits"][0]
            item["content"] = (
                "def test_bad(client):\n    assert client.post('/bad').status_code == 201\n"
            )
            item["after_sha256"] = content_hash(item["content"])
        patch_path.write_text(json.dumps(patch))
        before = graph.get_state(config)
        diff = (run / "logs/code-round-0.diff").read_bytes()
        with pytest.raises(WorkflowError):
            stage_review_edits(graph, run, config, patch_path)
        after = graph.get_state(config)
        assert before.config == after.config and before.values == after.values
        assert (run / "logs/code-round-0.diff").read_bytes() == diff
        assert not list((run / "logs").glob("review-edits-*.json"))
        assert not (run / "demo/src/booking/api.py").exists()


@pytest.mark.parametrize("stage, round_number, quality", [
    ("code", 0, {"passed": True}), ("code", 1, {"passed": False}),
])
def test_review_changes_cannot_mutate_tests_after_quality(stage, round_number, quality, tmp_path):
    snapshot = SimpleNamespace(
        values={"round": round_number, "quality": quality},
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"stage": stage})])],
    )
    graph = SimpleNamespace(get_state=lambda _config: snapshot)
    with pytest.raises(WorkflowError, match="før første kodegodkendelse"):
        stage_review_edits(graph, tmp_path, {}, tmp_path / "missing.json")


@pytest.mark.parametrize("path, part, expected", [
    ("tests/test_bookings.py", BOOKING_TESTS[0], "POST must use"),
    ("tests/test_bookings.py", BOOKING_TESTS[1], "HTTP status is not a JSON field"),
    ("tests/test_rooms.py", ROOM_TESTS[0], "F841"),
])
def test_actual_bad_test_functions_are_rejected_statically(path, part, expected):
    source = REVIEW["artifacts"][path]
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == part.name)
    raw = ast.get_source_segment(source, function)
    with pytest.raises(WorkflowError, match=expected):
        validate_part(raw, part, False, True)
    with pytest.raises(WorkflowError):
        clean_file(source, path)


def test_corrected_architecture_has_valid_openapi_and_complete_required_fields():
    edited = {item["path"]: item["content"] for item in REVIEW["patch"]["edits"]}
    old = yaml.safe_load(REVIEW["artifacts"]["docs/arch/openapi.yaml"])
    assert "description" not in old["paths"]["/health"]["get"]["responses"]["200"]
    api = yaml.safe_load(edited["docs/arch/openapi.yaml"])
    OpenAPI.model_validate(api)
    def resolve(reference):
        value = api
        for segment in reference.removeprefix("#/").split("/"):
            value = value[segment]
        return value

    def inspect(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/")
                resolve(value["$ref"])
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(api)
    for operations in api["paths"].values():
        for operation in operations.values():
            for response in operation["responses"].values():
                if "$ref" in response:
                    response = resolve(response["$ref"])
                assert isinstance(response["description"], str) and response["description"]
    assert len(api["paths"]) == 5
    assert sum(len(operations) for operations in api["paths"].values()) == 6
    for name in ("RoomCreate", "Room", "BookingCreate", "Booking"):
        schema = api["components"]["schemas"][name]
        assert set(schema["required"]) == set(schema["properties"])
    assert api["components"]["schemas"]["Booking"]["properties"]["end"]["format"] == "date-time"
    assert api["components"]["schemas"]["Error"]["required"] == ["detail"]
    assert api["paths"]["/bookings"]["post"]["requestBody"]["required"]
    for name, maximum in (("components.md", 30), ("deployment.md", 16), ("adr.md", 22),
                          ("openapi.yaml", 90)):
        assert len(edited[f"docs/arch/{name}"].splitlines()) <= maximum
    assert len(edited["tickets/TICKETS.md"].splitlines()) == 20
