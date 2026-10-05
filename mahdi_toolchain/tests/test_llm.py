import ast
import hashlib
import json
import traceback
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from mh_toolchain.bootstrap import prepare_backend
from mh_toolchain.config import Settings, WorkflowError, load_settings
from mh_toolchain.llm import (
    Generator,
    assemble_booking_completion,
    booking_completion_source,
    check_endpoints,
    clean_file,
)

# Reduced regression sample from Mahdi's rejected Qwen output: missing Booking,
# mixed modules, unfinished storage/API, and incorrect Pydantic validators.
MIXED_MODELS = """
from datetime import datetime
from pydantic import BaseModel, validator

class RoomCreate(BaseModel):
    name: str
    capacity: int

class Room(BaseModel):
    id: int
    name: str
    capacity: int

class BookingCreate(BaseModel):
    room_id: int
    title: str
    start: datetime
    end: datetime

    @validator('start', 'end')
    def start_before_end(cls, value):
        if value.start >= value.end:
            raise ValueError('Start must be before end')
        return value

class Storage:
    def create_booking(self, data: BookingCreate) -> Booking:
        pass

def create_app(db_path: str):
    pass
"""

# Exact complete Qwen response from run-01, attempt 3, supplied in run-01-fejl.zip.
# The old class-only parser wrongly rejected this response despite all four models.
REJECTED_COMPLETE_MODELS = '''```python
from pydantic import BaseModel, Field, model_validator
from datetime import datetime

class RoomCreate(BaseModel):
    name: str
    capacity: int = Field(ge=1)

class Room(RoomCreate):
    id: int

class BookingCreate(BaseModel):
    room_id: int
    title: str = Field(min_length=1)
    start: datetime
    end: datetime

    @model_validator(mode='after')
    def validate_start_end(self):
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self

class Booking(BookingCreate):
    id: int
```'''


def local_settings():
    settings = load_settings()
    return Settings(
        {
            key: replace(value, api_key="test-only-secret")
            for key, value in settings.endpoints.items()
        },
        settings.roles,
        settings.workflow,
    )


def completion(request, content, finish="stop"):
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "id": "test-response",
            "object": "chat.completion",
            "created": 1,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        },
    )


def test_real_chatopenai_client_routes_retries_truncation_and_caches(tmp_path):
    settings = local_settings()
    # Move coder_1 with configuration only; coder_2 remains bound to llm-b.
    settings.roles = {**settings.roles, "coder_1": "llm-a"}
    code = (Path(__file__).parent / "fixtures/models.py").read_text()
    requests = []

    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "mock chat template"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        requests.append(request)
        assert request.url.host == "127.0.0.1" and request.url.port == 8081
        assert request.headers["Authorization"] == "Bearer test-only-secret"
        payload = json.loads(request.content)
        assert payload["model"] == settings.endpoints["llm-a"].model
        assert payload["max_tokens"] == 2048
        return completion(request, code, "length" if len(requests) == 1 else "stop")

    generator = Generator(settings, tmp_path, httpx.MockTransport(respond))
    try:
        output = generator.file(
            "coder_1", "src/booking/models.py", "Implement", {"SPEC.md": "input"}
        )
        assert "class Booking(" in output and len(requests) == 2
        again = generator.file(
            "coder_1", "src/booking/models.py", "Implement", {"SPEC.md": "input"}
        )
        assert output == again and len(requests) == 2
        logs = list((tmp_path / "logs").glob("*-attempt-*.json"))
        assert len(logs) == 2
        assert "test-only-secret" not in "".join(path.read_text() for path in logs)
        records = [json.loads(path.read_text()) for path in logs]
        assert {item["status"] for item in records} == {"accepted", "rejected"}
    finally:
        generator.close()


def test_oversized_context_is_rejected_without_sending_chat(tmp_path):
    def respond(request):
        assert request.url.path != "/v1/chat/completions"
        return httpx.Response(404)

    generator = Generator(local_settings(), tmp_path, httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkflowError, match="kontekstbudget"):
            generator.file("coder_1", "src/booking/models.py", "Implement", {"huge": "x" * 9000})
    finally:
        generator.close()


