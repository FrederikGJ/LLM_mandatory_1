"""Received Windows cleanup failure, explicit connection close and final-check-only recovery."""

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from test_delivery_edits import DELIVERY, NoModelCalls, setup_delivery
from test_parts import settings_with_keys

from mh_toolchain import cli
from mh_toolchain import graph as graph_module
from mh_toolchain.config import WorkflowError
from mh_toolchain.files import DEPLOY_SMOKE
from mh_toolchain.graph import build_graph
from mh_toolchain.review_edits import stage_review_edits

FAILURE = json.loads((Path(__file__).parent / "fixtures/windows_deploy_failure.json").read_text())


@pytest.mark.parametrize("scenario", ["original", "fixed", "failed_health"])
def test_connections_close_before_restart_and_windows_style_cleanup(
    tmp_path, monkeypatch, scenario,
):
    """Emulate Windows deletion refusal with real SQLite handles and the received app."""
    demo = tmp_path / "demo"
    for name, text in DELIVERY["artifacts"].items():
        if name.startswith("src/"):
            path = demo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    monkeypatch.chdir(demo)
    monkeypatch.syspath_prepend(str(demo / "src"))
    monkeypatch.setenv("BOOKING_DB", ":memory:")
    saved_modules = {name: module for name, module in sys.modules.items()
                     if name == "booking" or name.startswith("booking.")}
    for name in saved_modules:
        del sys.modules[name]
    connections = []
    reconnect_checks = []
    cleanup_states = []
    folders = []
    original_connect = sqlite3.connect
    original_directory = tempfile.TemporaryDirectory

    class TrackedConnection(sqlite3.Connection):
        is_closed = False

        def close(self):
            super().close()
            self.is_closed = True

    def connect(database, *args, **kwargs):
        prior = [connection for path, connection in connections if path == str(database)]
        if str(database) != ":memory:" and prior:
            reconnect_checks.append(all(connection.is_closed for connection in prior))
        connection = original_connect(database, *args, **kwargs, factory=TrackedConnection)
        connections.append((str(database), connection))
        return connection

    class WindowsDirectory:
        def __init__(self, *args, **kwargs):
            assert not kwargs.get("ignore_cleanup_errors")
            self.directory = original_directory(*args, **kwargs)
            self.name = self.directory.name
            folders.append(self.name)

        def __enter__(self):
            return self.name

        def __exit__(self, *_args):
            try:
                cleanup_states.append([connection.is_closed for _path, connection in connections])
                active = [path for path, connection in connections
                          if path != ":memory:" and Path(path).parent == Path(self.name)
                          and not connection.is_closed]
                if active:
                    raise PermissionError(f"[WinError 32] database still open: {active[0]}")
            finally:
                # The fixture must clean up even when demonstrating the original defect.
                for _path, connection in connections:
                    connection.close()
                self.directory.cleanup()

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", WindowsDirectory)
    if scenario == "failed_health":
        original_get = TestClient.get

        def get(client, url, *args, **kwargs):
            if url == "/health":
                return httpx.Response(503, json={"status": "failed"})
            return original_get(client, url, *args, **kwargs)

        monkeypatch.setattr(TestClient, "get", get)
    try:
        if scenario == "original":
            with pytest.raises(PermissionError, match="WinError 32"):
                exec(FAILURE["result"]["command"][2], {})
            assert reconnect_checks == [False]
        elif scenario == "failed_health":
            with pytest.raises(AssertionError):
                exec(DEPLOY_SMOKE, {})
            assert reconnect_checks == []
        else:
            exec(DEPLOY_SMOKE, {})
            assert reconnect_checks == [True]
        if scenario != "original":
            assert cleanup_states and all(cleanup_states[0])
        assert connections and all(connection.is_closed for _path, connection in connections)
        assert folders and all(not Path(folder).exists() for folder in folders)
        assert not (demo / "booking.db").exists()
    finally:
        for name in list(sys.modules):
            if name == "booking" or name.startswith("booking."):
                del sys.modules[name]
        sys.modules.update(saved_modules)


