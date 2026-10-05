"""The actual repeated run-02 model body, targeted retries and saved Send-task recovery."""

import ast
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import ValidationError
from test_llm import completion
from test_parts import Responses, settings_with_keys
from test_workflow import FixtureGenerator

from mh_toolchain.cli import summarize
from mh_toolchain.config import WorkflowError
from mh_toolchain.contract_repairs import BOOKING_CLASS, repair_models_contract
from mh_toolchain.files import init_run
from mh_toolchain.graph import build_graph
from mh_toolchain.llm import Generator, booking_completion_source, clean_file, function_retry_prompt

FIXTURES = Path(__file__).parent / "fixtures"
# Body pasted from attempts 1 and 2, fb8dab38be28ca73c4b5. JSON envelopes/times are not received.
REJECTED = (FIXTURES / "run02_rejected_models.py").read_text()


def test_received_combined_models_error_gets_both_edits_without_automatic_code_insertion():
    with pytest.raises(WorkflowError) as caught:
        clean_file(REJECTED, "src/booking/models.py")
    assert "Missing required top-level definitions: Booking" in str(caught.value)
    assert "validate_start_end: model_validator" in str(caught.value)
    assert booking_completion_source(REJECTED, "src/booking/models.py") is None
    prompt = function_retry_prompt("src/booking/models.py", str(caught.value), REJECTED, "base")
    assert REJECTED.strip() in prompt
    assert prompt.index("CURRENT MODELS REPAIR TASK") > prompt.index(REJECTED.strip())
    assert 'def validate_start_end(self) -> "BookingCreate":' in prompt
    assert "FOURTH top-level class Booking(BookingCreate)" in prompt
    assert function_retry_prompt("src/booking/models.py", "", "", "base") == "base"


def test_failed_models_continue_repairs_saved_body_without_model_call_or_losing_api(tmp_path):
    settings = settings_with_keys()
    run = tmp_path / "run-02"
    init_run(run, settings.snapshot())
    saved_config = (run / "configuration.json").read_bytes()
    config = {"configurable": {"thread_id": "run-02"}, "max_concurrency": 2}
    requests = []

    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.port == 8082
        if len(requests) > 1:
            prompt = body["messages"][-1]["content"]
            assert "CURRENT MODELS REPAIR TASK" in prompt
            assert REJECTED.strip() in prompt
            assert "Remove cls completely" in prompt and "FOURTH top-level class" in prompt
            assert "accepting only self; never cls" in body["messages"][0]["content"]
        # The mock ALWAYS returns the actual rejected body, even after targeted feedback.
        return completion(request, REJECTED)

    class LegacyGenerator(Generator):
        def repair_contract(self, *args, **kwargs):
            # Reproduce v15's two rejected attempts to create a pre-fix checkpoint.
            return None

    class Provider(FixtureGenerator):
        def __init__(self, client, resumed=False):
            super().__init__()
            self.client, self.resumed = client, resumed

        def file(self, role, path, task, context):
            if self.resumed:
                assert role not in {"architect", "tech_lead", "coder_2"}
            if path == "src/booking/models.py":
                self.calls.append((role, path))
                return self.client.file(role, path, task, context)
            return super().file(role, path, task, context)

    client = LegacyGenerator(settings, run, httpx.MockTransport(respond))
    try:
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, Provider(client), saver)
            with pytest.raises(WorkflowError, match="Missing required top-level definitions"):
                graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
            tasks = graph.get_state(config).tasks
            finished = [task.result for task in tasks if task.result and not task.error]
            assert any("src/booking/api.py" in result.get("artifacts", {}) for result in finished)
    finally:
        client.close()
    old_logs = {path: path.read_bytes() for path in (run / "logs").glob("*-attempt-*.json")}
    assert len(requests) == 2 and len(old_logs) == 2
    assert not (run / "demo/src/booking/models.py").exists()
    fresh = Generator(settings, run, httpx.MockTransport(respond))
    try:
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, Provider(fresh, resumed=True), saver)
            result = graph.invoke(None, config)
            assert result["__interrupt__"][0].value["stage"] == "code"
            assert len(requests) == 2  # No request on resume; recover the saved model body.
            assert all(path.read_bytes() == raw for path, raw in old_logs.items())
            assert (run / "configuration.json").read_bytes() == saved_config
            assert not (run / "demo/src/booking/models.py").exists()
            assert not list((run / "logs").glob("pytest*.txt"))
            output = result["artifacts"]["src/booking/models.py"]
            repaired = repair_models_contract(REJECTED, "src/booking/models.py")
            assert repaired is not None and output == repaired[0]
            classes = {node.name for node in ast.parse(output).body
                       if isinstance(node, ast.ClassDef)}
            assert classes == {
                "RoomCreate", "Room", "BookingCreate", "Booking",
            }
            namespace = {}
            exec(output, namespace)  # Reviewed, fixed regression sample; no arbitrary saved code.
            payload = dict(room_id=1, title="Test", start="2026-01-01T09:00:00",
                           end="2026-01-01T10:00:00")
            assert namespace["Booking"](id=1, **payload).id == 1
            with pytest.raises(ValidationError):
                namespace["BookingCreate"](**{**payload, "end": payload["start"]})
            audit = json.loads(next((run / "logs").glob("*.contract-repair-*.json")).read_text())
            source = run / "logs" / audit["source_attempt"]
            assert audit["source_sha256"] == hashlib.sha256(old_logs[source]).hexdigest()
            assert audit["original_model_status"] == "rejected"
            assert audit["before"] == REJECTED and audit["after"] == output
            assert len(audit["changes"]) == 2
            result = graph.invoke(Command(resume=True), config)  # Simulated user approval.
            assert result["quality"]["passed"], result["quality"]
            assert result["__interrupt__"][0].value["stage"] == "delivery"
            result = graph.invoke(Command(resume=True), config)
            assert result["status"] == "passed"
            assert len(requests) == 2
            assert all(path.read_bytes() == raw for path, raw in old_logs.items())
            assert (run / "configuration.json").read_bytes() == saved_config
    finally:
        fresh.close()