@pytest.mark.parametrize("failure", ["http", "connection", "timeout"])
def test_model_errors_include_the_cause_and_http_status_without_keys(tmp_path, failure):
    settings = local_settings()
    calls = []
    detail = "server restarted; test-only-secret; Authorization: Bearer another-secret"
    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        calls.append(request)
        if failure == "http":
            return httpx.Response(503, json={"error": {"message": detail}})
        if failure == "timeout":
            raise httpx.ReadTimeout(detail, request=request)
        raise httpx.ReadError(detail, request=request)
    generator = Generator(settings, tmp_path, httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkflowError) as raised:
            generator.file("coder_1", "src/booking/models.py", "Implement", {})
    finally:
        generator.close()
    assert len(calls) == 1  # No automatic duplicate model requests after transport failure.
    log = next((tmp_path / "logs").glob("*-attempt-*.json"))
    record = json.loads(log.read_text(encoding="utf-8"))
    assert record["status"] == "error"
    assert record["http_status"] == (503 if failure == "http" else None)
    assert "server restarted" in record["error_message"]
    assert "Log: " + log.name in str(raised.value)
    visible = log.read_text(encoding="utf-8") + "".join(traceback.format_exception(raised.value))
    assert "test-only-secret" not in visible and "another-secret" not in visible


def test_failed_call_history_survives_continuation(tmp_path):
    settings = local_settings()
    settings.workflow = {**settings.workflow, "format_retries": 0}
    code = (Path(__file__).parent / "fixtures/models.py").read_text()
    calls = []

    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        calls.append(request)
        return completion(request, code, "length" if len(calls) == 1 else "stop")

    generator = Generator(settings, tmp_path, httpx.MockTransport(respond))
    try:
        with pytest.raises(WorkflowError, match="formatvalidering"):
            generator.file("coder_1", "src/booking/models.py", "Implement", {})
        generator.file("coder_1", "src/booking/models.py", "Implement", {})
        second_prompt = json.loads(calls[1].content)["messages"][-1]["content"]
        assert "Previous output was rejected:" in second_prompt
        records = [
            json.loads(path.read_text()) for path in (tmp_path / "logs").glob("*-attempt-*.json")
        ]
        assert len(records) == 2
        assert {item["status"] for item in records} == {"accepted", "rejected"}
    finally:
        generator.close()


def test_mixed_model_output_is_rejected_with_actionable_feedback():
    with pytest.raises(WorkflowError) as failure:
        clean_file(MIXED_MODELS, "src/booking/models.py")
    message = str(failure.value)
    assert "Missing required top-level definitions: Booking" in message
    assert "Storage" in message and "create_app" in message
    assert "unfinished function bodies" in message
    assert "Pydantic v2" in message


def test_after_model_validator_requires_instance_and_returns_self():
    code = (Path(__file__).parent / "fixtures/models.py").read_text()
    bad = code.replace('def check_interval(self)', 'def check_interval(cls, value)').replace(
        "return self", "return value"
    )
    with pytest.raises(WorkflowError, match="first argument is self"):
        clean_file(bad, "src/booking/models.py")
    assert clean_file(code, "src/booking/models.py").endswith("\n")


def test_real_client_retries_mixed_modules_with_file_scope_and_missing_name(tmp_path):
    settings = local_settings()
    root = Path(__file__).parents[1]
    contract = (root / "template/CONTRACT.md").read_text()
    spec = (root / "template/SPEC.md").read_text()
    code = (Path(__file__).parent / "fixtures/models.py").read_text()
    calls = []
    before = settings.snapshot()

    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        calls.append(request)
        assert request.url.port == 8082
        prompt = json.loads(request.content)["messages"][-1]["content"]
        assert "Booking(BookingCreate): id: int" in prompt
        assert "Contract excerpt for models.py only" in prompt
        assert "- Storage.__init__" not in prompt
        assert prompt.index("CURRENT FILE TASK") > prompt.index("Read-only SPEC.md")
        if len(calls) == 2:
            assert "Missing required top-level definitions: Booking" in prompt
            assert "Remove definitions belonging to other files: Storage, create_app" in prompt
        return completion(request, MIXED_MODELS if len(calls) == 1 else code)

    generator = Generator(settings, tmp_path, httpx.MockTransport(respond))
    try:
        output = generator.file(
            "coder_1",
            "src/booking/models.py",
            "Implement",
            {"SPEC.md": spec, "CONTRACT.md": contract},
        )
        assert "class Booking(" in output and len(calls) == 2
        assert settings.snapshot() == before
        assert contract == (root / "template/CONTRACT.md").read_text()
        records = [
            json.loads(path.read_text()) for path in (tmp_path / "logs").glob("*-attempt-*.json")
        ]
        assert {item["status"] for item in records} == {"accepted", "rejected"}
    finally:
        generator.close()


