"""Received run-02 review, immutable model evidence and real isolated quality checks."""

import argparse
import json
from pathlib import Path

import yaml
from fastapi.openapi.models import OpenAPI
from langgraph.checkpoint.sqlite import SqliteSaver
from test_parts import settings_with_keys
from test_workflow import FixtureGenerator

from mh_toolchain import cli
from mh_toolchain.files import init_run
from mh_toolchain.graph import build_graph
from mh_toolchain.llm import EXPECTED_API

RECEIVED = json.loads((Path(__file__).parent / "fixtures/qwen_run02_review.json").read_text())


class ReviewProvider(FixtureGenerator):
    """No inference during amendments; delivery uses clearly identified test documents."""

    allow_delivery = False

    def file(self, role, path, task, context):
        assert self.allow_delivery, "Review unexpectedly requested model inference"
        assert path in {
            "reports/analysis.md", "README.md", "docs/runbook.md", "Dockerfile",
            "docs/deployment-checklist.md",
        }
        return super().file(role, path, task, context)


def test_received_run02_amendment_and_real_quality_preserve_original_evidence(
    tmp_path, monkeypatch, capsys,
):
    settings = settings_with_keys()
    assert settings.snapshot() == RECEIVED["configuration"]
    run = tmp_path / "run-02"
    init_run(run, settings.snapshot())
    for name, raw in RECEIVED["raw_logs"].items():
        (run / "logs" / name).write_bytes(raw.encode("utf-8"))
    patch_path = run.parent / "reviewed-run-02.json"
    patch_path.write_text(json.dumps(RECEIVED["patch"]))
    config = {"configurable": {"thread_id": "run-02"}, "max_concurrency": 2}
    provider = ReviewProvider()
    # Reconstruct a pause from actual artifacts; the user's checkpoint DB was not supplied.
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, provider, saver)
        graph.update_state(config, {
            "run_dir": str(run), "artifacts": RECEIVED["artifacts"], "round": 0,
            "status": "running",
        }, as_node="tester")
        result = graph.invoke(None, config)
        assert result["__interrupt__"][0].value["stage"] == "code"
        (run / "logs/code-round-0.diff").write_bytes(RECEIVED["original_diff"].encode("utf-8"))
        summary = cli.summarize(run, graph.get_state(config))
        assert summary["model_calls"] == 35 and summary["rejected_outputs"] == 7
        assert summary["model_seconds"] == 1089.987 and summary["contract_repairs"] == 1
    configuration_bytes = (run / "configuration.json").read_bytes()
    old_diff = (run / "logs/code-round-0.diff").read_bytes()
    raw_logs = {path: path.read_bytes() for path in (run / "logs").glob("*.json")}
    monkeypatch.setattr(cli, "run_path", lambda _name: run)
    monkeypatch.setattr(cli, "Generator", lambda _settings, _run: provider)
    args = argparse.Namespace(command="amend-review", id="run-02", patch=str(patch_path))
    assert cli.run_command(args, settings) == 0
    assert "needs_review" in capsys.readouterr().out
    assert not provider.calls
    assert not (run / "demo/src/booking/api.py").exists()
    assert not (run / "demo/tests/test_bookings.py").exists()
    assert not list((run / "logs").glob("pytest*.txt"))
    assert (run / "logs/review-before-edit-001.diff").read_bytes() == old_diff
    assert all(path.read_bytes() == raw for path, raw in raw_logs.items())
    record = json.loads((run / "logs/review-edits-001.json").read_text())
    assert record["checkpoint_updated"] and record["stage"] == "code"
    assert len(record["edits"]) == 8 and "not a local model response" in record["kind"]
    assert "awaiting user review" in record["approval"]
    assert cli.run_command(args, settings) == 0  # Idempotent; still awaiting approval.
    assert not provider.calls
    assert len(list((run / "logs").glob("review-edits-*.json"))) == 1
    provider.allow_delivery = True
    approve = argparse.Namespace(command="resume", id="run-02", decision="approve")
    # Simulated approval in an isolated test, followed by actual pytest/Ruff/mypy.
    assert cli.run_command(approve, settings) == 0
    summary = json.loads((run / "summary.json").read_text())
    assert summary["quality"]["passed"] and summary["quality"]["tests"] == 21
    assert summary["pending"][0]["stage"] == "delivery"
    assert summary["model_calls"] == 35 and summary["model_seconds"] == 1089.987
    assert summary["rejected_outputs"] == 7 and summary["contract_repairs"] == 1
    assert summary["review_edits"] == 1
    amended = {item["path"]: item["content"] for item in RECEIVED["patch"]["edits"]}
    for name, original in RECEIVED["artifacts"].items():
        assert (run / "demo" / name).read_text() == amended.get(name, original)
    assert len((run / "demo/src/booking/storage.py").read_text().splitlines()) == 76
    assert len(list((run / "logs").glob("*-attempt-*.json"))) == 35
    # Delivery documents here are test doubles; this checks the actual local persistence smoke.
    assert cli.run_command(approve, settings) == 0
    deployment = json.loads((run / "demo/reports/deployment.json").read_text())
    assert deployment["passed"] and deployment["container_build"] == "NOT_RUN"
    assert json.loads((run / "summary.json").read_text())["status"] == "passed"
    assert all(path.read_bytes() == raw for path, raw in raw_logs.items())
    assert (run / "configuration.json").read_bytes() == configuration_bytes


def test_run02_openapi_required_fields_constraints_and_exact_http_contract():
    edited = {item["path"]: item["content"] for item in RECEIVED["patch"]["edits"]}
    api = yaml.safe_load(edited["docs/arch/openapi.yaml"])
    OpenAPI.model_validate(api)
    assert set(api["paths"]) == set(EXPECTED_API)
    for path, methods in EXPECTED_API.items():
        assert set(api["paths"][path]) == set(methods)
        for method, codes in methods.items():
            assert set(api["paths"][path][method]["responses"]) == set(codes)

    def resolve(reference):
        value = api
        assert reference.startswith("#/")
        for name in reference[2:].split("/"):
            value = value[name]
        return value

    def inspect(value):
        if isinstance(value, dict):
            if "$ref" in value:
                resolve(value["$ref"])
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(api)
    schemas = api["components"]["schemas"]
    for name in ("RoomCreate", "Room", "BookingCreate", "Booking"):
        assert set(schemas[name]["required"]) == set(schemas[name]["properties"])
    for name in ("RoomCreate", "Room"):
        assert schemas[name]["properties"]["capacity"]["minimum"] == 1
    for name in ("BookingCreate", "Booking"):
        assert schemas[name]["properties"]["title"]["minLength"] == 1
        for field in ("start", "end"):
            assert schemas[name]["properties"][field]["format"] == "date-time"
    assert schemas["Error"]["properties"]["detail"]["type"] == "string"
    assert schemas["ValidationError"]["properties"]["detail"]["type"] == "array"
    for path in ("/rooms", "/bookings"):
        assert api["paths"][path]["post"]["requestBody"]["required"]
    for name, maximum in (("components.md", 30), ("openapi.yaml", 90), ("adr.md", 22),
                          ("deployment.md", 16)):
        assert len(edited[f"docs/arch/{name}"].splitlines()) <= maximum
    assert len(edited["tickets/TICKETS.md"].splitlines()) == 20
