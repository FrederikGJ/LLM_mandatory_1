"""Fixed scaffolds; storage, handler and test bodies come from local model calls."""

import ast
import hashlib
import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .config import WorkflowError
from .files import write_json
from .llm import clean_file, ruff_source, storage_function_issues, test_http_issues, unfence

if TYPE_CHECKING:
    from .llm import Generator

STORAGE_IMPORTS = (
    "import sqlite3\n\n"
    "from booking.models import Booking, BookingCreate, Room, RoomCreate\n"
)
STORAGE_PREAMBLE = STORAGE_IMPORTS + (
    "\n\nclass NotFoundError(Exception):\n    pass\n\n\n"
    "class ConflictError(Exception):\n    pass\n\n\nclass Storage:\n"
)
API_IMPORTS = (
    "import os\n\n"
    "from fastapi import FastAPI, Request, Response\n"
    "from fastapi.responses import JSONResponse\n\n"
    "from booking.models import Booking, BookingCreate, Room, RoomCreate\n"
    "from booking.storage import ConflictError, NotFoundError, Storage\n"
)
TEST_PREAMBLE = (
    "import pytest\nfrom fastapi.testclient import TestClient\n\n"
    "from booking.api import create_app\n\n\n"
    "@pytest.fixture\ndef client():\n"
    "    with TestClient(create_app(':memory:')) as http_client:\n"
    "        yield http_client\n\n\n"
    "def _room(name='A', capacity=4):\n"
    "    return {'name': name, 'capacity': capacity}\n"
)
BOOKING_TEST_PREAMBLE = TEST_PREAMBLE + (
    "\n\ndef _booking(room_id, start='10:00:00', end='11:00:00', title='Meeting'):\n"
    "    return {'room_id': room_id, 'title': title,\n"
    "            'start': '2026-01-01T' + start, 'end': '2026-01-01T' + end}\n"
)
TEST_INTERFACES = (
    "The application is already implemented. Importing booking.api.create_app is supplied. "
    "The supplied pytest client fixture opens TestClient(create_app(':memory:')) separately "
    "for EVERY test; no data survives between tests. Available client.get/post/delete and "
    "_room(name='A', capacity=4) returning a room JSON dictionary. Booking tests also have "
    "_booking(room_id, start='10:00:00', end='11:00:00', title='Meeting') returning valid "
    "JSON with ISO timestamps on 2026-01-01. Use these helpers as JSON payloads, not models. "
    "Response fields: Room id/name/capacity; Booking id/room_id/title/start/end. "
    "Make real HTTP requests and assert results. No application implementations or mocks."
)
INTERFACES = (
    "Explicit interface excerpt; full SPEC/CONTRACT remain in the workflow. "
    "Do not copy/redefine their classes. RoomCreate has name: str, capacity: int >= 1. "
    "Room adds id: int. BookingCreate has room_id: int, title: str, start/end: datetime "
    "and validates start < end. Booking adds id: int; it has NO capacity field. "
    "Pydantic v2: use model_dump(), not dict(). "
    "Storage has __init__, create_room, get_room, list_rooms, create_booking, "
    "list_bookings, delete_booking. Its SQLite connection is ALWAYS self.db. "
    "Errors are NotFoundError and ConflictError. Return model instances, not JSON strings."
)


@dataclass(frozen=True)
class Part:
    name: str
    header: str
    target_lines: int
    task: str
    decorator: str = ""