def test_missing_booking_is_generated_separately_within_the_existing_retry_budget(tmp_path):
    settings = local_settings()
    code = (Path(__file__).parent / "fixtures/models.py").read_text()
    partial, tail = code.rsplit("\n\nclass Booking(", 1)
    fragment = "class Booking(" + tail
    calls = []

    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        calls.append(request)
        if len(calls) == 1:
            return completion(request, partial)
        prompt = json.loads(request.content)["messages"][-1]["content"]
        assert "MISSING CLASS TASK" in prompt and partial in prompt
        return completion(request, "```python\n" + fragment.strip() + "\n```")

    generator = Generator(settings, tmp_path, httpx.MockTransport(respond))
    try:
        output = generator.file("coder_1", "src/booking/models.py", "Implement", {})
        assert "class Booking(BookingCreate)" in output and len(calls) == 2
        assert output.startswith(partial)
        assert generator.file("coder_1", "src/booking/models.py", "Implement", {}) == output
        assert len(calls) == 2
        records = [
            json.loads(path.read_text()) for path in (tmp_path / "logs").glob("*-attempt-*.json")
        ]
        accepted = next(item for item in records if item["status"] == "accepted")
        assert accepted["completion"]["symbol"] == "Booking"
        assert accepted["assembled_content"] == output
        assert "class RoomCreate" not in accepted["content"]
        source_path = tmp_path / "logs" / accepted["completion"]["source_attempt"]
        source = json.loads(source_path.read_text())
        assert source["status"] == "rejected" and source["content"] == partial
    finally:
        generator.close()


def test_actual_complete_response_is_accepted_during_a_class_completion(tmp_path):
    models = clean_file(REJECTED_COMPLETE_MODELS, "src/booking/models.py")
    partial = models.rsplit("\n\nclass Booking(", 1)[0]
    calls = []

    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        calls.append(request)
        return completion(request, partial if len(calls) == 1 else REJECTED_COMPLETE_MODELS)

    generator = Generator(local_settings(), tmp_path, httpx.MockTransport(respond))
    try:
        assert generator.file("coder_1", "src/booking/models.py", "Implement", {}) == models
        assert len(calls) == 2
        accepted = next(
            item
            for item in (
                json.loads(log.read_text())
                for log in (tmp_path / "logs").glob("*-attempt-*.json")
            )
            if item["status"] == "accepted"
        )
        assert accepted["content"] == REJECTED_COMPLETE_MODELS
        assert accepted["assembled_content"] == models
        assert accepted["completion"]["assembly"] == "validate the model-generated complete file"
    finally:
        generator.close()


def test_actual_complete_models_validate_the_contract_at_runtime():
    # This exact, reviewed regression sample only imports datetime/Pydantic and
    # defines models. No arbitrary saved reply is executed by the generator.
    namespace = {}
    exec(clean_file(REJECTED_COMPLETE_MODELS, "src/booking/models.py"), namespace)
    room = namespace["Room"](id=7, name="Room", capacity=1)
    assert room.id == 7 and room.capacity == 1
    data = {
        "room_id": 7,
        "title": "Meeting",
        "start": "2026-10-04T09:00:00",
        "end": "2026-10-04T10:00:00",
    }
    booking = namespace["Booking"](id=9, **data)
    assert booking.id == 9 and booking.start < booking.end
    with pytest.raises(ValidationError):
        namespace["RoomCreate"](name="Room", capacity=0)
    for invalid in ({"title": ""}, {"end": data["start"]}, {"start": data["end"]}):
        with pytest.raises(ValidationError):
            namespace["BookingCreate"](**{**data, **invalid})


