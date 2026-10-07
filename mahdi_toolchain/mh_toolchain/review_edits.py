"""Stage recorded review edits at code/delivery pauses without executing generated code."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .config import WorkflowError
from .files import safe_path, write_json
from .llm import clean_file


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def stage_review_edits(graph, run: Path, config: dict, patch_path: Path) -> bool:
    """Validate everything first; append provenance and create a new review checkpoint."""
    snapshot = graph.get_state(config)
    pending = [item.value for task in snapshot.tasks for item in task.interrupts]
    stage = pending[0].get("stage") if len(pending) == 1 else None
    if stage not in {"code", "delivery"}:
        raise WorkflowError("Reviewrettelser kræver en kode- eller delivery-pause.")
    if stage == "code" and (
        snapshot.values.get("round", 0) != 0 or snapshot.values.get("quality") is not None
    ):
        raise WorkflowError("Reviewrettelser kan kun bruges før første kodegodkendelse.")
    if stage == "delivery" and not isinstance(snapshot.values.get("quality"), dict):
        raise WorkflowError("Delivery-rettelser kræver et gemt kvalitetsresultat.")
    patch = json.loads(patch_path.read_text(encoding="utf-8-sig"))
    if not isinstance(patch, dict) or patch.get("format") != "mahdi-review-edits-v1" or (
        patch.get("run_id") != run.name
    ):
        raise WorkflowError("Reviewpakken har forkert format eller kørselsnavn.")
    if patch.get("stage", "code") != stage:
        raise WorkflowError("Reviewpakken passer ikke til den aktuelle reviewfase.")
    configuration = json.loads((run / "configuration.json").read_text(encoding="utf-8-sig"))
    config_hash = content_hash(json.dumps(configuration, sort_keys=True))
    if patch.get("configuration_sha256") != config_hash:
        raise WorkflowError("Reviewpakken passer ikke til den gemte konfiguration.")
    artifacts = snapshot.values["artifacts"]
    base = patch.get("base_artifacts_sha256")
    if not isinstance(base, dict) or set(base) != set(artifacts):
        raise WorkflowError("Reviewpakkens filliste passer ikke til checkpointet.")
    groups = ("quality_report", "docs", "deploy") if stage == "delivery" else (
        "architect", "tech_lead", "coder_1", "coder_2", "tester"
    )
    allowed = {
        task["path"] for group in groups
        for task in configuration["workflow"][group]
    }
    if stage == "delivery":
        allowed &= {
            "reports/analysis.md", "README.md", "docs/runbook.md", "Dockerfile",
            "docs/deployment-checklist.md",
        }
    edits = patch.get("edits")
    if not isinstance(edits, list) or not edits:
        raise WorkflowError("Reviewpakken indeholder ingen rettelser.")
    proposed = {}
    reasons = {}
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get("path"), str):
            raise WorkflowError("Reviewrettelsen mangler en gyldig filsti.")
        name = edit["path"]
        if name not in allowed or name not in artifacts or name in proposed:
            raise WorkflowError(f"Ugyldig eller gentaget reviewsti: {name}.")
        safe_path(run / "demo", name)
        content = edit.get("content")
        reason = edit.get("reason")
        if not isinstance(content, str) or not isinstance(reason, str) or not reason.strip():
            raise WorkflowError(f"Reviewrettelsen til {name} mangler indhold/begrundelse.")
        if edit.get("before_sha256") != base[name]:
            raise WorkflowError(f"Reviewrettelsens kildehash passer ikke: {name}.")
        if edit.get("after_sha256") != content_hash(content):
            raise WorkflowError(f"Reviewrettelsens indholdshash passer ikke: {name}.")
        if clean_file(content, name) != content:
            raise WorkflowError(f"Reviewrettelsen skal være færdigvalideret: {name}.")
        proposed[name], reasons[name] = content, reason
    for name, original in artifacts.items():
        expected = {base[name]}
        if name in proposed:
            expected.add(content_hash(proposed[name]))
        if content_hash(original) not in expected:
            raise WorkflowError(f"Filen er ændret siden gennemgangen: {name}. Intet er anvendt.")
    changed = {name: text for name, text in proposed.items() if text != artifacts[name]}
    if not changed:
        print("[review] Rettelserne er allerede gemt; godkendelsen afventer stadig.")
        return False
    previous = list((run / "logs").glob("review-edits-*.json"))
    number = max((int(path.stem.rsplit("-", 1)[1]) for path in previous), default=0) + 1
    record_path = run / "logs" / f"review-edits-{number:03d}.json"
    old_diff = Path(pending[0]["diff"])
    old_diff_bytes = old_diff.read_bytes()
    backup = run / "logs" / f"review-before-edit-{number:03d}.diff"
    backup.write_bytes(old_diff_bytes)
    record = {
        "kind": "review correction; not a local model response",
        "stage": stage,
        "origin": patch.get("origin", "review editor"),
        "created_utc": datetime.now(UTC).isoformat(),
        "configuration_sha256": config_hash,
        "patch_sha256": hashlib.sha256(patch_path.read_bytes()).hexdigest(),
        "source_archive_sha256": patch.get("source_archive_sha256"),
        "previous_diff": backup.name,
        "previous_diff_sha256": hashlib.sha256(old_diff_bytes).hexdigest(),
        "approval": "awaiting user review; no generated code executed",
        "edits": [{
            "path": name, "reason": reasons[name],
            "before_sha256": content_hash(artifacts[name]),
            "after_sha256": content_hash(content),
            "original_content": artifacts[name], "reviewed_content": content,
        } for name, content in changed.items()],
        "checkpoint_updated": False,
    }
    write_json(record_path, record)
    updated = graph.update_state(
        config, {"artifacts": changed}, as_node="deploy" if stage == "delivery" else "tester",
    )
    record["checkpoint_updated"] = True
    record["checkpoint_id"] = updated["configurable"].get("checkpoint_id")
    write_json(record_path, record)
    print(f"[review] {len(changed)} filer rettet i checkpointet; nye diffs afventer review.")
    return True