STORAGE = (
    Part("__init__", "def __init__(self, db_path: str) -> None:", 10,
         "Assign self.db = sqlite3.connect(db_path, check_same_thread=False). "
         "Set self.db.row_factory = sqlite3.Row. Use one executescript call with compact SQL "
         "to CREATE TABLE IF NOT EXISTS rooms(id INTEGER PRIMARY KEY, name TEXT UNIQUE, "
         "capacity INTEGER) and bookings(id INTEGER PRIMARY KEY, room_id INTEGER, title TEXT, "
         "start TEXT, end TEXT). Commit. No separate create_tables helper."),
    Part("create_room", "def create_room(self, data: RoomCreate) -> Room:", 10,
         "Insert name/capacity into rooms using self.db.execute and SQL parameters. "
         "Commit on success. Catch only sqlite3.IntegrityError and raise ConflictError "
         "from that error. Return self.get_room(int(cursor.lastrowid or 0))."),
    Part("get_room", "def get_room(self, room_id: int) -> Room:", 6,
         "Select the room by id with a parameter and fetchone. If row is None, raise "
         "NotFoundError('room not found'). Return Room(**dict(row))."),
    Part("list_rooms", "def list_rooms(self) -> list[Room]:", 3,
         "Read all rooms ordered by id. Return a list of Room(**dict(row)) from that query."),
    Part("create_booking", "def create_booking(self, data: BookingCreate) -> Booking:", 13,
         "First self.get_room(data.room_id) to enforce room existence. Convert start/end "
         "to ISO strings. Query the same room for overlap: room_id = ? AND start < ? AND "
         "end > ?, with parameters (data.room_id, end, start). If fetchone is not None, "
         "raise ConflictError('overlap'). Insert room_id/title/start/end with parameters, "
         "commit and return Booking(id=int(cursor.lastrowid or 0), **data.model_dump()). "
         "Back-to-back bookings are allowed. No capacity lookup, no other helper."),
    Part("list_bookings", "def list_bookings(self, room_id: int) -> list[Booking]:", 5,
         "Call self.get_room(room_id) first. Select this room's bookings ORDER BY start. "
         "Return a list of Booking(**dict(row))."),
    Part("delete_booking", "def delete_booking(self, booking_id: int) -> None:", 6,
         "Delete using the booking id parameter, commit, and if cursor.rowcount == 0 "
         "raise NotFoundError('booking not found'). Do not silently accept a missing id."),
)
API = (
    Part("missing", "async def missing(_request: Request, error: NotFoundError) -> JSONResponse:",
         3,
         "Return JSONResponse(status_code=404, content={'detail': str(error)}). "
         "Use str(error); the exception has no detail attribute.",
         "@app.exception_handler(NotFoundError)"),
    Part("conflict", "async def conflict(_request: Request, error: ConflictError) -> JSONResponse:",
         3,
         "Return JSONResponse(status_code=409, content={'detail': str(error)}).",
         "@app.exception_handler(ConflictError)"),
    Part("health", "def health() -> dict[str, str]:", 3,
         "Return the JSON dictionary {'status': 'ok'}.", "@app.get('/health')"),
    Part("create_room", "def create_room(data: RoomCreate) -> Room:", 3,
         "Return storage.create_room(data). FastAPI serializes the model and handlers map errors.",
         "@app.post('/rooms', status_code=201)"),
    Part("list_rooms", "def list_rooms() -> list[Room]:", 3,
         "Return storage.list_rooms(). FastAPI serializes its models.", "@app.get('/rooms')"),
    Part("create_booking", "def create_booking(data: BookingCreate) -> Booking:", 3,
         "Return storage.create_booking(data). FastAPI serializes datetime/model values. "
         "Do not put raw Pydantic models or datetime values into JSONResponse.",
         "@app.post('/bookings', status_code=201)"),
    Part("list_bookings", "def list_bookings(room_id: int) -> list[Booking]:", 3,
         "Return storage.list_bookings(room_id). Errors use the registered exception handlers.",
         "@app.get('/rooms/{room_id}/bookings')"),
    Part("delete_booking", "def delete_booking(booking_id: int) -> Response:", 4,
         "Call storage.delete_booking(booking_id), then return Response(status_code=204).",
         "@app.delete('/bookings/{booking_id}', status_code=204)"),
)
ROOM_TESTS = (
    Part("test_health_and_rooms", "def test_health_and_rooms(client):", 9,
         "GET /health must be 200 with {'status': 'ok'}. POST /rooms json=_room() must be "
         "201. Save its JSON, then GET /rooms must be 200 and contain exactly that room."),
    Part("test_duplicate_room_name", "def test_duplicate_room_name(client):", 5,
         "In this fresh client, POST /rooms json=_room() must be 201. Repeat the identical "
         "request and assert 409. Do not assert the exact error message."),
    Part("test_room_capacity", "def test_room_capacity(client):", 4,
         "POST /rooms json=_room(capacity=0) must be 422. Then POST /rooms "
         "json=_room(capacity=1) must be 201."),
    Part("test_unknown_room_bookings", "def test_unknown_room_bookings(client):", 3,
         "The database is empty. GET /rooms/999/bookings must be 404. "
         "Do not assume a room was created by another test."),
)
BOOKING_TESTS = (
    Part("test_bookings_sorted", "def test_bookings_sorted(client):", 14,
         "Create your own room using POST /rooms json=_room(), assert 201 and save id from "
         "response.json()['id']. POST /bookings with _booking(id, '12:00:00', '13:00:00') "
         "then _booking(id, '09:00:00', '10:00:00'), both 201. GET this room's bookings, "
         "assert 200, exactly two rows and their start values are sorted."),
    Part("test_overlap_and_back_to_back", "def test_overlap_and_back_to_back(client):", 11,
         "Create your own room with POST /rooms json=_room(), assert 201, save its id. "
         "POST /bookings json=_booking(id) must be 201. POST with "
         "_booking(id, '10:30:00', '11:30:00') must be 409. POST with "
         "_booking(id, '11:00:00', '12:00:00') must be 201: back-to-back is allowed."),
    Part("test_invalid_booking_and_missing_room",
         "def test_invalid_booking_and_missing_room(client):", 11,
         "Create your own room with POST /rooms json=_room(), assert 201, save its id. "
         "POST /bookings with _booking(id, '11:00:00', '10:00:00') must be 422. "
         "POST with _booking(id, title='') must be 422. POST with _booking(999) must be "
         "404. Use valid dates for the unknown-room case. No exact error message assertions."),
    Part("test_cancel_booking", "def test_cancel_booking(client):", 13,
         "Create your own room with POST /rooms json=_room(), assert 201, save its id. "
         "POST /bookings json=_booking(id), assert 201 and save booking id. DELETE that "
         "booking must be 204 with empty body. GET the room's bookings must be 200 and "
         "an empty list. DELETE the same booking again must be 404."),
)
TEST_PARTS = {
    "tests/test_rooms.py": ("tests/test_rooms", ROOM_TESTS, TEST_PREAMBLE),
    "tests/test_bookings.py": ("tests/test_bookings", BOOKING_TESTS, BOOKING_TEST_PREAMBLE),
}


