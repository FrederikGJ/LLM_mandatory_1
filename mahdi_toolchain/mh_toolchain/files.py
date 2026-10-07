"""Isolated demo repositories, reviewable diffs and fixed quality commands."""

import difflib
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import TOOLCHAIN, WorkflowError


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def safe_path(root: Path, name: str) -> Path:
    path = Path(name)
    if (
        path.is_absolute()
        or "\\" in name
        or ":" in name
        or ".." in path.parts
        or ".git" in path.parts
    ):
        raise WorkflowError(f"Ugyldig artefaktsti: {name}")
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise WorkflowError(f"Artefakt uden for demo-mappen: {name}")
    return target


def git(demo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(demo), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode:
        raise WorkflowError(f"git {arguments[0]} fejlede: {result.stderr.strip()}")
    return result.stdout.strip()


def init_run(run: Path, snapshot: dict) -> None:
    if shutil.which("git") is None:
        raise WorkflowError("Git mangler. Installér Git og åbn IntelliJ-terminalen igen.")
    run.mkdir(parents=True, exist_ok=False)
    demo = run / "demo"
    shutil.copytree(
        TOOLCHAIN / "template", demo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.in")
    )
    git(demo, "init", "--quiet", "--initial-branch=main")
    for key, value in {
        "user.name": "mahdi_toolchain",
        "user.email": "toolchain@localhost",
        "commit.gpgsign": "false",
        "core.hooksPath": ".git/hooks",
        "push.default": "nothing",
        "core.autocrlf": "false",
    }.items():
        git(demo, "config", key, value)
    commit(demo, "chore: read-only booking specification and acceptance suite")
    write_json(run / "configuration.json", snapshot)
    (run / "logs").mkdir()


def commit(demo: Path, message: str) -> None:
    if not (demo / ".git").is_dir() or git(demo, "remote"):
        raise WorkflowError("Demoen skal have sit eget git-repo uden remote.")
    git(demo, "add", "--all")
    if git(demo, "status", "--porcelain"):
        git(demo, "commit", "--quiet", "-m", message)


def review(run: Path, artifacts: dict[str, str], tag: str) -> Path:
    demo = run / "demo"
    chunks: list[str] = []
    for name, content in sorted(artifacts.items()):
        path = safe_path(demo, name)
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        chunks.extend(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{name}" if path.exists() else "/dev/null",
                tofile=f"b/{name}",
            )
        )
    diff = run / "logs" / f"{tag}.diff"
    diff.write_text("".join(chunks), encoding="utf-8")
    return diff


def apply(run: Path, artifacts: dict[str, str], message: str) -> None:
    for name, content in artifacts.items():
        target = safe_path(run / "demo", name)
        if target.suffix == ".py" and "src/" in name:
            if len(content.splitlines()) >= 80:
                raise WorkflowError(f"{name}: SPEC kræver færre end 80 linjer.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    commit(run / "demo", message)


def execute(run: Path, name: str, arguments: list[str], timeout: int) -> dict:
    import time

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "OPENAI_", "LANGSMITH_", "LANGCHAIN_", "HF_TOKEN"))
    }
    env.update(PYTHONPATH=str(run / "demo" / "src"), PYTHONIOENCODING="utf-8")
    started = time.monotonic()
    try:
        result = subprocess.run(
            arguments,
            cwd=run / "demo",
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        code, output = result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        code, output = 124, f"Kommandoen overskred {timeout} sekunder.\n"
    log = run / "logs" / f"{name}.txt"
    log.write_text(output, encoding="utf-8")
    return {
        "check": name,
        "command": arguments,
        "exit_code": code,
        "seconds": round(time.monotonic() - started, 3),
        "log": str(log),
    }


def quality(run: Path, round_number: int, timeout: int) -> dict:
    junit = run / "logs" / f"pytest-round-{round_number}.xml"
    commands = {
        "pytest": [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "acceptance",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit}",
        ],
        "ruff": [sys.executable, "-m", "ruff", "check", "src", "tests"],
        "mypy": [sys.executable, "-m", "mypy", "src"],
    }
    checks = [
        execute(run, f"{name}-round-{round_number}", args, timeout)
        for name, args in commands.items()
    ]
    tests = failures = errors = skipped = 0
    if junit.exists():
        suites = ET.parse(junit).getroot()
        for suite in suites.iter("testsuite"):
            tests += int(suite.get("tests", 0))
            failures += int(suite.get("failures", 0))
            errors += int(suite.get("errors", 0))
            skipped += int(suite.get("skipped", 0))
    passed = all(check["exit_code"] == 0 for check in checks) and tests > 0 and skipped == 0
    return {
        "round": round_number,
        "passed": passed,
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "checks": checks,
    }


def quality_markdown(result: dict) -> str:
    lines = [
        "# Quality report (generated by Python from executed checks)",
        "",
        f"- Overall: {'PASS' if result['passed'] else 'FAIL'}",
        f"- Tests: {result['tests']}; failures: {result['failures']}; "
        f"errors: {result['errors']}; skipped: {result['skipped']}",
        f"- Repair round: {result['round']}",
        "",
        "| Check | Exit code | Seconds | Raw output |",
        "|---|---|---|---|",
    ]
    for check in result["checks"]:
        lines.append(
            f"| {check['check']} | {check['exit_code']} | {check['seconds']} "
            f"| ../../logs/{Path(check['log']).name} |"
        )
    lines.extend(
        [
            "",
            "## Known limitations and risks",
            "- Small local models may fail to produce correct code even after repair.",
            "- Local single-instance SQLite demo; no authentication or load test.",
            "- Same-time requests and mixed timezone offsets need additional evaluation.",
            "- Human review precedes executing generated code; this is not a sandbox.",
            "- The generated Dockerfile has not been built by this workflow.",
            "- LLM test outputs are checked against a separate, read-only acceptance suite.",
        ]
    )
    return "\n".join(lines) + "\n"


def failure_context(result: dict) -> str:
    parts = []
    for check in result["checks"]:
        if check["exit_code"]:
            raw = Path(check["log"]).read_text(encoding="utf-8")
            if len(raw) > 2400:
                raw = (
                    raw[:1200]
                    + f"\n[{len(raw) - 2400} characters omitted; see raw log]\n"
                    + raw[-1200:]
                )
            parts.append(f"{check['check']} exit={check['exit_code']}\n{raw}")
    return "\n".join(parts)


DEPLOY_SMOKE = """import os
import sqlite3
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory() as folder:
    connections = []
    original_connect = sqlite3.connect
    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection
    sqlite3.connect = tracked_connect
    try:
        os.environ["BOOKING_DB"] = ":memory:"
        from booking.api import create_app
        db = str(Path(folder) / "booking.db")
        with TestClient(create_app(db)) as client:
            response = client.get("/health")
            assert response.status_code == 200 and response.json() == {"status": "ok"}
            room = client.post("/rooms", json={"name": "Deploy", "capacity": 2})
            assert room.status_code == 201
            saved_room = room.json()
        for connection in connections:
            connection.close()
        connections.clear()
        assert Path(db).is_file()
        with TestClient(create_app(db)) as second:
            response = second.get("/rooms")
            assert response.status_code == 200 and response.json() == [saved_room]
    finally:
        sqlite3.connect = original_connect
        for connection in connections:
            connection.close()
print("PASS: import, health, SQLite creation and persistence across app restart")
"""
