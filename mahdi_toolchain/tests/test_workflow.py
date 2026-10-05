import json
from pathlib import Path

import pytest
import yaml
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from mh_toolchain.config import TOOLCHAIN, WorkflowError, load_settings
from mh_toolchain.files import git, init_run, safe_path
from mh_toolchain.graph import build_graph
from mh_toolchain.llm import EXPECTED_API, clean_file


class FixtureGenerator:
    """Test double only; this class is never imported by run.py or normal model runs."""

    def __init__(self, broken=False):
        self.calls = []
        self.broken = broken

    def close(self):
        pass

    def file(self, role, path, task, context):
        self.calls.append((role, path))
        if path.startswith("src/"):
            text = (Path(__file__).parent / "fixtures" / Path(path).name).read_text()
            if self.broken and path.endswith("api.py"):
                text = text.replace('return {"status": "ok"}', 'return {"status": "broken"}')
            return clean_file(text, path)
        if path == "docs/arch/openapi.yaml":
            text = yaml.safe_dump(
                {
                    "openapi": "3.1.0",
                    "info": {"title": "Test", "version": "1"},
                    "paths": {
                        url: {
                            method: {"responses": {code: {"description": "test"} for code in codes}}
                            for method, codes in methods.items()
                        }
                        for url, methods in EXPECTED_API.items()
                    },
                    "components": {
                        "schemas": {
                            name: {"type": "object"}
                            for name in ["RoomCreate", "Room", "BookingCreate", "Booking"]
                        }
                    },
                }
            )
            return clean_file(text, path)
        if path.startswith("tests/"):
            return (
                "from fastapi.testclient import TestClient\n\n"
                "from booking.api import create_app\n\n\n"
                "def test_generated_health():\n"
                '    assert TestClient(create_app(":memory:")).get("/health").status_code == 200\n'
            )
        if path == "tickets/TICKETS.md":
            return (
                "# Tickets\n" + "\n".join(f"## T-0{i}: scoped fixture" for i in range(1, 5)) + "\n"
            )
        if path == "Dockerfile":
            return (
                "FROM python:3.12-slim\nENV BOOKING_DB=/data/booking.db\nUSER 1000\n"
                'CMD ["uvicorn", "booking.api:app"]\n'
            )
        return "# Test fixture document\n- Test data, not a measured model output.\n"


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.delenv("ENDPOINTS_FILE", raising=False)
    monkeypatch.delenv("LLM_BACKEND_ENV", raising=False)
    monkeypatch.delenv("WORKFLOW_FILE", raising=False)
    return load_settings()


def test_full_graph_reviews_resume_real_quality_and_deploy(tmp_path, settings):
    run = tmp_path / "demo-run"
    init_run(run, settings.snapshot())
    provider = FixtureGenerator()
    config = {"configurable": {"thread_id": "test"}, "max_concurrency": 2}
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, provider, saver)
        result = graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
        assert result["__interrupt__"][0].value["stage"] == "code"
        assert not (run / "demo/src/booking/api.py").exists()
        assert {"coder_1", "coder_2"} <= {role for role, _ in provider.calls}
        diff = Path(result["__interrupt__"][0].value["diff"]).read_text()
        assert "src/booking/models.py" in diff and "src/booking/api.py" in diff
    count = len(provider.calls)
    # Reopen SQLite to simulate stopping Python and resuming in a new process.
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, provider, saver)
        result = graph.invoke(Command(resume=True), config)
        assert result["quality"]["passed"], result["quality"]
        assert result["quality"]["tests"] >= 15
        assert result["__interrupt__"][0].value["stage"] == "delivery"
        assert len(provider.calls) > count
        assert sum(role == "architect" for role, _ in provider.calls) == 4
        result = graph.invoke(Command(resume=True), config)
        assert result["status"] == "passed"
    deploy = json.loads((run / "demo/reports/deployment.json").read_text())
    assert deploy["passed"] and deploy["container_build"] == "NOT_RUN"
    assert git(run / "demo", "remote") == ""
    assert git(run / "demo", "status", "--porcelain") == ""