def string_continuations(tree: ast.AST) -> set[int]:
    rows: set[int] = set()
    for item in ast.walk(tree):
        if isinstance(item, ast.JoinedStr) or (
            isinstance(item, ast.Constant) and isinstance(item.value, (str, bytes))
        ):
            rows.update(range(item.lineno + 1, (item.end_lineno or item.lineno) + 1))
    return rows


def indent_function(code: str) -> str:
    """Move a function into its scope without changing multiline literal contents."""
    protected = string_continuations(ast.parse(code))
    return "\n".join(
        ("    " + line if line.strip() and number not in protected else line)
        for number, line in enumerate(code.splitlines(), 1)
    )


def split_literals(code: str) -> str:
    """Use adjacent literals for long strings, preserving every character, including SQL."""
    tree = ast.parse(code)
    # Constants inside an f-string are not standalone string literals.
    embedded = {id(child) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
                for child in ast.walk(node)}
    source = code.encode("utf-8")
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    replacements = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in embedded or ("\n" not in node.value and len(repr(node.value)) <= 80):
            continue
        chunks, current = [], ""
        for character in node.value:
            if current and len(repr(current + character)) > 80:
                chunks.append(repr(current))
                current = ""
            current += character
        chunks.append(repr(current))
        literal = chunks[0] if len(chunks) == 1 else "(\n" + "\n".join(chunks) + "\n)"
        assert node.end_lineno is not None and node.end_col_offset is not None
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        replacements.append((start, end, literal.encode("utf-8")))
    for start, end, encoded_literal in sorted(replacements, reverse=True):
        source = source[:start] + encoded_literal + source[end:]
    return source.decode("utf-8")


