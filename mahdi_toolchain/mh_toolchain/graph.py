"""Typed state, Send workers, two review gates, bounded repairs and persisted state."""

from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from .config import Settings, WorkflowError
from .files import (
    DEPLOY_SMOKE,
    apply,
    commit,
    execute,
    failure_context,
    quality,
    quality_markdown,
    review,
    write_json,
)
from .llm import clean_file


def merge_artifacts(previous: dict, update: dict) -> dict:
    return {**previous, **update}


class State(TypedDict, total=False):
    run_dir: str
    artifacts: Annotated[dict[str, str], merge_artifacts]
    round: int
    quality: dict
    approved: bool
    status: str


class WorkerState(State, total=False):
    worker: str


def build_graph(settings: Settings, generator, checkpointer):
    tasks = settings.workflow

    def generate_group(
        state: State, group: str, role: str | None = None, only: set[str] | None = None,
    ) -> dict:
        artifacts = dict(state["artifacts"])
        new = {}
        run = Path(state["run_dir"])
        for task in tasks[group]:
            if only is not None and task["path"] not in only:
                continue
            context = {}
            for name in task["read"]:
                if name in artifacts:
                    context[name] = artifacts[name]
                else:
                    path = run / "demo" / name
                    if not path.is_file():
                        raise WorkflowError(f"Kontekstfil mangler: {name}; trinnet stoppes.")
                    context[name] = path.read_text(encoding="utf-8")
            text = task["task"]
            if state["round"] and group in {"coder_1", "coder_2"}:
                context[task["path"]] = artifacts.get(task["path"], "")
                context["quality-errors.txt"] = failure_context(state["quality"])
                text += "\nRepair the source using these errors. Do not modify tests or interfaces."
            content = generator.file(role or group, task["path"], text, context)
            artifacts[task["path"]] = new[task["path"]] = content
        return {"artifacts": new}

    def architect(state: State):
        return generate_group(state, "architect")

    def tech_lead(state: State):
        return generate_group(state, "tech_lead")

    def dispatch(state: State):
        return [Send("coder", {**state, "worker": worker}) for worker in ("coder_1", "coder_2")]

    def coder(state: WorkerState):
        return generate_group(state, state["worker"])

    def tester(state: State):
        # Tests remain fixed during repairs; only source files can be repaired.
        artifacts = dict(state["artifacts"])
        updated = {}
        # A successful Send task may already be saved by an older generator version.
        # Check joined artifacts too, so an invalid old API result cannot skip validation.
        for group in ("coder_1", "coder_2"):
            invalid = set()
            for task in tasks[group]:
                path = task["path"]
                try:
                    normalized = clean_file(artifacts.get(path, ""), path)
                except WorkflowError as error:
                    print(f"[validering] {path}: {error}; genopbygger filen", flush=True)
                    invalid.add(path)
                    continue
                if normalized != artifacts[path]:
                    updated[path] = artifacts[path] = normalized
            if invalid:
                corrected = generate_group({**state, "artifacts": artifacts}, group, only=invalid)
                for path, content in corrected["artifacts"].items():
                    artifacts[path] = updated[path] = clean_file(content, path)
        if state["round"] == 0:
            tests = generate_group({**state, "artifacts": artifacts}, "tester")
            updated.update(tests["artifacts"])
        return {"artifacts": updated}

    def review_code(state: State):
        run = Path(state["run_dir"])
        diff = review(run, state["artifacts"], f"code-round-{state['round']}")
        decision = interrupt(
            {
                "stage": "code",
                "round": state["round"],
                "diff": str(diff),
                "plan": state["artifacts"]["tickets/TICKETS.md"],
                "actions": "Apply the diff, commit in the isolated demo, "
                "then run pytest, ruff and mypy.",
            }
        )
        approved = decision is True
        return {"approved": approved, "status": "running" if approved else "rejected"}

    def run_quality(state: State):
        run = Path(state["run_dir"])
        apply(run, state["artifacts"], f"feat: reviewed model artifacts round {state['round']}")
        result = quality(run, state["round"], int(tasks["command_timeout_seconds"]))
        report = quality_markdown(result)
        write_json(run / "demo/reports" / f"quality-round-{state['round']}.json", result)
        path = run / "demo/reports/quality.md"
        path.write_text(report, encoding="utf-8")
        commit(run / "demo", f"test: measured quality round {state['round']}")
        return {"quality": result, "artifacts": {"reports/quality.md": report}}

    def after_quality(state: State):
        if not state["quality"]["passed"] and state["round"] < int(tasks["max_fix_rounds"]):
            return "repair"
        return "quality_report"

    def repair(state: State):
        return {"round": state["round"] + 1}

    def report_node(state: State):
        return generate_group(state, "quality_report", "tester")

    def docs(state: State):
        return generate_group(state, "docs")

    def deploy(state: State):
        return generate_group(state, "deploy")

    def review_delivery(state: State):
        run = Path(state["run_dir"])
        diff = review(run, state["artifacts"], "delivery")
        decision = interrupt(
            {
                "stage": "delivery",
                "diff": str(diff),
                "quality_passed": state["quality"]["passed"],
                "actions": "Apply and commit documentation/configuration, "
                "then validate local runtime "
                "(import, health, SQLite persistence). Docker build is a manual check.",
            }
        )
        approved = decision is True
        return {"approved": approved, "status": "running" if approved else "rejected"}

    def validate_deploy(state: State):
        import sys

        run = Path(state["run_dir"])
        apply(run, state["artifacts"], "docs: reviewed documentation and deployment configuration")
        result = execute(
            run,
            "deployment",
            [sys.executable, "-c", DEPLOY_SMOKE],
            int(tasks["command_timeout_seconds"]),
        )
        result["container_build"] = "NOT_RUN"
        result["passed"] = result["exit_code"] == 0
        write_json(run / "demo/reports/deployment.json", result)
        commit(run / "demo", "deploy: measured local runtime and persistence validation")
        return {"status": "passed" if result["passed"] and state["quality"]["passed"] else "failed"}

    def approved_route(state: State, destination: str):
        return destination if state.get("approved") else END

    builder = StateGraph(State)
    nodes = {
        "architect": architect,
        "tech_lead": tech_lead,
        "coder": coder,
        "tester": tester,
        "review_code": review_code,
        "run_quality": run_quality,
        "repair": repair,
        "quality_report": report_node,
        "docs": docs,
        "deploy": deploy,
        "review_delivery": review_delivery,
        "validate_deploy": validate_deploy,
    }
    for name, node in nodes.items():
        builder.add_node(name, node)
    builder.add_edge(START, "architect")
    builder.add_edge("architect", "tech_lead")
    builder.add_conditional_edges("tech_lead", dispatch, ["coder"])
    builder.add_edge("coder", "tester")
    builder.add_edge("tester", "review_code")
    builder.add_conditional_edges(
        "review_code", lambda s: approved_route(s, "run_quality"), ["run_quality", END]
    )
    builder.add_conditional_edges("run_quality", after_quality, ["repair", "quality_report"])
    builder.add_conditional_edges("repair", dispatch, ["coder"])
    builder.add_edge("quality_report", "docs")
    builder.add_edge("docs", "deploy")
    builder.add_edge("deploy", "review_delivery")
    builder.add_conditional_edges(
        "review_delivery", lambda s: approved_route(s, "validate_deploy"), ["validate_deploy", END]
    )
    builder.add_edge("validate_deploy", END)
    return builder.compile(checkpointer=checkpointer)