@pytest.mark.parametrize("has_booking", [False, True])
@pytest.mark.parametrize("indent", ["    ", "\t"])
def test_contract_repair_preserves_all_fields_and_validator_body(has_booking, indent):
    source = REJECTED + ("\n\n" + BOOKING_CLASS if has_booking else "")
    source = source.replace("    ", indent)
    result = repair_models_contract(source, "src/booking/models.py")
    assert result is not None
    repaired, changes = result
    before_tree, after_tree = ast.parse(source), ast.parse(repaired)
    before = next(node for node in ast.walk(before_tree)
                  if isinstance(node, ast.FunctionDef))
    after = next(node for node in ast.walk(after_tree)
                 if isinstance(node, ast.FunctionDef))
    assert [ast.dump(node) for node in before.body] == [ast.dump(node) for node in after.body]
    assert [node.arg for node in after.args.args] == ["self"]
    assert len(changes) == (1 if has_booking else 2)
    assert clean_file(repaired, "src/booking/models.py") == repaired
    assert len(repaired.splitlines()) < 80
    assert repair_models_contract(repaired, "src/booking/models.py") is None


@pytest.mark.parametrize("unsafe", [
    REJECTED.replace("return self", "return cls"),
    REJECTED.replace("raise ValueError", "print(cls)\n            raise ValueError"),
    REJECTED.replace("(cls, self)", "(cls, value)"),
    REJECTED.replace("(cls, self)", "(cls, self, extra)"),
    REJECTED.replace("@model_validator", "@classmethod\n    @model_validator"),
    REJECTED.replace("Field(ge=1)", "Field(ge=0)"),
    REJECTED.replace("min_length=1", "min_length=0"),
    REJECTED + "\nclass Storage:\n    pass\n",
    REJECTED + "\nprint('unexpected module statement')\n",
    REJECTED + "\n# TODO fix something\n",
    REJECTED.replace("return self", "return undefined_name"),
    "invalid python {{{",
])
def test_contract_repair_declines_other_errors_without_replacing_implementation(unsafe):
    assert repair_models_contract(unsafe, "src/booking/models.py") is None
    assert repair_models_contract(REJECTED, "src/booking/storage.py") is None