def fold_continuations(code: str) -> str:
    """Fold layout inside brackets only; never join statements, comments or literal lines."""
    tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
    joins: set[int] = set()
    protected: set[int] = set()
    depth = 0
    for token in tokens:
        if token.type == tokenize.OP:
            if token.string in {"(", "[", "{"}:
                depth += 1
            elif token.string in {")", "]", "}"}:
                depth -= 1
        if token.type == tokenize.NL and depth > 0:
            joins.add(token.start[0])
        if token.type == tokenize.COMMENT or token.start[0] != token.end[0]:
            protected.update(range(token.start[0], token.end[0] + 1))
    # Python 3.12 tokenizes f-strings into several tokens. Keep their layout intact.
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.JoinedStr):
            protected.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    output: list[str] = []
    previous = 0
    for number, line in enumerate(code.splitlines(), 1):
        if output and previous in joins and not {previous, number} & protected:
            left, right = output[-1].rstrip(), line.lstrip()
            separator = "" if left.endswith(("(", "[", "{")) or right.startswith(
                (")", "]", "}")
            ) else " "
            merged = left + separator + right
            if len(merged) <= 96:
                output[-1] = merged
                previous = number
                continue
        output.append(line)
        previous = number
    result = "\n".join(output) + "\n"
    return result if ast.dump(ast.parse(result)) == ast.dump(ast.parse(code)) else code


def compact_function(code: str) -> str:
    """Choose a shorter equivalent layout; function line budgets are prompt targets only."""
    expected = ast.dump(ast.parse(code), include_attributes=False)
    candidates = [code]
    layouts = [code]
    # Keep explanatory comments and type/quality directives in the original layout.
    if not any(token.type == tokenize.COMMENT for token in tokenize.generate_tokens(
        io.StringIO(code).readline
    )):
        layouts.append(ast.unparse(ast.parse(code)))
    for layout in dict.fromkeys(layouts):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "ruff", "format", "--isolated", "--target-version",
                 "py312", "--line-length", "96", "--stdin-filename", "function.py", "-"],
                input=split_literals(layout), encoding="utf-8", capture_output=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise WorkflowError("Funktionsformateringen kunne ikke gennemføres.") from error
        if result.returncode != 0:
            continue
        try:
            equivalent = ast.dump(ast.parse(result.stdout), include_attributes=False) == expected
        except SyntaxError:
            equivalent = False
        if equivalent:
            candidates.append(result.stdout)
    candidates += [fold_continuations(candidate) for candidate in candidates.copy()]
    # Four additional columns are needed when the function is nested in the file.
    # Long lines are a formatting preference here; the actual quality gate still runs.
    def score(candidate: str) -> tuple[int, int, int]:
        lines = candidate.splitlines()
        return (sum(len(line) > 96 for line in lines), len(lines), max(map(len, lines)))

    return min(candidates, key=score).rstrip() + "\n"