def test_rejection_never_executes_generated_source(tmp_path, settings):
    run = tmp_path / "rejected"
    init_run(run, settings.snapshot())
    config = {"configurable": {"thread_id": "reject"}}
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, FixtureGenerator(), saver)
        graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
        result = graph.invoke(Command(resume=False), config)
        assert result["status"] == "rejected"
        assert not (run / "demo/src/booking/api.py").exists()
        assert not list((run / "logs").glob("pytest*.txt"))


def test_tester_failure_continues_cached_tests_then_runs_real_quality_and_deployment(tmp_path):
    import httpx
    from test_parts import Responses, settings_with_keys

    from mh_toolchain.llm import Generator

    settings = settings_with_keys()
    snapshot = settings.snapshot()
    run = tmp_path / "tester-checkpoint"
    init_run(run, snapshot)
    config = {"configurable": {"thread_id": "tester-restart"}, "max_concurrency": 2}
    replies = Responses()
    selected = ("test_bookings", "test_invalid_booking_and_missing_room")
    correct = replies.parts[selected]
    replies.parts[selected] = (
        "def test_invalid_booking_and_missing_room(client):\n    assert True\n"
    )

    class Provider(FixtureGenerator):
        def __init__(self, client):
            super().__init__()
            self.client = client

        def file(self, role, path, task, context):
            if path.startswith("tests/"):
                self.calls.append((role, path))
                return self.client.file(role, path, task, context)
            return super().file(role, path, task, context)

    first = Generator(settings, run, httpx.MockTransport(replies))
    try:
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, Provider(first), saver)
            with pytest.raises(WorkflowError, match="real test"):
                graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
            assert "src/booking/api.py" in graph.get_state(config).values["artifacts"]
            assert not (run / "demo/src/booking/api.py").exists()
            assert not list((run / "logs").glob("pytest*.txt"))
    finally:
        first.close()
    saved = {path: path.read_bytes() for path in (run / "logs").glob("*-attempt-*.json")}
    before = len(replies.calls)
    assert before == 6 + settings.workflow["format_retries"] + 1
    replies.parts[selected] = correct
    fresh = Generator(settings, run, httpx.MockTransport(replies))
    current = Provider(fresh)
    try:
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, current, saver)
            result = graph.invoke(None, config)
            assert result["__interrupt__"][0].value["stage"] == "code"
            assert len(replies.calls) == before + 2
            assert replies.calls[-2:] == [
                "fragments/tests/test_bookings/test_invalid_booking_and_missing_room.py",
                "fragments/tests/test_bookings/test_cancel_booking.py",
            ]
            assert not {"architect", "tech_lead", "coder_1", "coder_2"} & {
                role for role, _ in current.calls
            }
            assert not (run / "demo/src/booking/api.py").exists()
            assert not (run / "demo/tests/test_rooms.py").exists()
            assert all(path.read_bytes() == raw for path, raw in saved.items())
            repaired = [json.loads(path.read_text()) for path in (run / "logs").glob(
                "tester-test_invalid_booking_and_missing_room-*-attempt-*.json"
            ) if json.loads(path.read_text())["status"] == "accepted"]
            assert len(repaired) == 1
            prompt = repaired[0]["messages"][-1]["content"]
            assert "Read-only rejected test:\n" in prompt and "assert True" in prompt
            assert "CURRENT TEST FUNCTION TASK:" in prompt
            # Simulated user approval; the actual application/pytest/Ruff/mypy run below.
            result = graph.invoke(Command(resume=True), config)
            assert result["quality"]["passed"], result["quality"]
            assert result["quality"]["tests"] == 21
            assert result["__interrupt__"][0].value["stage"] == "delivery"
            result = graph.invoke(Command(resume=True), config)
            assert result["status"] == "passed"
        assert all(path.read_bytes() == raw for path, raw in saved.items())
        assert settings.snapshot() == snapshot
        assert json.loads((run / "configuration.json").read_text()) == snapshot
        assert json.loads((run / "demo/reports/deployment.json").read_text())["passed"]
    finally:
        fresh.close()


