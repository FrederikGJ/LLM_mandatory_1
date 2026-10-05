"""Actual rejected Qwen sources plus SDK/SQLite regressions for scoped generation."""

import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from mh_toolchain.config import WorkflowError, load_settings
from mh_toolchain.llm import Generator, clean_file, compact_blank_lines, scope_request
from mh_toolchain.parts import (
    API,
    BOOKING_TESTS,
    ROOM_TESTS,
    STORAGE,
    STORAGE_PREAMBLE,
    compact_function,
    fold_continuations,
    indent_function,
    string_continuations,
    validate_part,
)

FIXTURES = Path(__file__).parent / "fixtures"
PROBLEMS = json.loads((FIXTURES / "qwen_problem_sources.json").read_text())
ACTUAL_ASSEMBLY = json.loads((FIXTURES / "qwen_run01_assembly.json").read_text(encoding="utf-8"))
LONG_INITIALIZER = '''def __init__(self, db_path: str) -> None:
    self.db = sqlite3.connect(db_path, check_same_thread=False)
    self.db.row_factory = sqlite3.Row
    self.db.executescript(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            capacity INTEGER
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER,
            title TEXT,
            start TEXT,
            end TEXT,
            FOREIGN KEY (room_id) REFERENCES rooms(id)
        );
        """
    )
    self.db.commit()
'''


def settings_with_keys():
    settings = load_settings()
    settings.endpoints = {
        key: replace(value, api_key="test-only-secret") for key, value in settings.endpoints.items()
    }
    return settings


def function_responses():
    """Test data only. Normal run.py never imports these implementations."""
    responses = {}
    for kind, parts, filename in (
        ("storage", STORAGE, "storage.py"), ("api", API, "api.py"),
        ("test_rooms", ROOM_TESTS, "rooms_cases.py"),
        ("test_bookings", BOOKING_TESTS, "booking_cases.py"),
    ):
        source = (FIXTURES / filename).read_text()
        tree = ast.parse(source)
        for part in parts:
            function = next(
                item for item in ast.walk(tree)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == part.name
            )
            raw = ast.get_source_segment(source, function)
            assert raw is not None
            responses[kind, part.name] = part.header + "\n" + "\n".join(raw.splitlines()[1:])
    return responses