def validate_part(content: str, part: Part, storage: bool, testing: bool = False) -> str:
    """Extract one matching function; keep its body and apply the fixed interface header."""
    raw = unfence(content)
    if not raw or len(raw) > 12000 or "\x00" in raw:
        raise WorkflowError("Tomt eller ugyldigt funktionsoutput.")
    try:
        tree = ast.parse(raw)
    except SyntaxError as error:
        raise WorkflowError(f"Return one valid function beginning with: {part.header}") from error
    matches = [
        item for item in ast.walk(tree)
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == part.name
    ]
    if len(matches) != 1:
        raise WorkflowError(f"Return exactly the function {part.name}, not the complete module.")
    function = matches[0]
    expected = ast.parse(part.header + "\n    pass").body[0]
    assert isinstance(expected, (ast.FunctionDef, ast.AsyncFunctionDef))
    def signature(args):
        return ([arg.arg for arg in args.args], args.vararg, args.kwarg,
                [arg.arg for arg in args.kwonlyargs], args.posonlyargs)

    if signature(function.args) != signature(expected.args):
        raise WorkflowError(f"Keep the exact parameter names in: {part.header}")
    if any(isinstance(item, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef))
           for statement in function.body for item in ast.walk(statement)):
        raise WorkflowError("Use the available imports and variables; no imports/classes/globals.")
    if storage and any(
        isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name)
        and item.value.id == "self" and item.attr not in {"db", *(p.name for p in STORAGE)}
        for statement in function.body for item in ast.walk(statement)
    ):
        raise WorkflowError("Use self.db and the seven contract methods; no invented helpers.")
    if storage and (issues := storage_function_issues(function)):
        raise WorkflowError("; ".join(issues))
    if testing:
        if issues := test_http_issues(function):
            raise WorkflowError("; ".join(issues))
        assertions = [node for node in ast.walk(function) if isinstance(node, ast.Assert)]
        if not assertions or all(isinstance(node.test, ast.Constant) for node in assertions):
            raise WorkflowError(
                "Return a real test with assertions on HTTP results, not assert True."
            )
        if not any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "client"
            and node.func.attr in {"get", "post", "delete", "request"}
            for node in ast.walk(function)
        ):
            raise WorkflowError("The test must make real HTTP requests using the supplied client.")
    first, last = function.body[0].lineno, function.end_lineno
    assert last is not None
    lines = raw.splitlines()
    while first > function.lineno + 1 and (
        not lines[first - 2].strip() or lines[first - 2].lstrip().startswith("#")
    ):
        first -= 1
    protected = string_continuations(function)
    column = function.body[0].col_offset
    body = "\n".join(
        line if number in protected or not line.strip() else "    " + line[column:]
        for number, line in enumerate(lines[first - 1:last], first)
    )
    code = part.header + "\n" + body + "\n"
    code = clean_file(code, "fragments/function.py")
    canonical = ast.parse(code).body[0]
    assert isinstance(canonical, (ast.FunctionDef, ast.AsyncFunctionDef))
    if ast.dump(ast.Module(body=function.body, type_ignores=[])) != ast.dump(
        ast.Module(body=canonical.body, type_ignores=[])
    ):
        raise WorkflowError(
            "Use consistent indentation; assembling must preserve the function body."
        )
    code = compact_function(code)
    if testing:
        preamble = TEST_PREAMBLE if part in ROOM_TESTS else BOOKING_TEST_PREAMBLE
        wrapper = preamble + "\n\n" + code
    else:
        wrapper = (
            STORAGE_PREAMBLE if storage else
            API_IMPORTS + "\ndef create_app(db_path: str) -> FastAPI:\n"
            "    app = FastAPI()\n    storage = Storage(db_path)\n"
        ) + indent_function(code) + "\n"
    if testing:
        ruff_source(wrapper, "fragments/function.py", lint_rules="F")
    else:
        ruff_source(wrapper, "fragments/function.py")
    return code