def test_coder_failure_continues_without_regenerating_architecture_or_tickets(tmp_path, settings):
    class FailingCoder(FixtureGenerator):
        def file(self, role, path, task, context):
            if role == "coder_1" and path == "src/booking/models.py":
                raise WorkflowError("Missing required top-level definitions: Booking")
            return super().file(role, path, task, context)

    run = tmp_path / "model-failure"
    original = settings.snapshot()
    init_run(run, original)
    config = {"configurable": {"thread_id": "model-failure"}, "max_concurrency": 2}
    before = FailingCoder()
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, before, saver)
        with pytest.raises(WorkflowError, match="Booking"):
            graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
        state = graph.get_state(config).values
        assert "docs/arch/openapi.yaml" in state["artifacts"]
        assert "tickets/TICKETS.md" in state["artifacts"]
    after = FixtureGenerator()
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, after, saver)
        result = graph.invoke(None, config)
        assert result["__interrupt__"][0].value["stage"] == "code"
    assert not {"architect", "tech_lead"} & {role for role, _ in after.calls}
    assert ("coder_1", "src/booking/models.py") in after.calls
    assert settings.snapshot() == original
    assert not (run / "demo/src/booking/models.py").exists()


@pytest.mark.parametrize("fail_handler", [False, True])
def test_continue_revalidates_an_old_successful_send_result_and_passes_real_quality(
    tmp_path, fail_handler,
):
    import httpx
    from test_parts import Responses, settings_with_keys

    from mh_toolchain.llm import Generator, unfence

    settings = settings_with_keys()
    run = tmp_path / "old-api-checkpoint"
    init_run(run, settings.snapshot())
    config = {"configurable": {"thread_id": "old-api"}, "max_concurrency": 2}
    replies = Responses(legacy_api=True)
    old_client = Generator(settings, run, httpx.MockTransport(replies))

    class Legacy(FixtureGenerator):
        def file(self, role, path, task, context):
            if role == "coder_1" and path.endswith("storage.py"):
                raise WorkflowError("Legacy storage output exceeds 79 lines")
            if role == "coder_2":
                # Reproduce the old parser's accepted real Qwen API response.
                return old_client.file(role, path, task, context,
                                       validator=lambda raw, _path: unfence(raw) + "\n")
            if role == "coder_1":
                return old_client.file(role, path, task, context)
            return super().file(role, path, task, context)

    legacy = Legacy()
    try:
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, legacy, saver)
            with pytest.raises(WorkflowError, match="Legacy storage"):
                graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
    finally:
        old_client.close()
    old_logs = {path: path.read_bytes() for path in (run / "logs").glob("*-attempt-*.json")}
    replies.legacy_api = False
    correct_handler = replies.parts["api", "create_room"]
    # Synthetic reply with the exact reported undefined name; actual body is unseen.
    bad_handler = (
        "def create_room(data: RoomCreate) -> Room:\n"
        "    try:\n        return storage.create_room(data)\n"
        "    except ValidationError as error:\n        raise error\n"
    )
    if fail_handler:
        replies.parts["api", "create_room"] = bad_handler
    fresh = Generator(settings, run, httpx.MockTransport(replies))

    class Current(FixtureGenerator):
        def file(self, role, path, task, context):
            self.calls.append((role, path))
            if role in {"coder_1", "coder_2"}:
                return fresh.file(role, path, task, context)
            return super().file(role, path, task, context)

    current = Current()
    try:
        partial = {}
        if fail_handler:
            with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
                graph = build_graph(settings, current, saver)
                with pytest.raises(WorkflowError, match="Undefined name `ValidationError`"):
                    graph.invoke(None, config)
            partial = {path: path.read_bytes() for path in (run / "logs").glob(
                "*-attempt-*.json"
            )}
            assert [path for path in replies.calls if path.startswith("fragments/api/")] == [
                "fragments/api/missing.py", "fragments/api/conflict.py", "fragments/api/health.py",
                "fragments/api/create_room.py", "fragments/api/create_room.py",
            ]
            assert not (run / "demo/src/booking/api.py").exists()
            fresh.close()
            replies.parts["api", "create_room"] = correct_handler
            fresh = Generator(settings, run, httpx.MockTransport(replies))
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, current, saver)
            result = graph.invoke(None, config)
            assert result["__interrupt__"][0].value["stage"] == "code"
            assert not (run / "demo/src/booking/api.py").exists()
            assert not (run / "demo/src/booking/storage.py").exists()
            assert not {"architect", "tech_lead"} & {role for role, _ in current.calls}
            assert replies.calls.count("src/booking/models.py") == 1
            assert replies.calls.count("src/booking/api.py") == 1  # only the old call
            assert len([path for path in replies.calls if path.startswith("fragments/")]) == (
                17 if fail_handler else 15
            )
            assert all(path.read_bytes() == raw for path, raw in old_logs.items())
            assert all(path.read_bytes() == raw for path, raw in partial.items())
            if fail_handler:
                assert all(replies.calls.count(f"fragments/api/{name}.py") == 1
                           for name in ("missing", "conflict", "health"))
                repaired = [json.loads(path.read_text()) for path in (run / "logs").glob(
                    "coder_2-create_room-*-attempt-*.json"
                ) if json.loads(path.read_text())["status"] == "accepted"]
                assert len(repaired) == 1
                prompt = repaired[0]["messages"][-1]["content"]
                assert "Read-only rejected function:\n" + bad_handler.rstrip() in prompt
                assert "CURRENT API FUNCTION TASK:" in prompt
                assert "Do not add try/except" in prompt
            assert list((run / "logs").glob("coder_2-api-*.cache-invalidated.json"))
            result = graph.invoke(Command(resume=True), config)
            assert result["quality"]["passed"], result["quality"]
            assert result["quality"]["tests"] == 15
            assert result["__interrupt__"][0].value["stage"] == "delivery"
            result = graph.invoke(Command(resume=True), config)
            assert result["status"] == "passed"
        assert settings.snapshot() == json.loads((run / "configuration.json").read_text())
    finally:
        fresh.close()