def test_continue_recovers_older_complete_reply_without_new_calls_or_history_changes(tmp_path):
    settings = local_settings()
    settings.workflow = {**settings.workflow, "format_retries": 0}
    original_settings = settings.snapshot()
    models = clean_file(REJECTED_COMPLETE_MODELS, "src/booking/models.py")
    partial = models.rsplit("\n\nclass Booking(", 1)[0]
    api = (Path(__file__).parent / "fixtures/api.py").read_text()
    calls = []

    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        calls.append(request)
        prompt = json.loads(request.content)["messages"][-1]["content"]
        return completion(request, api if "File: src/booking/api.py" in prompt else partial)

    first = Generator(settings, tmp_path, httpx.MockTransport(respond))
    try:
        accepted_api = first.file("coder_2", "src/booking/api.py", "Implement", {})
        with pytest.raises(WorkflowError, match="Missing required top-level definitions: Booking"):
            first.file("coder_1", "src/booking/models.py", "Implement", {})
    finally:
        first.close()
    first_attempt = next((tmp_path / "logs").glob("coder_1-models-*-attempt-1.json"))
    stem = first_attempt.name.removesuffix("-attempt-1.json")
    record = json.loads(first_attempt.read_text())
    # Reproduce the four-attempt order from Mahdi's real diagnostic, including a
    # valid older reply and a newer reply still missing Booking.
    for number in (2, 3, 4):
        saved = {**record}
        if number == 3:
            saved.update(
                content=REJECTED_COMPLETE_MODELS,
                error="Return only the missing Booking class; no imports or other classes.",
                completion={
                    "symbol": "Booking",
                    "source_attempt": f"{stem}-attempt-2.json",
                    "assembly": "append the model-generated class, then validate the full file",
                },
            )
        (tmp_path / "logs" / f"{stem}-attempt-{number}.json").write_text(json.dumps(saved))
    original_logs = {
        log: log.read_bytes() for log in (tmp_path / "logs").glob("*-attempt-*.json")
    }
    api_cache = next((tmp_path / "logs").glob("coder_2-api-*.cache.json"))
    original_api_cache = api_cache.read_bytes()

    def no_request(request):
        pytest.fail("Recovery/cache reuse must not send tokenizer or model requests")

    second = Generator(settings, tmp_path, httpx.MockTransport(no_request))
    try:
        assert second.file("coder_1", "src/booking/models.py", "Implement", {}) == models
        assert second.file("coder_2", "src/booking/api.py", "Implement", {}) == accepted_api
        assert second.file("coder_1", "src/booking/models.py", "Implement", {}) == models
    finally:
        second.close()
    assert len(calls) == 2
    assert original_settings == settings.snapshot()
    assert api_cache.read_bytes() == original_api_cache
    assert {log: log.read_bytes() for log in original_logs} == original_logs
    assert len(list((tmp_path / "logs").glob("*-attempt-*.json"))) == len(original_logs)
    recovery = json.loads((tmp_path / "logs" / f"{stem}.recovered.json").read_text())
    source = tmp_path / "logs" / f"{stem}-attempt-3.json"
    assert recovery["content"] == models
    assert recovery["recovery"]["source_attempt"] == source.name
    assert recovery["recovery"]["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert recovery["recovery"]["previous_status"] == "rejected"


@pytest.mark.parametrize(
    "replacement", ["Booking(BaseModel)", "Booking(BookingCreate):\n    id: str"]
)
def test_complete_completion_still_enforces_booking_inheritance_and_id_type(replacement):
    models = clean_file(REJECTED_COMPLETE_MODELS, "src/booking/models.py")
    partial = models.rsplit("\n\nclass Booking(", 1)[0]
    bad = (
        models.replace("Booking(BookingCreate)", replacement)
        if replacement == "Booking(BaseModel)"
        else models.replace("Booking(BookingCreate):\n    id: int", replacement)
    )
    with pytest.raises(WorkflowError, match="Booking must inherit BookingCreate"):
        assemble_booking_completion(partial, bad)


def test_api_blank_line_compaction_preserves_code_and_line_limit():
    api = (Path(__file__).parent / "fixtures/api.py").read_text()
    padded = "\n".join(line if line.strip() else "\n\n\n" for line in api.splitlines())
    assert len(padded.splitlines()) >= 80
    compact = clean_file(padded, "src/booking/api.py")
    assert len(compact.splitlines()) < 80
    assert ast.dump(ast.parse(compact)) == ast.dump(ast.parse(api))


def test_source_line_limit_does_not_change_multiline_literals_or_discard_code():
    literal = 'def create_app(db_path: str):\n    return """first\n' + "\n" * 80 + 'last"""\n'
    code = "value = 1\n" * 80 + "def create_app(db_path: str):\n    return db_path\n"
    for source in (literal, code):
        with pytest.raises(WorkflowError, match="SPEC kræver færre end 80 linjer"):
            clean_file(source, "src/booking/large.py")


def test_recover_old_api_line_rejection_only_when_blank_lines_can_be_removed(tmp_path):
    api = (Path(__file__).parent / "fixtures/api.py").read_text()
    calls = []

    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        calls.append(request)
        return completion(request, api)

    first = Generator(local_settings(), tmp_path, httpx.MockTransport(respond))
    try:
        first.file("coder_2", "src/booking/api.py", "Implement", {})
    finally:
        first.close()
    cache = next((tmp_path / "logs").glob("coder_2-api-*.cache.json"))
    cache.unlink()
    attempt = next((tmp_path / "logs").glob("coder_2-api-*-attempt-1.json"))
    record = json.loads(attempt.read_text())
    padded = "\n".join(line if line.strip() else "\n\n\n" for line in api.splitlines())
    record.update(content=padded, status="rejected", error="SPEC kræver færre end 80 linjer.")
    attempt.write_text(json.dumps(record))
    original = attempt.read_bytes()

    def no_request(request):
        pytest.fail("A recoverable prior API reply must not cause new requests")

    second = Generator(local_settings(), tmp_path, httpx.MockTransport(no_request))
    try:
        recovered = second.file("coder_2", "src/booking/api.py", "Implement", {})
    finally:
        second.close()
    assert len(calls) == 1 and attempt.read_bytes() == original
    assert len(recovered.splitlines()) < 80
    assert ast.dump(ast.parse(recovered)) == ast.dump(ast.parse(api))
    metadata = json.loads(next((tmp_path / "logs").glob("*.recovered.json")).read_text())
    assert metadata["recovery"]["raw_lines"] >= 80
    assert metadata["recovery"]["validated_lines"] < 80


def test_continue_completes_saved_models_and_reuses_accepted_api_cache(tmp_path):
    settings = local_settings()
    settings.workflow = {**settings.workflow, "format_retries": 0}
    before = settings.snapshot()
    fixtures = Path(__file__).parent / "fixtures"
    models = (fixtures / "models.py").read_text()
    api = (fixtures / "api.py").read_text()
    partial, tail = models.rsplit("\n\nclass Booking(", 1)
    fragment = "class Booking(" + tail
    calls = []

    def respond(request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 100})
        calls.append(request)
        prompt = json.loads(request.content)["messages"][-1]["content"]
        if "File: src/booking/api.py" in prompt:
            return completion(request, api)
        return completion(request, fragment if "MISSING CLASS TASK" in prompt else partial)

    transport = httpx.MockTransport(respond)
    first = Generator(settings, tmp_path, transport)
    try:
        accepted_api = first.file("coder_2", "src/booking/api.py", "Implement", {})
        with pytest.raises(WorkflowError, match="Missing required top-level definitions: Booking"):
            first.file("coder_1", "src/booking/models.py", "Implement", {})
    finally:
        first.close()
    assert len(calls) == 2
    second = Generator(settings, tmp_path, httpx.MockTransport(respond))
    try:
        assert second.file("coder_2", "src/booking/api.py", "Implement", {}) == accepted_api
        assert len(calls) == 2
        completed = second.file("coder_1", "src/booking/models.py", "Implement", {})
        assert len(calls) == 3 and "class Booking(" in completed
        assert settings.snapshot() == before
    finally:
        second.close()