def generate_parts(
    generator: "Generator", role: str, path: str, task: str, context: dict[str, str], stem: str,
) -> str:
    storage = path.endswith("storage.py")
    testing = path in TEST_PARTS
    parts: tuple[Part, ...] = STORAGE if storage else API
    kind = "storage" if storage else "api"
    test_preamble = ""
    if testing:
        kind, parts, test_preamble = TEST_PARTS[path]
    context_hash = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    logs = generator.run / "logs"
    rejected_path = logs / f"{stem}.assembly-rejected.json"
    if rejected_path.exists():
        rejected_bytes = rejected_path.read_bytes()
        try:
            rejected = json.loads(rejected_bytes)
            assembly = rejected["assembly"]
            tree = ast.parse(rejected["content"])
            valid_sources = (
                rejected["status"] == "rejected"
                and assembly["role"] == role and assembly["path"] == path
                and assembly["full_context_sha256"] == context_hash
                and [item["function"] for item in assembly["functions"]]
                == [part.name for part in parts]
            )
            for item in assembly["functions"]:
                cache_name = item["cache"]
                if Path(cache_name).name != cache_name:
                    valid_sources = False
                    break
                cached = json.loads((logs / cache_name).read_text(encoding="utf-8"))
                valid_sources = valid_sources and item["content_sha256"] == hashlib.sha256(
                    cached["content"].encode()
                ).hexdigest()
                definitions = (ast.FunctionDef, ast.AsyncFunctionDef)
                original = [node for node in ast.walk(ast.parse(cached["content"]))
                            if isinstance(node, definitions) and node.name == item["function"]]
                assembled = [node for node in ast.walk(tree)
                             if isinstance(node, definitions) and node.name == item["function"]]
                valid_sources = valid_sources and len(original) == len(assembled) == 1
                if valid_sources:
                    valid_sources = ast.dump(ast.Module(
                        body=original[0].body, type_ignores=[],
                    )) == ast.dump(ast.Module(body=assembled[0].body, type_ignores=[]))
            recovered = clean_file(rejected["content"], path) if valid_sources else None
        except (KeyError, TypeError, ValueError, SyntaxError, OSError, WorkflowError):
            recovered = None
        if recovered is not None:
            record = {
                "content": recovered,
                "assembly": {
                    **assembly,
                    "strategy": (
                        "model-generated tests with fixed client/data fixtures v1" if testing
                        else "model-generated functions with fixed interface scaffold v1"
                    ),
                    "line_count": len(recovered.splitlines()),
                    "full_input_task": task, "full_input_context": context,
                    "review_and_quality": "still required by the workflow",
                    "recovery": {
                        "source": rejected_path.name,
                        "source_sha256": hashlib.sha256(rejected_bytes).hexdigest(),
                        "raw_lines": len(rejected["content"].splitlines()),
                        "method": "static revalidation and blank-line normalization",
                    },
                },
            }
            write_json(logs / f"{stem}.assembly-recovered.json", record)
            write_json(logs / f"{stem}.assembly.json", record)
            write_json(logs / f"{stem}.cache.json", record)
            print(f"  {role}: {path} genbrugt fra gemt samling "
                  f"({len(recovered.splitlines())} linjer; intet nyt modelkald)", flush=True)
            return recovered
    inputs = {"interface-excerpt": TEST_INTERFACES if testing else INTERFACES,
              "full-context-sha256": context_hash}
    if "quality-errors.txt" in context:
        inputs["quality-errors.txt"] = context["quality-errors.txt"]
    functions: list[str] = []
    sources: list[dict] = []
    requests: list[tuple[str, str]] = []
    for part in parts:
        part_path = f"fragments/{kind}/{part.name}.py"
        # Preserve v4 request fingerprints, including its compactness targets, so saved
        # replies can be revalidated. Only the assembled file has a hard line limit.
        instruction = (
            f"Target: {path}; function {part.name} only. Return the complete function starting "
            f"with exactly this header:\n{part.header}\n"
            f"Implementation: {part.task}\n"
            f"Maximum {part.target_lines} lines including the header; compact SQL and calls. "
            "Each line must be at most 96 characters. No imports, decorators, classes, "
            "helper functions or explanations. The toolchain supplies them and assembles the file. "
            + ("Available: client and _room; booking tests also have _booking. "
               "Return only this test; no fixtures, setup, models or application code. " if testing
               else "Available: self.db, sqlite3, Room/RoomCreate/Booking/BookingCreate, "
               "NotFoundError, ConflictError. " if storage else
               "This function will be nested INSIDE create_app. Available closure: app, storage; "
               "imports: Request, Response, JSONResponse and the contract types. ")
            + "Follow the exact parameter names. The body must be fully implemented."
        )
        def validate(content: str, _path: str, selected: Part = part) -> str:
            return validate_part(content, selected, storage, testing)

        code = generator.file(
            role, part_path, instruction, inputs,
            validator=validate,
        )
        requests.append((part_path, instruction))
        digest = hashlib.sha256(json.dumps(
            [role, part_path, instruction, inputs, generator.settings.for_role(role).public()],
            sort_keys=True,
        ).encode()).hexdigest()[:20]
        sources.append({
            "function": part.name, "cache": f"{role}-{part.name}-{digest}.cache.json",
            "content_sha256": hashlib.sha256(code.encode()).hexdigest(),
        })
        functions.append((part.decorator + "\n" if part.decorator else "") + code.rstrip())
    def assemble(selected_functions: list[str] | None = None) -> str:
        selected = functions if selected_functions is None else selected_functions
        if testing:
            return test_preamble.rstrip() + "\n\n\n" + "\n\n\n".join(selected) + "\n"
        body = indent_function("\n\n".join(selected))
        if storage:
            return STORAGE_PREAMBLE + body + "\n"
        return API_IMPORTS + (
            "\n\ndef create_app(db_path: str) -> FastAPI:\n"
            "    app = FastAPI()\n    storage = Storage(db_path)\n\n"
        ) + body + (
            "\n\n    return app\n\n\n"
            "app = create_app(os.environ.get('BOOKING_DB', 'booking.db'))\n"
        )

    retries = int(generator.settings.workflow["format_retries"])
    for assembly_round in range(retries + 1):
        text = assemble()
        try:
            text = clean_file(text, path)
            break
        except WorkflowError as error:
            rejected = {
                "content": text, "status": "rejected", "error": str(error),
                "assembly": {"role": role, "path": path, "functions": sources,
                             "full_context_sha256": context_hash, "round": assembly_round},
            }
            write_json(generator.run / "logs" / f"{stem}.assembly-rejected.json", rejected)
            write_json(generator.run / "logs" / f"{stem}.assembly-rejected-{assembly_round}.json",
                       rejected)
            match = re.search(r"\((\d+) linjer;", str(error))
            if assembly_round == retries or not match or not str(error).startswith(
                "SPEC kræver færre end 80 linjer"
            ):
                raise
            needed = int(match.group(1)) - 79
        # A large but valid model reply can still overflow the assembled file. Ask for
        # one shorter body instead of repeatedly stopping with the same cached result.
        chosen = max(range(len(functions)), key=lambda index: len(functions[index].splitlines()))
        part = parts[chosen]
        current = functions[chosen].removeprefix(part.decorator + "\n" if part.decorator else "")
        goal = max(2, len(current.splitlines()) - needed)
        part_path, instruction = requests[chosen]
        repair_task = instruction + (
            f"\nASSEMBLY REPAIR: {path} is {needed} lines above its 79-line limit. "
            f"Return only {part.name}, aiming for at most {goal} lines including its header. "
            "Preserve its behavior, validation and SQL parameterization. Simplify layout or "
            "redundant work; do not omit features, use placeholders or invent helpers."
        )
        repair_inputs = {**inputs, "current-function.py": current,
                         "assembly-errors.txt": f"Reduce {path} by at least {needed} lines."}

        def validate_repair(
            content: str, _path: str, selected: Part = part, selected_index: int = chosen,
        ) -> str:
            code = validate_part(content, selected, storage, testing)
            proposed = functions.copy()
            proposed[selected_index] = (
                selected.decorator + "\n" if selected.decorator else ""
            ) + code.rstrip()
            # Accept a shortening only when the complete assembled file also passes.
            # Otherwise Generator supplies the actual failure to its bounded retry.
            clean_file(assemble(proposed), path)
            return code

        print(f"  {role}: samlingen er for lang; beder modellen forkorte {part.name}", flush=True)
        code = generator.file(role, part_path, repair_task, repair_inputs,
                              validator=validate_repair)
        functions[chosen] = (part.decorator + "\n" if part.decorator else "") + code.rstrip()
        digest = hashlib.sha256(json.dumps(
            [role, part_path, repair_task, repair_inputs,
             generator.settings.for_role(role).public()],
            sort_keys=True,
        ).encode()).hexdigest()[:20]
        sources[chosen] = {
            "function": part.name, "cache": f"{role}-{part.name}-{digest}.cache.json",
            "content_sha256": hashlib.sha256(code.encode()).hexdigest(),
            "assembly_repair": {"round": assembly_round + 1, "target_lines": goal},
        }
    record = {
        "content": text, "assembly": {
            "strategy": "model-generated tests with fixed client/data fixtures v1" if testing
            else "model-generated functions with fixed interface scaffold v1",
            "role": role, "path": path, "functions": sources,
            "line_count": len(text.splitlines()), "full_context_sha256": context_hash,
            "full_input_task": task, "full_input_context": context,
            "review_and_quality": "still required by the workflow",
        },
    }
    write_json(generator.run / "logs" / f"{stem}.assembly.json", record)
    write_json(generator.run / "logs" / f"{stem}.cache.json", record)
    print(f"  {role}: {path} samlet fra {len(parts)} modelsvar ({len(text.splitlines())} linjer)",
          flush=True)
    return text