class Responses:
    def __init__(self, fail_get_room=False, legacy_api=False):
        self.parts = function_responses()
        self.calls = []
        self.fail_get_room = fail_get_room
        self.legacy_api = legacy_api

    def __call__(self, request):
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "test"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * 120})
        payload = json.loads(request.content)
        assert request.url.host == "127.0.0.1" and request.url.port == 8082
        assert request.headers["Authorization"] == "Bearer test-only-secret"
        assert payload["model"] == "qwen2.5-coder-1.5b"
        prompt = payload["messages"][-1]["content"]
        path = prompt.split("File: ", 1)[1].splitlines()[0]
        self.calls.append(path)
        if path == "src/booking/models.py":
            content = (FIXTURES / "models.py").read_text()
        elif path == "src/booking/api.py" and self.legacy_api:
            content = next(item["content"] for item in PROBLEMS if item["status"] == "accepted")
        else:
            assert path.startswith("fragments/")
            kind = Path(path).parts[-2] if path.startswith("fragments/tests/") else (
                Path(path).parts[1]
            )
            function = Path(path).stem
            content = self.parts[kind, function]
            if path.startswith("fragments/tests/"):
                assert "The application is already implemented" in prompt
                assert "CURRENT TEST FUNCTION TASK" in prompt
                assert f"def {function}(client):" in prompt
                assert "No application code" in payload["messages"][0]["content"]
            else:
                assert "Explicit interface excerpt" in prompt
            assert "class RoomCreate(" not in prompt and "Ticket T-01" not in prompt
            if self.fail_get_room and (kind, function) == ("storage", "get_room"):
                content = "def get_room(self, room_id: int) -> Room:\n    return absent_row\n"
        return httpx.Response(200, json={
            "id": "scoped-test", "object": "chat.completion", "created": 1,
            "model": payload["model"], "choices": [{
                "index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }], "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
        })


def inputs():
    root = Path(__file__).parents[1]
    return {name: (root / "template" / name).read_text() for name in ("SPEC.md", "CONTRACT.md")}


def seed_actual_logs(run):
    """Exact bytes from the user archive; never called from production code."""
    logs = run / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    original = {}
    for name, raw in ACTUAL_ASSEMBLY["logs"].items():
        path = logs / name
        original[path] = raw.encode("utf-8")
        path.write_bytes(original[path])
    return original


def actual_cache(function):
    name = next(name for name in ACTUAL_ASSEMBLY["logs"]
                if name.startswith(f"coder_1-{function}-") and name.endswith(".cache.json")
                and (function != "models" or "1440074710f53cfd1592" in name))
    return name, json.loads(ACTUAL_ASSEMBLY["logs"][name])


def test_tester_preserves_truncated_whole_file_and_generates_eight_small_tests(tmp_path):
    settings = settings_with_keys()
    snapshot = settings.snapshot()
    logs = tmp_path / "logs"
    logs.mkdir()
    context = inputs()
    room_task = settings.workflow["tester"][0]
    digest = hashlib.sha256(json.dumps(
        ["tester", room_task["path"], room_task["task"], context,
         settings.for_role("tester").public()], sort_keys=True,
    ).encode()).hexdigest()[:20]
    # Actual pasted truncated answer; the JSON envelope is reconstructed test data.
    rejected = logs / f"tester-test_rooms-{digest}-attempt-1.json"
    original = json.dumps({
        "path": room_task["path"], "status": "rejected", "finish_reason": "length",
        "error": "Svaret ramte tokenloftet; skriv filen mere kompakt.",
        "content": (FIXTURES / "qwen_truncated_tests.txt").read_text(),
    }).encode()
    rejected.write_bytes(original)
    replies = Responses()
    client = Generator(settings, tmp_path, httpx.MockTransport(replies))
    try:
        for task in settings.workflow["tester"]:
            output = client.file("tester", task["path"], task["task"], context)
            tree = ast.parse(output)
            assert len([node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name.startswith("test_")]) == 4
            assert "@pytest.fixture" in output and "create_app(':memory:')" in output
            assert not any(isinstance(node, ast.ClassDef) for node in tree.body)
            assert len(output.splitlines()) < 80
            assert client.file("tester", task["path"], task["task"], context) == output
    finally:
        client.close()
    assert len(replies.calls) == 8
    assert all(path.startswith("fragments/tests/") for path in replies.calls)
    assert rejected.read_bytes() == original
    assert settings.snapshot() == snapshot
    for path in logs.glob("tester-test_*.assembly.json"):
        assembly = json.loads(path.read_text())["assembly"]
        assert len(assembly["functions"]) == 4
        assert assembly["full_input_context"] == context
        for source in assembly["functions"]:
            cached = json.loads((logs / source["cache"]).read_text())["content"]
            assert source["content_sha256"] == hashlib.sha256(cached.encode()).hexdigest()


@pytest.mark.parametrize("body, error", [
    ("assert True", "real test"),
    ("assert _room()['capacity'] == 4", "real HTTP requests"),
    ("assert client.post('/bookings', json=_booking(1)).status_code == 201", "Undefined.*_booking"),
])
def test_scoped_tests_reject_vacuous_or_unavailable_dependencies(body, error):
    part = ROOM_TESTS[0]
    with pytest.raises(WorkflowError, match=error):
        validate_part(part.header + "\n    " + body + "\n", part, False, True)


def test_test_module_must_import_the_app_instead_of_redefining_it():
    source = "class RoomCreate:\n    name: str\n\ndef test_health(client):\n"
    source += "    assert client.get('/health').status_code == 200\n"
    with pytest.raises(WorkflowError, match="Tests must import the existing app"):
        clean_file(source, "tests/test_rooms.py")


@pytest.mark.parametrize("record", PROBLEMS, ids=[f"reply-{i}" for i in range(len(PROBLEMS))])
def test_real_problem_sources_are_rejected_before_review(record):
    with pytest.raises(WorkflowError):
        clean_file(record["content"], record["path"])


def test_static_check_catches_undefined_closure_names_without_execution():
    api = (FIXTURES / "api.py").read_text().replace(
        "storage.create_room", "missing_storage.create_room"
    )
    with pytest.raises(WorkflowError, match="Undefined.*missing_storage"):
        clean_file(api, "src/booking/api.py")


def test_failed_function_continues_using_cached_completed_functions(tmp_path):
    settings = settings_with_keys()
    settings.workflow = {**settings.workflow, "format_retries": 0}
    before = settings.snapshot()
    responses = Responses(fail_get_room=True)
    first = Generator(settings, tmp_path, httpx.MockTransport(responses))
    try:
        with pytest.raises(WorkflowError, match="absent_row"):
            first.file("coder_1", "src/booking/storage.py", "Implement", inputs())
    finally:
        first.close()
    assert responses.calls == [f"fragments/storage/{name}.py" for name in (
        "__init__", "create_room", "get_room"
    )]
    saved = {path: path.read_bytes() for path in (tmp_path / "logs").glob("*-attempt-*.json")}
    responses.fail_get_room = False
    second = Generator(settings, tmp_path, httpx.MockTransport(responses))
    try:
        output = second.file("coder_1", "src/booking/storage.py", "Implement", inputs())
        count = len(responses.calls)
        assert second.file("coder_1", "src/booking/storage.py", "Implement", inputs()) == output
        assert len(responses.calls) == count
    finally:
        second.close()
    assert len(responses.calls) == 8 and len(output.splitlines()) < 80
    assert all(path.read_bytes() == raw for path, raw in saved.items())
    assert before == settings.snapshot()
    assembly = json.loads(next((tmp_path / "logs").glob("*.assembly.json")).read_text())
    assert len(assembly["assembly"]["functions"]) == 7
    for item in assembly["assembly"]["functions"]:
        cached = json.loads((tmp_path / "logs" / item["cache"]).read_text())["content"]
        assert item["content_sha256"] == hashlib.sha256(cached.encode()).hexdigest()
    assert len(list((tmp_path / "logs").glob("*-attempt-*.json"))) == 8


def test_invalid_accepted_api_cache_is_preserved_and_rebuilt(tmp_path):
    settings = settings_with_keys()
    context = inputs()
    task = "Implement"
    endpoint = settings.for_role("coder_2")
    scoped_task, scoped_context = scope_request("src/booking/api.py", task, context)
    digest = hashlib.sha256(json.dumps(
        ["coder_2", "src/booking/api.py", scoped_task, scoped_context, endpoint.public()],
        sort_keys=True,
    ).encode()).hexdigest()[:20]
    stem = f"coder_2-api-{digest}"
    bad = next(record["content"] for record in PROBLEMS if record["status"] == "accepted")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / f"{stem}.cache.json").write_text(json.dumps({"content": bad}))
    responses = Responses()
    generator = Generator(settings, tmp_path, httpx.MockTransport(responses))
    try:
        output = generator.file("coder_2", "src/booking/api.py", task, context)
    finally:
        generator.close()
    assert len(responses.calls) == 8 and len(output.splitlines()) < 80
    invalidation = json.loads((logs / f"{stem}.cache-invalidated.json").read_text())
    assert invalidation["original_cache"]["content"] == bad
    assert "local FastAPI" in invalidation["error"]
    assert clean_file(output, "src/booking/api.py") == output


def test_scaffold_indentation_preserves_multiline_literals():
    part = next(item for item in STORAGE if item.name == "get_room")
    raw = part.header + (
        '\n    message = """first\nsecond\n  third"""\n    raise NotFoundError(message)\n'
    )
    canonical = validate_part(raw, part, True)
    wrapped = "class Storage:\n" + indent_function(canonical)
    before = ast.parse(raw).body[0]
    after = ast.parse(wrapped).body[0].body[0]
    assert ast.dump(ast.Module(body=before.body, type_ignores=[])) == ast.dump(
        ast.Module(body=after.body, type_ignores=[])
    )


def test_long_sql_initializer_preserves_its_body_and_is_not_limited_to_ten_lines():
    part = STORAGE[0]
    canonical = validate_part(LONG_INITIALIZER, part, True)
    assert part.target_lines < len(canonical.splitlines()) < len(LONG_INITIALIZER.splitlines())
    assert ast.dump(ast.parse(canonical)) == ast.dump(ast.parse(LONG_INITIALIZER))
    assert max(map(len, canonical.splitlines())) <= 96
    assert validate_part(canonical, part, True) == canonical


def test_handler_can_exceed_the_prompt_line_target_without_losing_comments():
    part = next(item for item in API if item.name == "create_room")
    source = part.header + (
        "\n    # Keep this explanation and any quality/type directives.\n"
        "    result = storage.create_room(data)\n    return result\n"
    )
    result = validate_part(source, part, False)
    assert len(result.splitlines()) > part.target_lines
    assert "# Keep this explanation" in result
    assert ast.dump(ast.parse(result)) == ast.dump(ast.parse(source))


def test_literal_layout_preserves_sql_comments_quoted_text_and_unicode():
    part = next(item for item in STORAGE if item.name == "get_room")
    value = "-- Bevar linjeskiftet: ø, 漢字, 'citat'\\n\nSELECT 'a  b', \"x\"; " * 4
    source = part.header + "\n    raise NotFoundError(" + repr(value) + ")\n"
    result = validate_part(source, part, True)
    assert ast.dump(ast.parse(result)) == ast.dump(ast.parse(source))
    assert max(map(len, result.splitlines())) <= 96


def test_assembled_source_still_rejects_eighty_or_more_lines(tmp_path):
    responses = Responses()
    responses.parts["storage", "__init__"] = (
        STORAGE[0].header + "\n" + "    self.db.commit()\n" * 80
    )
    generator = Generator(settings_with_keys(), tmp_path, httpx.MockTransport(responses))
    try:
        with pytest.raises(WorkflowError, match="SPEC kræver færre end 80 linjer"):
            generator.file("coder_1", "src/booking/storage.py", "Implement", inputs())
    finally:
        generator.close()
    assert not list((tmp_path / "logs").glob("*.assembly.json"))
    rejected = json.loads(next((tmp_path / "logs").glob("*.assembly-rejected.json")).read_text())
    assert rejected["status"] == "rejected" and len(rejected["assembly"]["functions"]) == 7


def test_actual_86_line_assembly_can_be_compacted_without_changing_any_function_body():
    functions = [actual_cache(part.name)[1]["content"].rstrip() for part in STORAGE]
    before = STORAGE_PREAMBLE + indent_function("\n\n".join(functions)) + "\n"
    assert len(before.splitlines()) == 86
    assert sum(bool(line.strip()) for line in before.splitlines()) == 73
    shorter = [compact_function(function) for function in functions]
    after = STORAGE_PREAMBLE + indent_function("\n\n".join(shorter)) + "\n"
    after = compact_blank_lines(after, ast.parse(after))
    assert len(after.splitlines()) < 80
    assert ast.dump(ast.parse(after)) == ast.dump(ast.parse(before))
    # Equivalent formatting alone must not hide the two actual functional problems.
    with pytest.raises(WorkflowError, match="cursors.*with.*R5"):
        clean_file(after, "src/booking/storage.py")


@pytest.mark.parametrize("function, message", [("create_room", "cursors.*with"),
                                                ("list_bookings", "R5.*sort by start")])
def test_actual_bad_functions_are_rejected_without_executing_them(function, message):
    part = next(part for part in STORAGE if part.name == function)
    with pytest.raises(WorkflowError, match=message):
        validate_part(actual_cache(function)[1]["content"], part, True)


@pytest.mark.parametrize("body", [
    "    with self.db.cursor() as cursor:\n        return []\n",
    "    cursor = self.db.execute('SELECT * FROM rooms')\n    with cursor:\n        return []\n",
    "    alias = cursor\n    cursor = self.db.cursor()\n    with alias:\n        return []\n",
])
def test_direct_and_assigned_sqlite_cursors_are_not_context_managers(body):
    part = next(part for part in STORAGE if part.name == "list_rooms")
    with pytest.raises(WorkflowError, match="cursors.*with"):
        validate_part(part.header + "\n" + body, part, True)


def test_python_sort_by_start_is_allowed_as_an_alternative_to_sql_ordering():
    part = next(part for part in STORAGE if part.name == "list_bookings")
    source = part.header + (
        "\n    self.get_room(room_id)\n"
        "    rows = self.db.execute('SELECT * FROM bookings WHERE room_id = ?', (room_id,))\n"
        "    return sorted([Booking(**dict(row)) for row in rows], "
        "key=lambda booking: booking.start)\n"
    )
    assert validate_part(source, part, True)


@pytest.mark.parametrize("intervening_errors", [False, True])
def test_repeated_actual_r5_reply_gets_a_concrete_repair_and_reuses_other_caches(
    tmp_path, intervening_errors,
):
    """Actual pasted reply; new transport responses and log envelopes are test data."""
    bad = '''```python
def list_bookings(self, room_id: int) -> list[Booking]:
    room = self.get_room(room_id)
    bookings = self.db.execute("SELECT * FROM bookings WHERE room_id = ?", (room_id,)).fetchall()
    return [Booking(**dict(row)) for row in bookings]
```'''
    part = next(part for part in STORAGE if part.name == "list_bookings")
    with pytest.raises(WorkflowError, match="R5.*sort by start"):
        validate_part(bad, part, True)
    settings = settings_with_keys()
    seed_actual_logs(tmp_path)
    responses = Responses()
    # Represent the user's already corrected create_room; its actual new reply is unseen.
    room_cache, _ = actual_cache("create_room")
    (tmp_path / "logs" / room_cache).write_text(json.dumps({
        "content": responses.parts["storage", "create_room"], "test_fixture": True,
    }), encoding="utf-8")
    bookings_cache, _ = actual_cache("list_bookings")
    stem = bookings_cache.removesuffix(".cache.json")
    for attempt in (2, 3):
        (tmp_path / "logs" / f"{stem}-attempt-{attempt}.json").write_text(json.dumps({
            "role": "coder_1", "path": "fragments/storage/list_bookings.py",
            "endpoint": "llm-b", "model": "qwen2.5-coder-1.5b", "finish_reason": "stop",
            "content": bad, "status": "rejected", "error": "R5: list_bookings must sort by start.",
            "test_fixture": "Envelope reconstructed; content copied from user's terminal.",
        }), encoding="utf-8")
    if intervening_errors:
        for number, error in ((4, "OpenAITimeoutError"), (5, "OpenAIAPIError")):
            (tmp_path / "logs" / f"{stem}-attempt-{number}.json").write_text(json.dumps({
                "role": "coder_1", "path": "fragments/storage/list_bookings.py",
                "endpoint": "llm-b", "model": "qwen2.5-coder-1.5b", "status": "error",
                "error": error, "test_fixture": "Simulated transport failure after R5 rejection.",
            }), encoding="utf-8")
    before = {p: p.read_bytes() for p in (tmp_path / "logs").glob("*-attempt-*.json")}
    prompts = []
    def transport(request):
        if request.url.path.endswith("/chat/completions"):
            prompt = json.loads(request.content)["messages"][-1]["content"]
            prompts.append(prompt)
            assert "Read-only rejected function:\n" + bad.removeprefix("```python\n").removesuffix(
                "\n```"
            ) in prompt
            assert "MANDATORY EDIT:" in prompt
            assert "SELECT * FROM bookings WHERE room_id = ? ORDER BY start" in prompt
            assert "do not assign an unused room variable" in prompt
            # Fail once again to exercise feedback inside the same bounded retry loop.
            responses.parts["storage", "list_bookings"] = bad if len(prompts) == 1 else (
                function_responses()["storage", "list_bookings"].replace(
                    "room_id=?", "room_id = ?"
                )
            )
        return responses(request)
    context = inputs()
    context["src/booking/models.py"] = clean_file(actual_cache("models")[1]["content"],
                                                 "src/booking/models.py")
    task = settings.workflow["coder_1"][1]
    generator = Generator(settings, tmp_path, httpx.MockTransport(transport))
    try:
        output = generator.file("coder_1", task["path"], task["task"], context)
        assert generator.file("coder_1", task["path"], task["task"], context) == output
    finally:
        generator.close()
    assert responses.calls == ["fragments/storage/list_bookings.py"] * 2
    assert len(output.splitlines()) < 80
    assert "ORDER BY start" in output
    assert all(p.read_bytes() == raw for p, raw in before.items())
    first = 6 if intervening_errors else 4
    rejected = json.loads((tmp_path / "logs" / f"{stem}-attempt-{first}.json").read_text())
    accepted = json.loads((tmp_path / "logs" / f"{stem}-attempt-{first + 1}.json").read_text())
    assert rejected["status"] == "rejected" and accepted["status"] == "accepted"
    assert accepted["messages"][-1]["content"] == prompts[-1]


def test_line_folding_keeps_statement_boundaries_comments_and_multiline_strings():
    source = '''def example(value):
    text = """first
second
    third"""
    result = dict(
        key=value,  # Keep this on its own line.
        other=text,
    )
    print(result)
    return result
'''
    after = fold_continuations(source)
    assert ast.dump(ast.parse(after)) == ast.dump(ast.parse(source))
    assert "key=value,  # Keep this on its own line." in after
    assert "    print(result)\n    return result" in after
    assert 'text = """first\nsecond\n    third"""' in after


def test_actual_caches_reuse_five_functions_and_regenerate_only_the_two_bad_ones(tmp_path):
    settings = settings_with_keys()
    assert settings.snapshot() == ACTUAL_ASSEMBLY["configuration"]
    original = seed_actual_logs(tmp_path)
    responses = Responses()
    context = inputs()
    context["src/booking/models.py"] = clean_file(actual_cache("models")[1]["content"],
                                                 "src/booking/models.py")
    task = settings.workflow["coder_1"][1]
    generator = Generator(settings, tmp_path, httpx.MockTransport(responses))
    try:
        output = generator.file("coder_1", task["path"], task["task"], context)
        assert generator.file("coder_1", task["path"], task["task"], context) == output
    finally:
        generator.close()
    assert responses.calls == ["fragments/storage/create_room.py",
                               "fragments/storage/list_bookings.py"]
    assert len(output.splitlines()) < 80
    assert all(path.read_bytes() == raw for path, raw in original.items()
               if "-attempt-" in path.name)
    for name in ("__init__", "get_room", "list_rooms", "create_booking", "delete_booking"):
        before = ast.parse(actual_cache(name)[1]["content"]).body[0]
        after = next(node for node in ast.walk(ast.parse(output))
                     if isinstance(node, ast.FunctionDef) and node.name == name)
        assert ast.dump(ast.Module(body=before.body, type_ignores=[])) == ast.dump(
            ast.Module(body=after.body, type_ignores=[])
        )
    for function in ("create_room", "list_bookings"):
        cache_name, old_cache = actual_cache(function)
        invalidation = json.loads((tmp_path / "logs" / cache_name.replace(
            ".cache.json", ".cache-invalidated.json"
        )).read_text(encoding="utf-8"))
        assert invalidation["original_cache"] == old_cache
    for path in (tmp_path / "logs").glob("*.cache-revalidated.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        source = path.with_name(path.name.replace(".cache-revalidated.json", ".cache.json"))
        assert record["revalidation"]["source_cache_sha256"] == hashlib.sha256(
            original[source]
        ).hexdigest()
    assembly = json.loads(next((tmp_path / "logs").glob("*.assembly.json")).read_text())
    assert len(assembly["assembly"]["functions"]) == 7
    for item in assembly["assembly"]["functions"]:
        cached = json.loads((tmp_path / "logs" / item["cache"]).read_text())["content"]
        assert item["content_sha256"] == hashlib.sha256(cached.encode()).hexdigest()


def test_overflowing_assembly_gets_one_targeted_model_repair_without_regenerating_other_functions(
    tmp_path,
):
    responses = Responses()
    compact_init = responses.parts["storage", "__init__"]
    first = compact_init.splitlines()[1]
    indentation = " " * (len(first) - len(first.lstrip()))
    responses.parts["storage", "__init__"] += "\n" + (indentation + "self.db.commit()\n") * 30

    def transport(request):
        if request.url.path not in {"/apply-template", "/tokenize"}:
            prompt = json.loads(request.content)["messages"][-1]["content"]
            if "ASSEMBLY REPAIR:" in prompt:
                assert "function __init__ only" in prompt
                assert "Read-only current-function.py:" in prompt
                responses.parts["storage", "__init__"] = compact_init
        return responses(request)

    settings = settings_with_keys()
    before = settings.snapshot()
    generator = Generator(settings, tmp_path, httpx.MockTransport(transport))
    try:
        output = generator.file("coder_1", "src/booking/storage.py", "Implement", inputs())
    finally:
        generator.close()
    assert len(output.splitlines()) < 80
    assert len(responses.calls) == 8
    assert responses.calls.count("fragments/storage/__init__.py") == 2
    assert len(list((tmp_path / "logs").glob("*-attempt-*.json"))) == 8
    assert before == settings.snapshot()
    rejected = json.loads(next((tmp_path / "logs").glob("*.assembly-rejected.json")).read_text())
    assert rejected["status"] == "rejected"
    assembly = json.loads(next((tmp_path / "logs").glob("*.assembly.json")).read_text())
    repaired = [item for item in assembly["assembly"]["functions"] if "assembly_repair" in item]
    assert len(repaired) == 1 and repaired[0]["function"] == "__init__"


def eighty_line_source():
    """Synthetic 80/67 boundary; the user's latest complete source is not available."""
    source = (FIXTURES / "storage.py").read_text().rstrip() + "\n"
    comments = "".join(f"        # Preserve explanation {number}.\n" for number in range(15))
    source = source.replace(
        "        self.db.commit()\n", comments + "        self.db.commit()\n", 1,
    )
    assert len(source.splitlines()) == 80
    assert sum(bool(line.strip()) for line in source.splitlines()) == 67
    return source


def test_exact_eighty_line_boundary_preserves_statements_comments_sql_and_import_groups():
    source = eighty_line_source()
    result = clean_file(source, "src/booking/storage.py")
    assert len(result.splitlines()) == 79
    assert ast.dump(ast.parse(result)) == ast.dump(ast.parse(source))
    assert all(f"# Preserve explanation {number}." in result for number in range(15))
    assert "import sqlite3\n\nfrom booking.models" in result
    assert "pass\n\n\nclass ConflictError" in result
    assert clean_file(result, "src/booking/storage.py") == result


def seed_rejected_assembly(run, settings, source):
    """Represent a failed final assembly and its seven accepted source caches."""
    logs = run / "logs"
    logs.mkdir()
    path, task, context = "src/booking/storage.py", "Implement", inputs()
    task, context = scope_request(path, task, context)
    digest = hashlib.sha256(json.dumps(
        ["coder_1", path, task, context, settings.for_role("coder_1").public()], sort_keys=True,
    ).encode()).hexdigest()[:20]
    stem = f"coder_1-storage-{digest}"
    lines, tree, sources = source.splitlines(), ast.parse(source), []
    for part in STORAGE:
        node = next(item for item in ast.walk(tree)
                    if isinstance(item, ast.FunctionDef) and item.name == part.name)
        protected = string_continuations(node)
        code = part.header + "\n" + "\n".join(
            line if number in protected else line[4:]
            for number, line in enumerate(lines[node.lineno:node.end_lineno], node.lineno + 1)
        ) + "\n"
        cache_name = f"coder_1-{part.name}-boundary-fixture.cache.json"
        (logs / cache_name).write_text(json.dumps({"content": code, "test_fixture": True}))
        sources.append({"function": part.name, "cache": cache_name,
                        "content_sha256": hashlib.sha256(code.encode()).hexdigest()})
    record = {
        "content": source, "status": "rejected",
        "error": "SPEC kræver færre end 80 linjer pr. kildefil (80 linjer; 67 ikke-tomme).",
        "assembly": {
            "role": "coder_1", "path": path, "functions": sources, "round": 1,
            "full_context_sha256": hashlib.sha256(json.dumps(context, sort_keys=True).encode(
            )).hexdigest(),
        },
    }
    rejected_path = logs / f"{stem}.assembly-rejected.json"
    rejected_path.write_text(json.dumps(record), encoding="utf-8")
    return rejected_path


def test_rejected_final_assembly_is_recovered_without_model_calls_or_changed_old_logs(tmp_path):
    settings = settings_with_keys()
    source = eighty_line_source()
    rejected_path = seed_rejected_assembly(tmp_path, settings, source)
    original = {path: path.read_bytes() for path in (tmp_path / "logs").iterdir()}
    before = settings.snapshot()

    def transport(_request):
        pytest.fail("A valid saved 80-line assembly needs no network request")

    generator = Generator(settings, tmp_path, httpx.MockTransport(transport))
    try:
        output = generator.file("coder_1", "src/booking/storage.py", "Implement", inputs())
        assert generator.file("coder_1", "src/booking/storage.py", "Implement", inputs()) == output
    finally:
        generator.close()
    assert len(output.splitlines()) == 79
    assert ast.dump(ast.parse(output)) == ast.dump(ast.parse(source))
    assert before == settings.snapshot()
    assert all(path.read_bytes() == content for path, content in original.items())
    assert not list((tmp_path / "logs").glob("*-attempt-*.json"))
    record = json.loads(next((tmp_path / "logs").glob("*.assembly.json")).read_text())
    assert record["assembly"]["line_count"] == 79
    assert record["assembly"]["recovery"]["source"] == rejected_path.name
    assert record["assembly"]["recovery"]["source_sha256"] == hashlib.sha256(
        original[rejected_path]
    ).hexdigest()


@pytest.mark.parametrize("problem", [
    "different-context", "cache-hash", "source-body", "unsorted-bookings",
])
def test_assembly_recovery_requires_matching_sources_context_and_current_validation(
    tmp_path, problem,
):
    settings = settings_with_keys()
    source = eighty_line_source()
    if problem == "unsorted-bookings":
        source = source.replace("room_id=? ORDER BY start", "room_id=?")
    rejected_path = seed_rejected_assembly(tmp_path, settings, source)
    record = json.loads(rejected_path.read_text())
    if problem == "different-context":
        record["assembly"]["full_context_sha256"] = "different"
    elif problem == "cache-hash":
        record["assembly"]["functions"][0]["content_sha256"] = "different"
    elif problem == "source-body":
        record["content"] = record["content"].replace("ORDER BY id", "ORDER BY name")
    rejected_path.write_text(json.dumps(record), encoding="utf-8")
    original = rejected_path.read_bytes()
    responses = Responses()
    generator = Generator(settings, tmp_path, httpx.MockTransport(responses))
    try:
        output = generator.file("coder_1", "src/booking/storage.py", "Implement", inputs())
    finally:
        generator.close()
    assert len(responses.calls) == 7
    assert "ORDER BY start" in output
    assert not list((tmp_path / "logs").glob("*.assembly-recovered.json"))
    assert rejected_path.read_bytes() == original


def test_shortening_is_rejected_until_the_complete_assembly_fits(tmp_path):
    responses = Responses()
    compact_init = responses.parts["storage", "__init__"]
    first = compact_init.splitlines()[1]
    indentation = " " * (len(first) - len(first.lstrip()))
    long_init = compact_init + "\n" + (indentation + "self.db.commit()\n") * 80
    responses.parts["storage", "__init__"] = long_init
    repairs = []

    def transport(request):
        if request.url.path not in {"/apply-template", "/tokenize"}:
            prompt = json.loads(request.content)["messages"][-1]["content"]
            if "ASSEMBLY REPAIR:" in prompt:
                repairs.append(prompt)
                responses.parts["storage", "__init__"] = (
                    long_init if len(repairs) == 1 else compact_init
                )
        return responses(request)

    generator = Generator(settings_with_keys(), tmp_path, httpx.MockTransport(transport))
    try:
        output = generator.file("coder_1", "src/booking/storage.py", "Implement", inputs())
    finally:
        generator.close()
    assert len(repairs) == 2 and len(output.splitlines()) < 80
    assert "Previous output was rejected: SPEC kræver færre end 80 linjer" in repairs[1]
    attempts = [json.loads(path.read_text()) for path in (tmp_path / "logs").glob(
        "coder_1-__init__-*-attempt-*.json"
    )]
    assert len(attempts) == 3
    assert sum(item["status"] == "rejected" for item in attempts) == 1