@pytest.mark.parametrize(
    "fragment",
    [
        "class Booking(BaseModel):\n    id: int\n",
        "class Booking(BookingCreate):\n    id: str\n",
        "class Booking(BookingCreate):\n    id: int = print('side effect')\n",
        "import os\nclass Booking(BookingCreate):\n    id: int\n",
    ],
)
def test_invalid_completion_cannot_bypass_the_full_file_contract(fragment):
    models = (Path(__file__).parent / "fixtures/models.py").read_text()
    partial = models.rsplit("\n\nclass Booking(", 1)[0]
    assert booking_completion_source(partial, "src/booking/models.py") is not None
    with pytest.raises(WorkflowError):
        assemble_booking_completion(partial, fragment)
    assert booking_completion_source(MIXED_MODELS, "src/booking/models.py") is None


def test_check_proves_auth_alias_and_chat_for_both_endpoints():
    settings = local_settings()
    seen = set()

    def respond(request):
        ep = settings.endpoints["llm-a" if request.url.port == 8081 else "llm-b"]
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if "Authorization" not in request.headers:
            return httpx.Response(401)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": ep.model}]})
        seen.add(request.url.port)
        return completion(request, "OK")

    results = check_endpoints(settings, transport=httpx.MockTransport(respond))
    assert seen == {8081, 8082}
    assert all(item["chat"] == "PASS" for item in results)


def test_backend_preparation_preserves_keys_and_repairs_crlf(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("download-models.sh", "smoke-test.sh"):
        (scripts / name).write_bytes(b"#!/bin/sh\r\nexit 0\r\n")
    env = tmp_path / ".env"
    env.write_text("LLM_A_API_KEY=keep-this-key\nLLM_B_API_KEY=\nLLM_A_PORT=9081\n")
    messages = prepare_backend(tmp_path)
    first = env.read_bytes()
    assert b"LLM_A_API_KEY=keep-this-key" in first and b"LLM_A_PORT=9081" in first
    assert "API-nøgler" in " ".join(messages)
    assert all(b"\r" not in path.read_bytes() for path in scripts.iterdir())
    prepare_backend(tmp_path)
    assert env.read_bytes() == first
