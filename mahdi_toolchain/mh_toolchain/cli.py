"""CLI commands for starting, reviewing, resuming and comparing persisted workflows."""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .config import TOOLCHAIN, WorkflowError, load_settings
from .files import DEPLOY_SMOKE, git, init_run, safe_path, write_json
from .graph import build_graph
from .llm import Generator, check_endpoints


def run_path(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,70}", run_id):
        raise WorkflowError("Kørselsnavn må kun indeholde bogstaver, tal, _ og -.")
    return TOOLCHAIN / "runs" / run_id


def summarize(run: Path, snapshot) -> dict:
    state = snapshot.values
    pending = [item.value for task in snapshot.tasks for item in task.interrupts]
    logs = []
    for path in sorted((run / "logs").glob("*-attempt-*.json")):
        logs.append(json.loads(path.read_text(encoding="utf-8")))
    errors = [str(task.error) for task in snapshot.tasks if task.error]
    summary = {
        "run_id": run.name,
        "status": "needs_review"
        if pending
        else "error"
        if errors
        else state.get("status", "running"),
        "errors": errors,
        "next": list(snapshot.next),
        "pending": pending,
        "round": state.get("round", 0),
        "quality": state.get("quality"),
        "model_calls": len(logs),
        "rejected_outputs": sum(item.get("status") == "rejected" for item in logs),
        "model_seconds": round(sum(item.get("seconds", 0) for item in logs), 3),
        "endpoints_used": sorted({item["endpoint"] for item in logs}),
        "roles_used": sorted({item["role"] for item in logs}),
        "files": git(run / "demo", "ls-files").splitlines(),
        "commits": int(git(run / "demo", "rev-list", "--count", "HEAD")),
        "review_edits": len(list((run / "logs").glob("review-edits-*.json"))),
        "deployment_retries": len(list((run / "logs").glob("deployment-retry-*.json"))),
        "contract_repairs": len(list((run / "logs").glob("*.contract-repair-*.json"))),
    }
    write_json(run / "summary.json", summary)
    lines = [
        f"# Run {run.name}",
        "",
        f"- Status: {summary['status']}",
        f"- Model calls: {summary['model_calls']}",
        f"- Model time: {summary['model_seconds']} s",
        f"- Toolchain contract repairs (separate from model calls): {summary['contract_repairs']}",
        f"- Endpoints used: {', '.join(summary['endpoints_used'])}",
        f"- Repair round: {summary['round']}",
        f"- Commits: {summary['commits']}",
    ]
    for item in pending:
        lines.extend(
            [
                "",
                f"## Review {item['stage']}",
                f"- Diff: {item['diff']}",
                f"- Actions: {item['actions']}",
            ]
        )
    (run / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def retry_deployment(graph, run: Path, config: dict) -> None:
    """Retry only an already approved, failed final check; archive its original evidence."""
    snapshot = graph.get_state(config)
    state = snapshot.values
    quality = state.get("quality")
    if snapshot.next or any(task.interrupts for task in snapshot.tasks) or (
        state.get("status") != "failed" or state.get("approved") is not True
        or not isinstance(quality, dict) or quality.get("passed") is not True
    ):
        raise WorkflowError("retry-deploy kræver godkendt delivery og en fejlet driftskontrol.")
    report_path = run / "demo/reports/deployment.json"
    raw_report = report_path.read_bytes()
    report = json.loads(raw_report.decode("utf-8-sig"))
    if not isinstance(report, dict) or report.get("check") != "deployment" or (
        report.get("passed") is not False
    ) or (
        report.get("exit_code", 0) == 0
    ):
        raise WorkflowError("Det gemte deployment-resultat dokumenterer ikke en fejlet kontrol.")
    for name, content in state["artifacts"].items():
        path = safe_path(run / "demo", name)
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            raise WorkflowError(f"Godkendt fil er ændret: {name}. Intet er gentaget.")
    if git(run / "demo", "status", "--porcelain"):
        raise WorkflowError("Demoens filer er ændret efter godkendelsen. Intet er gentaget.")
    raw_log = (run / "logs/deployment.txt").read_bytes()
    previous = list((run / "logs").glob("deployment-retry-*.json"))
    number = max((int(path.stem.rsplit("-", 1)[1]) for path in previous), default=0) + 1
    prefix = f"deployment-before-retry-{number:03d}"
    (run / "logs" / f"{prefix}.json").write_bytes(raw_report)
    (run / "logs" / f"{prefix}.txt").write_bytes(raw_log)
    record_path = run / "logs" / f"deployment-retry-{number:03d}.json"
    record = {
        "kind": "local runtime validation retry; not a model response",
        "created_utc": datetime.now(UTC).isoformat(),
        "previous_checkpoint_id": snapshot.config["configurable"].get("checkpoint_id"),
        "previous_report": f"{prefix}.json", "previous_log": f"{prefix}.txt",
        "previous_report_sha256": hashlib.sha256(raw_report).hexdigest(),
        "previous_log_sha256": hashlib.sha256(raw_log).hexdigest(),
        "new_program_sha256": hashlib.sha256(DEPLOY_SMOKE.encode("utf-8")).hexdigest(),
        "quality": state["quality"],
        "approval": "existing delivery approval retained; artifacts unchanged",
        "checkpoint_updated": False,
    }
    write_json(record_path, record)
    updated = graph.update_state(config, {"status": "running"}, as_node="review_delivery")
    record["checkpoint_updated"] = True
    record["checkpoint_id"] = updated["configurable"].get("checkpoint_id")
    write_json(record_path, record)
    print("[drift] Tidligere fejl er arkiveret; gentager kun validate_deploy.", flush=True)


def run_command(args, settings) -> int:
    initial: dict | Command | None
    if args.command == "run":
        run_id = args.id or datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S")
        run = run_path(run_id)
        if run.exists():
            raise WorkflowError(
                f"{run_id} findes allerede. Brug status/resume/continue eller nyt navn."
            )
        # Check BOTH real endpoints before constructing any generated application.
        checks = check_endpoints(settings)
        init_run(run, settings.snapshot())
        write_json(run / "endpoint-checks.json", checks)
        initial = {"run_dir": str(run), "artifacts": {}, "round": 0, "status": "running"}
    else:
        run_id = args.id
        run = run_path(run_id)
        if not (run / "checkpoints.sqlite").exists():
            raise WorkflowError(f"Ingen gemt kørsel med navnet {run_id}.")
        previous = json.loads((run / "configuration.json").read_text(encoding="utf-8"))
        if previous != settings.snapshot():
            raise WorkflowError(
                "Konfigurationen er ændret siden start. Gendan den eller start ny kørsel."
            )
        initial = None
    config = {"configurable": {"thread_id": run_id}, "max_concurrency": 2, "recursion_limit": 100}
    generator = Generator(settings, run)
    try:
        with SqliteSaver.from_conn_string(str(run / "checkpoints.sqlite")) as saver:
            graph = build_graph(settings, generator, saver)
            snapshot = graph.get_state(config)
            pending = [item for task in snapshot.tasks for item in task.interrupts]
            if args.command == "resume":
                if not pending:
                    raise WorkflowError(
                        "Kørslen afventer ikke review. Se status eller brug continue."
                    )
                initial = Command(resume=args.decision == "approve")
            elif args.command == "continue":
                if pending:
                    raise WorkflowError("Kørslen afventer review. Læs diffen og brug resume.")
                if not snapshot.next:
                    raise WorkflowError("Kørslen er allerede afsluttet.")
            elif args.command == "amend-review":
                from .review_edits import stage_review_edits

                stage_review_edits(graph, run, config, Path(args.patch))
            elif args.command == "retry-deploy":
                retry_deployment(graph, run, config)
            if args.command != "status":
                try:
                    for event in graph.stream(initial, config, stream_mode="updates"):
                        for node in event:
                            if node != "__interrupt__":
                                print(f"[trin] {node}", flush=True)
                finally:
                    summarize(run, graph.get_state(config))
            summary = summarize(run, graph.get_state(config))
    finally:
        generator.close()
    print(f"\nKørsel: {run_id}\nStatus: {summary['status']}\nResultater: {run / 'summary.md'}")
    for item in summary["pending"]:
        print(
            f"Læs diffen: {item['diff']}\nDerefter: python run.py resume --id {run_id} "
            "--decision approve\nAfvis med --decision reject."
        )
    if summary["status"] == "failed" and (run / "demo/reports/deployment.json").exists():
        print(f"Driftsresultat: {run / 'demo/reports/deployment.json'}")
        print(f"Fejllog: {run / 'logs/deployment.txt'}")
    return 0 if summary["status"] in {"needs_review", "passed", "running"} else 2


def compare(args) -> int:
    a, b = run_path(args.a), run_path(args.b)
    data = [json.loads((run / "summary.json").read_text(encoding="utf-8")) for run in (a, b)]
    lines = [f"# Comparison {args.a} / {args.b}", "", "| Metric | A | B |", "|---|---|---|"]
    for field in (
        "status",
        "round",
        "model_calls",
        "rejected_outputs",
        "model_seconds",
        "commits",
        "endpoints_used",
        "roles_used",
        "review_edits",
        "deployment_retries",
        "contract_repairs",
    ):
        lines.append(f"| {field} | {data[0].get(field, 0)} | {data[1].get(field, 0)} |")
    for index, record in enumerate(data):
        result = record.get("quality")
        lines.append(
            f"\n- {'A' if index == 0 else 'B'} quality: "
            + (f"passed={result['passed']}, tests={result['tests']}" if result else "not run")
        )
    common = set(data[0]["files"]) & set(data[1]["files"])
    same = sum(
        (a / "demo" / name).read_bytes() == (b / "demo" / name).read_bytes() for name in common
    )
    lines.extend(
        [
            f"\n- Shared files: {len(common)}; byte-identical: {same}",
            f"- Only A: {sorted(set(data[0]['files']) - set(data[1]['files']))}",
            f"- Only B: {sorted(set(data[1]['files']) - set(data[0]['files']))}",
        ]
    )
    output = a.parent / f"comparison-{args.a}-{args.b}.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output.read_text(encoding="utf-8"))
    print(f"Gemt: {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Mahdi: LangGraph + two local llama.cpp endpoints")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Health, auth, model aliases and actual chat on both endpoints")
    start = sub.add_parser("run", help="Start a new isolated booking demo")
    start.add_argument("--id")
    for command in ("status", "resume", "continue", "amend-review", "retry-deploy"):
        item = sub.add_parser(command)
        item.add_argument("--id", required=True)
        if command == "resume":
            item.add_argument("--decision", choices=["approve", "reject"], required=True)
        if command == "amend-review":
            item.add_argument("--patch", required=True)
    comparison = sub.add_parser("compare")
    comparison.add_argument("--a", required=True)
    comparison.add_argument("--b", required=True)
    args = parser.parse_args()
    try:
        if sys.version_info < (3, 12):  # noqa: UP036 - useful for CLI users with an older Python
            raise WorkflowError(
                "Brug Python 3.12 eller nyere. Bookingdemoens SPEC bruger Python 3.12."
            )
        if args.command == "compare":
            return compare(args)
        settings = load_settings()
        if args.command == "check":
            for role, name in settings.roles.items():
                print(f"{role:10} -> {name} ({settings.for_role(role).model})")
            checks = check_endpoints(settings)
            write_json(TOOLCHAIN / "diagnostics/endpoints.json", checks)
            print("RESULTAT: Begge lokale endpoints er klar.")
            return 0
        return run_command(args, settings)
    except (WorkflowError, OSError, ValueError) as error:
        print(f"FEJL: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Afbrudt. Se status; brug continue for at fortsætte fra checkpoint.", file=sys.stderr)
        return 130