def test_truncated_response_is_never_contract_repaired(tmp_path):
    settings = settings_with_keys()

    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        response = completion(request, REJECTED)
        body = response.json()
        body["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=body)

    generator = Generator(settings, tmp_path, httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkflowError, match="tokenloftet"):
            generator.file("coder_1", "src/booking/models.py", "Implement", {})
        assert not list((tmp_path / "logs").glob("*.contract-repair-*.json"))
        assert not list((tmp_path / "logs").glob("*.cache.json"))
        records = [json.loads(path.read_text())
                   for path in (tmp_path / "logs").glob("*-attempt-*.json")]
        assert len(records) == settings.workflow["format_retries"] + 1
        assert all(record["status"] == "rejected" for record in records)
    finally:
        generator.close()


def test_fresh_full_graph_handles_repeat_bad_models_then_runs_real_21_checks(tmp_path):
    settings = settings_with_keys()
    run = tmp_path / "fresh-contract-run"
    init_run(run, settings.snapshot())
    saved_config = (run / "configuration.json").read_bytes()
    replies = Responses()
    calls = []

    def respond(request):
        if request.url.path in {"/apply-template", "/tokenize"}:
            return replies(request)
        body = json.loads(request.content)
        path = body["messages"][-1]["content"].split("File: ", 1)[1].splitlines()[0]
        calls.append(path)
        if path == "src/booking/models.py":
            return completion(request, REJECTED)  # No corrected model response exists.
        return replies(request)

    class Provider(FixtureGenerator):
        def __init__(self, generator):
            super().__init__()
            self.generator = generator

        def file(self, role, path, task, context):
            if path.startswith(("src/", "tests/")):
                return self.generator.file(role, path, task, context)
            return super().file(role, path, task, context)

    generator = Generator(settings, run, httpx.MockTransport(respond))
    config = {"configurable": {"thread_id": "fresh-contract-run"}, "max_concurrency": 2}
    try:
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, Provider(generator), saver)
            result = graph.invoke({"run_dir": str(run), "artifacts": {}, "round": 0}, config)
            assert result["__interrupt__"][0].value["stage"] == "code"
            assert calls.count("src/booking/models.py") == 1
            assert len(calls) == 24  # Models + 7 Storage + 8 API + 8 tests, all mock calls.
            assert not (run / "demo/src/booking/models.py").exists()
            assert not list((run / "logs").glob("pytest*.txt"))
            original = {path: path.read_bytes()
                        for path in (run / "logs").glob("*-attempt-*.json")}
            result = graph.invoke(Command(resume=True), config)
            assert result["quality"]["passed"], result["quality"]
            assert result["quality"]["tests"] == 21
            assert result["__interrupt__"][0].value["stage"] == "delivery"
            result = graph.invoke(Command(resume=True), config)
            assert result["status"] == "passed"
            summary = summarize(run, graph.get_state(config))
            assert summary["model_calls"] == 24
            assert summary["rejected_outputs"] == 1 and summary["contract_repairs"] == 1
            assert all(path.read_bytes() == raw for path, raw in original.items())
            assert (run / "configuration.json").read_bytes() == saved_config
            before = len(calls)
            task = settings.workflow["coder_1"][0]
            context = {name: (run / "demo" / name).read_text() for name in task["read"]}
            assert generator.file("coder_1", task["path"], task["task"], context)
            assert len(calls) == before  # Repaired cache is idempotent.
        deploy = json.loads((run / "demo/reports/deployment.json").read_text())
        assert deploy["passed"] and deploy["container_build"] == "NOT_RUN"
    finally:
        generator.close()