def test_actual_assembly_failure_continues_and_passes_quality_after_targeted_regeneration(tmp_path):
    import httpx
    from test_parts import (
        ACTUAL_ASSEMBLY,
        Responses,
        actual_cache,
        seed_actual_logs,
        settings_with_keys,
    )

    from mh_toolchain.llm import Generator

    settings = settings_with_keys()
    assert settings.snapshot() == ACTUAL_ASSEMBLY["configuration"]
    run = tmp_path / "actual-run01-assembly"
    init_run(run, settings.snapshot())
    original_logs = seed_actual_logs(run)
    config = {"configurable": {"thread_id": "actual-run01"}, "max_concurrency": 2}

    class OldResult(FixtureGenerator):
        def file(self, role, path, task, context):
            if path == "src/booking/storage.py":
                raise WorkflowError("SPEC kræver færre end 80 linjer (86 linjer; 73 ikke-tomme)")
            if path == "src/booking/models.py":
                return clean_file(actual_cache("models")[1]["content"], path)
            for name, raw in ACTUAL_ASSEMBLY["logs"].items():
                record = json.loads(raw)
                if record.get("role") == role and record.get("path") == path and (
                    record.get("status") == "accepted"
                ):
                    cached = ACTUAL_ASSEMBLY["logs"][name.rsplit("-attempt-", 1)[0] + ".cache.json"]
                    return json.loads(cached)["content"]
            raise AssertionError((role, path))

    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, OldResult(), saver)
        with pytest.raises(WorkflowError, match="86 linjer; 73 ikke-tomme"):
            graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
    replies = Responses()
    client = Generator(settings, run, httpx.MockTransport(replies))

    class Current(FixtureGenerator):
        def file(self, role, path, task, context):
            if role in {"coder_1", "coder_2"}:
                self.calls.append((role, path))
                return client.file(role, path, task, context)
            return super().file(role, path, task, context)

    current = Current()
    try:
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, current, saver)
            result = graph.invoke(None, config)
            assert result["__interrupt__"][0].value["stage"] == "code"
            assert not {"architect", "tech_lead"} & {role for role, _ in current.calls}
            assert [path for path in replies.calls if path.startswith("fragments/storage/")] == [
                "fragments/storage/create_room.py", "fragments/storage/list_bookings.py",
            ]
            assert len(replies.calls) == 10  # Two storage corrections and eight API functions.
            assert "src/booking/models.py" not in replies.calls
            assert all(path.read_bytes() == raw for path, raw in original_logs.items()
                       if "-attempt-" in path.name)
            assert not (run / "demo/src/booking/storage.py").exists()
            storage = result["artifacts"]["src/booking/storage.py"]
            assert len(storage.splitlines()) < 80
            result = graph.invoke(Command(resume=True), config)
            assert result["quality"]["passed"], result["quality"]
            assert result["quality"]["tests"] == 15
            assert result["__interrupt__"][0].value["stage"] == "delivery"
            result = graph.invoke(Command(resume=True), config)
            assert result["status"] == "passed"
        assert settings.snapshot() == ACTUAL_ASSEMBLY["configuration"]
    finally:
        client.close()


