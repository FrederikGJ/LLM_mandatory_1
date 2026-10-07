"""Narrow, auditable interface repairs; no model calls or source execution."""

import ast
import copy

MODEL_PATH = "src/booking/models.py"
BOOKING_CLASS = "class Booking(BookingCreate):\n    id: int\n"
MODEL_FIELDS = """class RoomCreate(BaseModel):
    name: str
    capacity: int = Field(ge=1)
class Room(RoomCreate):
    id: int
class BookingCreate(BaseModel):
    room_id: int
    title: str = Field(min_length=1)
    start: datetime
    end: datetime
"""


def repair_models_contract(content: str, path: str) -> tuple[str, list[dict]] | None:
    """Fix only the observed unused cls parameter and optional missing response class."""
    from .config import WorkflowError
    from .llm import clean_file, python_contract_issues, unfence

    if path != MODEL_PATH:
        return None
    source = unfence(content)
    if not source or len(source) > 30000 or "\x00" in source:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    issues = python_contract_issues(tree, path)
    signature_issue = (
        "validate_start_end: model_validator(mode='after') must be an instance "
        "method whose first argument is self and which returns self"
    )
    if signature_issue not in issues or set(issues) - {
        signature_issue, "Missing required top-level definitions: Booking",
    }:
        return None
    if any(not isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef))
           for node in tree.body):
        return None
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    if [node.name for node in classes] not in [
        ["RoomCreate", "Room", "BookingCreate"],
        ["RoomCreate", "Room", "BookingCreate", "Booking"],
    ]:
        return None
    expected_fields = ast.parse(MODEL_FIELDS)
    for actual, expected_class in zip(classes, expected_fields.body, strict=False):
        fields = copy.deepcopy(actual)
        fields.body = [node for node in fields.body if not isinstance(node, ast.FunctionDef)]
        if ast.dump(fields) != ast.dump(expected_class):
            return None
    validators = [node for node in classes[2].body if isinstance(node, ast.FunctionDef)]
    if len(validators) != 1:
        return None
    validator = validators[0]
    args = validator.args
    decorator = ast.parse("@model_validator(mode='after')\ndef f():\n    pass").body[0]
    assert isinstance(decorator, ast.FunctionDef)
    if (
        validator.name != "validate_start_end"
        or [node.arg for node in args.args] != ["cls", "self"]
        or args.posonlyargs or args.kwonlyargs or args.defaults or args.kw_defaults
        or args.vararg or args.kwarg
        or [ast.dump(node) for node in validator.decorator_list]
        != [ast.dump(node) for node in decorator.decorator_list]
        or any(isinstance(node, ast.Name) and node.id == "cls"
               for statement in validator.body for node in ast.walk(statement))
        or validator.body[0].lineno <= validator.lineno
    ):
        return None
    lines = source.splitlines()
    before = lines[validator.lineno - 1]
    if "#" in before or not before.strip().startswith("def validate_start_end(cls, self)"):
        return None
    indent = before[:len(before) - len(before.lstrip())]
    after = indent + 'def validate_start_end(self) -> "BookingCreate":'
    lines[validator.lineno - 1] = after
    changes = [{"operation": "apply contract instance-method signature",
                "before": before.strip(), "after": after.strip()}]
    # Check the entire AST against exactly these permitted changes. The model's
    # fields, imports, validator body, messages and all other statements survive.
    expected = copy.deepcopy(tree)
    target_class = next(node for node in expected.body
                        if isinstance(node, ast.ClassDef) and node.name == "BookingCreate")
    target = next(node for node in target_class.body if isinstance(node, ast.FunctionDef))
    target.args.args = [ast.arg(arg="self")]
    target.returns = ast.Constant(value="BookingCreate")
    repaired = "\n".join(lines) + "\n"
    if len(classes) == 3:
        repaired = repaired.rstrip() + "\n\n\n" + BOOKING_CLASS
        expected.body.extend(ast.parse(BOOKING_CLASS).body)
        changes.append({"operation": "append response class from fixed CONTRACT.md",
                        "after": BOOKING_CLASS, "origin": "toolchain; not model-generated"})
    try:
        repaired_tree = ast.parse(repaired)
    except SyntaxError:
        return None
    if ast.dump(repaired_tree) != ast.dump(expected):
        return None
    try:
        cleaned = clean_file(repaired, path)
    except WorkflowError:
        return None
    return cleaned, changes