def setup_failed_delivery(run, settings, monkeypatch):
    """Reconstruct the received failed final state; the first Windows result is replayed."""
    config, patch = setup_delivery(run, settings)

    def received_failure(run, name, _arguments, _timeout):
        assert name == "deployment", "No quality checks may run during final validation"
        (run / "logs/deployment.txt").write_text(FAILURE["log"], encoding="utf-8")
        return dict(FAILURE["result"])

    with monkeypatch.context() as before_fix:
        before_fix.setattr(graph_module, "execute", received_failure)
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, NoModelCalls(), saver)
            stage_review_edits(graph, run, config, patch)
            graph.invoke(None, config)
            result = graph.invoke(Command(resume=True), config)
            assert result["status"] == "failed" and result["approved"]
            assert graph.get_state(config).next == ()
    return config


def test_cli_retries_only_failed_final_check_with_real_received_source_and_preserved_evidence(
    tmp_path, monkeypatch, capsys,
):
    settings = settings_with_keys()
    run = tmp_path / "run-01"
    config = setup_failed_delivery(run, settings, monkeypatch)
    original_log = (run / "logs/deployment.txt").read_bytes()
    original_report = (run / "demo/reports/deployment.json").read_bytes()
    original_files = {path: path.read_bytes() for path in (run / "demo").rglob("*")
                      if path.is_file() and ".git" not in path.relative_to(run / "demo").parts
                      and path.name != "deployment.json"}
    original_logs = {path: path.read_bytes() for path in (run / "logs").iterdir()
                     if path.name != "deployment.txt"}
    original_config = (run / "configuration.json").read_bytes()
    monkeypatch.setattr(cli, "run_path", lambda _name: run)
    monkeypatch.setattr(cli, "Generator", lambda _settings, _run: NoModelCalls())
    capsys.readouterr()
    args = argparse.Namespace(command="retry-deploy", id="run-01")
    assert cli.run_command(args, settings) == 0
    output = capsys.readouterr().out
    assert "[trin] validate_deploy" in output and "[trin] review_delivery" not in output
    summary = json.loads((run / "summary.json").read_text())
    assert summary["status"] == "passed" and summary["quality"] == DELIVERY["quality"]
    assert summary["model_calls"] == 58 and summary["model_seconds"] == 3079.924
    assert summary["review_edits"] == 2 and summary["deployment_retries"] == 1
    assert (run / "logs/deployment-before-retry-001.txt").read_bytes() == original_log
    assert (run / "logs/deployment-before-retry-001.json").read_bytes() == original_report
    assert all(path.read_bytes() == raw for path, raw in original_files.items())
    assert all(path.read_bytes() == raw for path, raw in original_logs.items())
    assert (run / "configuration.json").read_bytes() == original_config
    result = json.loads((run / "demo/reports/deployment.json").read_text())
    assert result["passed"] and result["exit_code"] == 0
    assert result["container_build"] == "NOT_RUN" and result["command"][2] == DEPLOY_SMOKE
    assert (run / "logs/deployment.txt").read_text().startswith("PASS: import, health")
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, NoModelCalls(), saver)
        before = graph.get_state(config)
        with pytest.raises(WorkflowError, match="godkendt delivery"):
            cli.retry_deployment(graph, run, config)
        assert graph.get_state(config).config == before.config
    assert len(list((run / "logs").glob("deployment-retry-*.json"))) == 1


@pytest.mark.parametrize("problem", ["no_approval", "failed_quality", "source", "tests", "spec"])
def test_retry_rejects_missing_approval_failed_quality_or_changed_reviewed_files(
    tmp_path, monkeypatch, problem,
):
    settings = settings_with_keys()
    run = tmp_path / "run-01"
    config = setup_failed_delivery(run, settings, monkeypatch)
    with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
        graph = build_graph(settings, NoModelCalls(), saver)
        if problem == "no_approval":
            graph.update_state(config, {"approved": False}, as_node="validate_deploy")
        elif problem == "failed_quality":
            graph.update_state(config, {"quality": {"passed": False}}, as_node="validate_deploy")
        else:
            name = {"source": "src/booking/api.py", "tests": "tests/test_rooms.py",
                    "spec": "SPEC.md"}[problem]
            path = run / "demo" / name
            path.write_text(path.read_text() + "\n# unrelated edit\n")
        before = graph.get_state(config)
        log = (run / "logs/deployment.txt").read_bytes()
        report = (run / "demo/reports/deployment.json").read_bytes()
        with pytest.raises(WorkflowError):
            cli.retry_deployment(graph, run, config)
        assert graph.get_state(config).config == before.config
        assert (run / "logs/deployment.txt").read_bytes() == log
        assert (run / "demo/reports/deployment.json").read_bytes() == report
        assert not list((run / "logs").glob("deployment-retry-*.json"))