def test_continue_recovers_sql_function_rejected_by_v4_without_a_new_initializer_call(tmp_path):
    import hashlib

    import httpx
    from test_parts import LONG_INITIALIZER, Responses, settings_with_keys

    from mh_toolchain.llm import Generator, unfence

    settings = settings_with_keys()
    original_configuration = settings.snapshot()
    run = tmp_path / "long-sql-checkpoint"
    init_run(run, original_configuration)
    config = {"configurable": {"thread_id": "long-sql"}, "max_concurrency": 2}
    replies = Responses()
    replies.parts["storage", "__init__"] = LONG_INITIALIZER

    class OldLimit(Generator):
        def file(self, role, path, task, context, *, validator=None):
            if path == "fragments/storage/__init__.py":
                current_validator = validator

                def reject_long_function(raw, target):
                    if len(unfence(raw).splitlines()) > 10:
                        raise WorkflowError(
                            "Function __init__ must use at most 10 lines, header included."
                        )
                    return current_validator(raw, target)

                validator = reject_long_function
            return super().file(role, path, task, context, validator=validator)

    old_client = OldLimit(settings, run, httpx.MockTransport(replies))

    class Provider(FixtureGenerator):
        def __init__(self, client):
            super().__init__()
            self.client = client

        def file(self, role, path, task, context):
            if role in {"coder_1", "coder_2"}:
                self.calls.append((role, path))
                return self.client.file(role, path, task, context)
            return super().file(role, path, task, context)

    try:
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, Provider(old_client), saver)
            with pytest.raises(WorkflowError, match="Function __init__ must use at most 10"):
                graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
    finally:
        old_client.close()
    original_logs = {path: path.read_bytes() for path in (run / "logs").glob("*-attempt-*.json")}
    initializer_calls = replies.calls.count("fragments/storage/__init__.py")
    assert initializer_calls == settings.workflow["format_retries"] + 1
    fresh = Generator(settings, run, httpx.MockTransport(replies))
    current = Provider(fresh)
    try:
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, current, saver)
            result = graph.invoke(None, config)
            assert result["__interrupt__"][0].value["stage"] == "code"
            assert replies.calls.count("fragments/storage/__init__.py") == initializer_calls
            assert replies.calls.count("src/booking/models.py") == 1
            assert not {"architect", "tech_lead"} & {role for role, _ in current.calls}
            assert all(path.read_bytes() == raw for path, raw in original_logs.items())
            recovered = json.loads(next(
                (run / "logs").glob("coder_1-__init__-*.recovered.json")
            ).read_text(encoding="utf-8"))
            provenance = recovered["recovery"]
            source = run / "logs" / provenance["source_attempt"]
            assert provenance["source_sha256"] == hashlib.sha256(original_logs[source]).hexdigest()
            assert provenance["previous_status"] == "rejected"
            assert "at most 10 lines" in provenance["previous_error"]
            assert provenance["validated_lines"] > 10
            for path in ("src/booking/models.py", "src/booking/storage.py", "src/booking/api.py"):
                assert len(result["artifacts"][path].splitlines()) < 80
            assert not (run / "demo/src/booking/storage.py").exists()
            result = graph.invoke(Command(resume=True), config)
            assert result["quality"]["passed"], result["quality"]
            assert result["quality"]["tests"] == 15
            assert result["__interrupt__"][0].value["stage"] == "delivery"
            result = graph.invoke(Command(resume=True), config)
            assert result["status"] == "passed"
        assert settings.snapshot() == original_configuration
        assert replies.calls.count("fragments/storage/__init__.py") == initializer_calls
    finally:
        fresh.close()


def test_failed_quality_repairs_sources_only_and_stops_at_limit(tmp_path, settings):
    run = tmp_path / "failure"
    init_run(run, settings.snapshot())
    provider = FixtureGenerator(broken=True)
    config = {"configurable": {"thread_id": "fail"}}
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, provider, saver)
        graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
        result = graph.invoke(Command(resume=True), config)
        assert result["round"] == 1
        assert result["__interrupt__"][0].value["stage"] == "code"
        assert sum(role == "tester" for role, _ in provider.calls) == 2
        result = graph.invoke(Command(resume=True), config)
        assert not result["quality"]["passed"]
        assert result["__interrupt__"][0].value["stage"] == "delivery"
        result = graph.invoke(Command(resume=True), config)
        assert result["status"] == "failed"
    assert sum(role == "coder_2" for role, _ in provider.calls) == 2


@pytest.mark.parametrize("name", ["../secret", "/tmp/secret", "C:\\secret", ".git/config"])
def test_artifacts_cannot_escape_demo(tmp_path, name):
    with pytest.raises(WorkflowError):
        safe_path(tmp_path, name)


def test_routing_requires_two_distinct_endpoints(tmp_path, monkeypatch):
    config = yaml.safe_load((TOOLCHAIN.parent / "llm_backend/config/endpoints.yaml").read_text())
    config["endpoints"]["llm-b"]["base_url"] = config["endpoints"]["llm-a"]["base_url"]
    path = tmp_path / "endpoints.yaml"
    path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv("ENDPOINTS_FILE", str(path))
    with pytest.raises(WorkflowError, match="HR-01"):
        load_settings()


def test_cli_run_status_resume_and_compare_persisted_runs(tmp_path, monkeypatch):
    import sys

    from mh_toolchain import cli

    provider = FixtureGenerator()
    monkeypatch.setattr(cli, "TOOLCHAIN", tmp_path)
    monkeypatch.setattr(cli, "Generator", lambda settings, run: provider)
    monkeypatch.setattr(cli, "check_endpoints", lambda settings: [{"test_double": True}])
    for identifier in ("test-a", "test-b"):
        for command in (
            ["run", "--id", identifier],
            ["status", "--id", identifier],
            ["resume", "--id", identifier, "--decision", "approve"],
            ["resume", "--id", identifier, "--decision", "approve"],
        ):
            monkeypatch.setattr(sys, "argv", ["run.py", *command])
            assert cli.main() == 0
        summary = json.loads((tmp_path / "runs" / identifier / "summary.json").read_text())
        assert summary["status"] == "passed" and summary["quality"]["tests"] == 15
    monkeypatch.setattr(sys, "argv", ["run.py", "compare", "--a", "test-a", "--b", "test-b"])
    assert cli.main() == 0
    assert (tmp_path / "runs/comparison-test-a-test-b.md").exists()
