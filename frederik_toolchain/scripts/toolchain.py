"""Glue mellem Aider, llm_backend og demo-repoet (planens afsnit 2).

    toolchain.py check                                  tjek endpoints, nøgler og rollebinding
    toolchain.py run <rolle> [opgave ...] [--interactive]
    toolchain.py summary                                skriv summary.md for kørslen

Miljøvariabler:
    DEMO_DIR         demo-repoet (standard: runs/latest/demo)
    LOG_DIR          logs for kørslen (standard: ved siden af demo-repoet)
    ENDPOINTS_FILE   rolle -> endpoint (standard: ../llm_backend/config/endpoints.yaml)
    LLM_BACKEND_ENV  .env med API-nøglerne (standard: ../llm_backend/.env)
    STEP_TIMEOUT     max sekunder pr. Aider-kørsel (standard: 1800)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

TC_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = TC_DIR.parent
VENV_PY = TC_DIR / ".venv/bin/python"
LAUNCHER = TC_DIR / "scripts/aider_launch.py"
AIDER_CONF = TC_DIR / ".aider.conf.yml"
ROLES_FILE = TC_DIR / "config/roles.yaml"

ENDPOINTS_FILE = Path(os.environ.get("ENDPOINTS_FILE", REPO_DIR / "llm_backend/config/endpoints.yaml"))
BACKEND_ENV = Path(os.environ.get("LLM_BACKEND_ENV", REPO_DIR / "llm_backend/.env"))
DEMO_DIR = Path(os.environ.get("DEMO_DIR", TC_DIR / "runs/latest/demo")).resolve()
LOG_DIR = Path(os.environ.get("LOG_DIR", DEMO_DIR.parent / "logs")).resolve()
STEP_TIMEOUT = int(os.environ.get("STEP_TIMEOUT", "1800"))

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
STEPS_FILE = "steps.tsv"
STEPS_HEADER = [
    "step", "role", "task", "mode", "model", "editor", "started", "seconds",
    "attempts", "status", "commits", "sent", "received", "files", "note",
]  # fmt: skip

RETRY_HINT = (
    "\n\nNOTE: The previous attempt produced no usable file (often because the answer was too long). "
    "Answer with only the file listing(s), keep each file short and do not explain."
)
TOO_LONG_HINT = (
    "\n\nNOTE: {files} too long. Rewrite it to at most {limit} lines: bullet points only, no paragraphs."
)

# Kendte fejltilstande i Aiders output (EV-04), kun tekster fra Aider/litellm selv, ikke fra modellens svar.
# Bruges kun på mislykkede forsøg. Første match vinder; "hård" stopper nye forsøg.
DIAGNOSES = [
    (r"litellm\.APIConnectionError", "backend svarer ikke", True),
    (r"litellm\.AuthenticationError", "API-nøglen blev afvist", True),
    (r"exceeds the [\d,]+ token limit", "kontekstvinduet er for lille til filerne", True),
    (r"has hit a token limit", "svaret ramte max_tokens og blev kasseret", False),
    (r"No filename provided before|did not conform to the edit format", "svaret fulgte ikke edit-formatet", False),
    (r"\[politik\] Create new file\?.*-> nej", "modellen skrev til en fil uden for opgaven", False),
    (r"litellm\.Timeout|Toolchain: stoppet efter STEP_TIMEOUT", "timeout", False),
    (r"Unable to commit", "Aider kunne ikke committe", False),
]


class ToolchainError(Exception):
    pass


# --- Konfiguration ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    api_key: str
    api_key_env: str
    model: str
    context_window: int
    max_tokens: int
    timeout: int

    @property
    def aider_model(self) -> str:
        return f"openai/{self.model}"

    @property
    def root_url(self) -> str:
        return self.base_url.rstrip("/").removesuffix("/v1")

    def require_key(self) -> None:
        if not self.api_key:
            raise ToolchainError(
                f"API-nøgle mangler for {self.name}: sæt {self.api_key_env} i {BACKEND_ENV} eller i miljøet"
            )


@dataclass
class Task:
    role: str
    name: str
    mode: str
    edit: list[str]
    read: list[str]
    message: str
    map_tokens: int
    retries: int
    max_lines: int
    line_tolerance: float
    max_reflections: int
    run_before: str | None
    main: Endpoint
    editor: Endpoint | None
    weak: Endpoint

    @property
    def endpoints(self) -> list[Endpoint]:
        return [ep for ep in (self.main, self.editor, self.weak) if ep]


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def load_endpoints() -> tuple[dict[str, Endpoint], dict[str, str]]:
    if not ENDPOINTS_FILE.is_file():
        raise ToolchainError(f"{ENDPOINTS_FILE} findes ikke")
    cfg = yaml.safe_load(ENDPOINTS_FILE.read_text(encoding="utf-8"))
    env = {**read_env_file(BACKEND_ENV), **os.environ}
    endpoints = {}
    for name, ep in cfg["endpoints"].items():
        url = ep["base_url"]
        if urlparse(url).hostname not in LOCAL_HOSTS:
            raise ToolchainError(f"{name}: {url} er ikke et localhost-endpoint (NFR-SEC-01)")
        endpoints[name] = Endpoint(
            name=name,
            base_url=url,
            api_key=env.get(ep["api_key_env"], ""),
            api_key_env=ep["api_key_env"],
            model=ep["model"],
            context_window=int(ep["context_window"]),
            max_tokens=int(ep["max_tokens"]),
            timeout=int(ep.get("timeout_seconds", 300)),
        )
    return endpoints, dict(cfg["roles"])


def compose_message(prompt_file: str, task_message: str, edit: list[str], read: list[str], max_lines: int) -> str:
    task_text = task_message.strip()
    if any(f.endswith(".md") for f in edit):
        task_text += f"\nLimit: at most {max_lines} lines per Markdown file."
    parts = [
        (TC_DIR / "prompts/common.md").read_text(encoding="utf-8").strip(),
        (TC_DIR / prompt_file).read_text(encoding="utf-8").strip(),
        "Task:\n" + task_text,
        "Files you write:\n" + "\n".join(f"- {f}" for f in edit),
    ]
    if read:
        parts.append("Read-only context:\n" + "\n".join(f"- {f}" for f in read))
    return "\n\n".join(parts) + "\n"


def resolve_tasks(role: str, names: list[str]) -> list[Task]:
    cfg = yaml.safe_load(ROLES_FILE.read_text(encoding="utf-8"))
    defaults, roles = cfg.get("defaults", {}), cfg["roles"]
    if role not in roles:
        raise ToolchainError(f"ukendt rolle '{role}'. Kendte roller: {', '.join(roles)}")
    endpoints, role_map = load_endpoints()
    if role not in role_map:
        raise ToolchainError(f"rollen '{role}' mangler under roles: i {ENDPOINTS_FILE} (HR-02)")

    rcfg = roles[role]
    by_name = {t["name"]: t for t in rcfg["tasks"]}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise ToolchainError(f"ukendt opgave {unknown} for {role}. Kendte: {', '.join(by_name)}")
    selected = [by_name[n] for n in names] or [t for t in rcfg["tasks"] if not t.get("manual")]

    def endpoint(name: str) -> Endpoint:
        if name not in endpoints:
            raise ToolchainError(f"endpoint '{name}' findes ikke i {ENDPOINTS_FILE}")
        return endpoints[name]

    tasks = []
    for t in selected:
        def pick(key: str, t=t):
            return t.get(key, rcfg.get(key, defaults.get(key)))

        mode = pick("mode")
        if mode not in ("code", "architect"):
            raise ToolchainError(f"{role}/{t['name']}: mode skal være code eller architect, ikke {mode}")
        read = list(dict.fromkeys(rcfg.get("read", []) + t.get("read", [])))
        max_lines = int(pick("max_lines"))
        tasks.append(
            Task(
                role=role,
                name=t["name"],
                mode=mode,
                edit=t["edit"],
                read=read,
                message=compose_message(rcfg["prompt"], t["message"], t["edit"], read, max_lines),
                map_tokens=int(pick("map_tokens")),
                retries=int(pick("retries")),
                max_lines=max_lines,
                line_tolerance=float(pick("line_tolerance")),
                max_reflections=int(pick("max_reflections")),
                run_before=t.get("run_before"),
                main=endpoint(role_map[role]),
                editor=endpoint(pick("editor_endpoint")) if mode == "architect" else None,
                weak=endpoint(pick("weak_endpoint")),
            )
        )
    return tasks


# --- Git-værn (afsnit 1a) ----------------------------------------------------------------------


def git(*args: str, check: bool = True) -> str:
    res = subprocess.run(["git", "-C", str(DEMO_DIR), *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise ToolchainError(f"git {' '.join(args)} fejlede: {res.stderr.strip()}")
    return res.stdout.strip()


def guard_demo_repo() -> None:
    """Aider må kun køre i et demo-repo, der er sin egen git-rod og ikke har nogen remote."""
    if not (DEMO_DIR / ".git").is_dir():
        raise ToolchainError(f"{DEMO_DIR} er ikke et demo-repo (kør scripts/init_demo.sh)")
    if Path(git("rev-parse", "--show-toplevel")).resolve() != DEMO_DIR:
        raise ToolchainError(f"{DEMO_DIR} er ikke roden af sit eget git-repo")
    if DEMO_DIR == REPO_DIR:
        raise ToolchainError("demo-repoet må ikke være llm_mandatory_1")
    if git("remote"):
        raise ToolchainError(f"demo-repoet har en remote ({git('remote')}); det er ikke tilladt")


def dirty_paths() -> list[str]:
    return [line[3:] for line in git("status", "--porcelain").splitlines()]


# --- Kørsel af én opgave -----------------------------------------------------------------------


def write_model_files(tmp: Path, endpoints: list[Endpoint]) -> tuple[Path, Path]:
    """Model-settings med api_base/api_key pr. model, så én Aider-proces kan bruge to endpoints."""
    settings, metadata = [], {}
    for ep in {ep.name: ep for ep in endpoints}.values():
        ep.require_key()
        if ep.aider_model in metadata:
            raise ToolchainError(f"to endpoints bruger samme modelnavn {ep.model}; giv dem hver sit alias")
        settings.append(
            {
                "name": ep.aider_model,
                "edit_format": "whole",
                "editor_edit_format": "editor-whole",
                "use_repo_map": True,
                "extra_params": {
                    "api_base": ep.base_url,
                    "api_key": ep.api_key,
                    "max_tokens": ep.max_tokens,
                },
            }
        )
        metadata[ep.aider_model] = {
            "max_input_tokens": ep.context_window - ep.max_tokens,
            "max_output_tokens": ep.max_tokens,
            "max_tokens": ep.max_tokens,
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
            "litellm_provider": "openai",
            "mode": "chat",
        }
    settings_file, metadata_file = tmp / "model-settings.yml", tmp / "model-metadata.json"
    settings_file.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return settings_file, metadata_file


def aider_args(task: Task, settings: Path, metadata: Path, step_id: str, read: list[str], interactive: bool):
    args = [
        "--config", str(AIDER_CONF),
        "--git",
        "--model-settings-file", str(settings),
        "--model-metadata-file", str(metadata),
        "--model", task.main.aider_model,
        "--weak-model", task.weak.aider_model,
        "--map-tokens", str(task.map_tokens),
        "--timeout", str(max(ep.timeout for ep in task.endpoints)),
        "--chat-history-file", str(LOG_DIR / f"{step_id}.chat.md"),
        "--llm-history-file", str(LOG_DIR / f"{step_id}.llm.md"),
        "--input-history-file", str(LOG_DIR / "input.history"),
    ]  # fmt: skip
    if task.mode == "architect":
        args += ["--architect", "--editor-model", task.editor.aider_model, "--editor-edit-format", "editor-whole"]
    else:
        args += ["--edit-format", "whole"]
    if interactive:
        args += ["--no-auto-accept-architect", "--pretty", "--fancy-input"]
    else:
        # Én besked pr. kørsel: ingen grund til at lade weak-modellen opsummere historikken bagefter.
        args += ["--max-chat-history-tokens", "1000000"]
    for f in read:
        args += ["--read", f]
    return args + task.edit


def run_logged(cmd: list[str], env: dict[str, str], log_file: Path) -> tuple[int, str]:
    """Kører Aider, viser output live og gemmer det i logfilen."""
    chunks = []
    with log_file.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd, cwd=DEMO_DIR, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )  # fmt: skip
        deadline = time.monotonic() + STEP_TIMEOUT
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
            chunks.append(line)
            if time.monotonic() > deadline:
                proc.kill()
                chunks.append(f"\nToolchain: stoppet efter STEP_TIMEOUT={STEP_TIMEOUT}s (Timeout)\n")
                break
        return proc.wait(), "".join(chunks)


def diagnose(output: str) -> tuple[str, bool]:
    for pattern, text, hard in DIAGNOSES:
        if re.search(pattern, output):
            return text, hard
    return "", False


def token_counts(output: str) -> tuple[int, int]:
    def num(value: str, suffix: str) -> int:
        return int(float(value) * (1000 if suffix == "k" else 1))

    sent = received = 0
    for m in re.finditer(r"Tokens: ([\d.]+)(k?) sent, ([\d.]+)(k?) received", output):
        sent += num(m.group(1), m.group(2))
        received += num(m.group(3), m.group(4))
    return sent, received


def bad_task_files(task: Task) -> list[str]:
    """Returnerer de edit-filer, der mangler, er tomme eller ikke er committet."""
    bad = []
    for f in task.edit:
        path = DEMO_DIR / f
        tracked = subprocess.run(
            ["git", "-C", str(DEMO_DIR), "ls-files", "--error-unmatch", f], capture_output=True
        ).returncode == 0  # fmt: skip
        if not path.is_file() or path.stat().st_size == 0 or not tracked or f in dirty_paths():
            bad.append(f)
    return bad


def markdown_lengths(task: Task) -> dict[str, int]:
    """Antal ikke-tomme linjer i opgavens Markdown-filer."""
    return {
        f: sum(1 for line in (DEMO_DIR / f).read_text(encoding="utf-8").splitlines() if line.strip())
        for f in task.edit
        if f.endswith(".md") and (DEMO_DIR / f).is_file()
    }


def remove_empty_untracked(task: Task) -> None:
    """Aider opretter tomme filer for nye edit-filer; de fjernes igen, hvis modellen ikke skrev dem."""
    untracked = set(git("ls-files", "--others", "--exclude-standard").splitlines())
    for f in task.edit:
        path = DEMO_DIR / f
        if f in untracked and path.is_file() and path.stat().st_size == 0:
            path.unlink()


def next_step_no() -> int:
    steps = LOG_DIR / STEPS_FILE
    if not steps.is_file():
        return 1
    return sum(1 for _ in steps.open(encoding="utf-8"))  # header tæller som 1, så første trin bliver 1


def record_step(row: dict) -> None:
    steps = LOG_DIR / STEPS_FILE
    new = not steps.is_file()
    with steps.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=STEPS_HEADER, delimiter="\t")
        if new:
            writer.writeheader()
        writer.writerow(row)


def run_task(task: Task, interactive: bool) -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    step_id = f"{next_step_no():02d}-{task.role}-{task.name}"
    label = f"{task.role}/{task.name}"
    editor = f" + editor {task.editor.name}" if task.editor else ""
    print(f"\n=== {step_id}: {label} ({task.mode}-mode, {task.main.name}{editor}) ===", flush=True)

    if task.run_before:
        script = TC_DIR / "scripts" / f"{task.run_before}.sh"
        subprocess.run([str(script), str(DEMO_DIR)], check=False)

    read = []
    for f in task.read:
        if (DEMO_DIR / f).is_file():
            read.append(f)
        else:
            print(f"ADVARSEL: read-filen {f} findes ikke og springes over", flush=True)

    if dirty_paths():
        raise ToolchainError(f"demo-repoet har ucommittede ændringer: {dirty_paths()}")
    # Aider finder ikke git-repoet, hvis en ny fils mappe mangler, så mapperne oprettes først.
    for f in task.edit:
        (DEMO_DIR / f).parent.mkdir(parents=True, exist_ok=True)
    head_before = git("rev-parse", "HEAD")
    started = datetime.now().isoformat(timespec="seconds")
    t0 = time.monotonic()
    status, notes, attempts, sent, received = "fejl", [], 0, 0, 0

    tmp_root = os.environ.get("XDG_RUNTIME_DIR") or None
    with tempfile.TemporaryDirectory(prefix="aider-", dir=tmp_root) as tmp:  # mappen er 0700
        settings, metadata = write_model_files(Path(tmp), task.endpoints)
        env = {**os.environ, "OPENAI_API_BASE": task.main.base_url, "OPENAI_API_KEY": task.main.api_key}
        args = aider_args(task, settings, metadata, step_id, read, interactive)

        if interactive:
            proc = subprocess.run([str(VENV_PY), str(LAUNCHER), str(DEMO_DIR), "-", "--", *args], cwd=DEMO_DIR, env=env)
            remove_empty_untracked(task)
            return "ok" if proc.returncode == 0 else "fejl"

        hint = ""
        # Inden for tolerancen godtages filen med en note; først over den får modellen et nyt forsøg.
        hard_limit = task.max_lines + math.ceil(task.max_lines * task.line_tolerance)
        env["TOOLCHAIN_MAX_REFLECTIONS"] = str(task.max_reflections)
        while attempts <= task.retries:
            attempts += 1
            message_file = LOG_DIR / f"{step_id}.message.md"
            message_file.write_text(task.message + hint, encoding="utf-8")
            attempt_head = git("rev-parse", "HEAD")
            cmd = [str(VENV_PY), str(LAUNCHER), str(DEMO_DIR), str(message_file), "--", *args]
            rc, output = run_logged(cmd, env, LOG_DIR / f"{step_id}.log")
            s, r = token_counts(output)
            sent, received = sent + s, received + r
            remove_empty_untracked(task)
            made_commit = git("rev-parse", "HEAD") != attempt_head
            if rc == 0 and made_commit and not bad_task_files(task):
                lengths = markdown_lengths(task)
                over = {f: n for f, n in lengths.items() if n > task.max_lines}
                too_long = {f: n for f, n in over.items() if n > hard_limit}
                if not too_long:
                    status = "ok"
                    if over:
                        lengths_note = ", ".join(f"{f} {n}/{task.max_lines} linjer" for f, n in over.items())
                        notes.append(f"inden for tolerancen: {lengths_note}")
                    break
                status = "for lang"
                files = ", ".join(f"{f} has {n} lines" for f, n in too_long.items())
                hint = TOO_LONG_HINT.format(files=files + (" which is" if len(too_long) == 1 else ", which are"),
                                            limit=task.max_lines)
                too_long_note = ", ".join(f"{f} {n} linjer" for f, n in too_long.items())
                notes.append(f"forsøg {attempts}: for lang: {too_long_note} (max {task.max_lines})")
                print(f"--- {label}: {notes[-1]}", flush=True)
                continue
            # En fil fra et tidligere forsøg (for lang, men brugbar) ligger stadig committet.
            status = "for lang" if status == "for lang" else "fejl"
            hint = RETRY_HINT
            note, hard = diagnose(output)
            notes.append(f"forsøg {attempts}: {note or (f'exit {rc}' if rc else 'ingen commit eller tom fil')}")
            print(f"--- {label}: {notes[-1]}", flush=True)
            if hard:
                break

    head_after = git("rev-parse", "HEAD")
    files = git("diff", "--name-only", head_before, head_after).split()
    (LOG_DIR / f"{step_id}.diff").write_text(
        git("diff", "--stat", "--patch", head_before, head_after) + "\n", encoding="utf-8"
    )
    if dirty_paths():
        status = "fejl"
        notes.append(f"ucommittede ændringer efter Aider: {dirty_paths()}")
    note = "; ".join(notes)

    seconds = round(time.monotonic() - t0)
    record_step(
        {
            "step": step_id, "role": task.role, "task": task.name, "mode": task.mode,
            "model": task.main.name, "editor": task.editor.name if task.editor else "",
            "started": started, "seconds": seconds, "attempts": attempts, "status": status,
            "commits": git("rev-list", "--count", f"{head_before}..{head_after}"),
            "sent": sent, "received": received, "files": " ".join(files), "note": note,
        }
    )  # fmt: skip
    print(f"=== {step_id}: {status} på {seconds}s ({attempts} forsøg) {note}".rstrip(), flush=True)
    return status


# --- Kommandoer -------------------------------------------------------------------------------


def http_status(url: str, key: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, ""
    except (urllib.error.URLError, OSError) as err:
        return 0, str(err)


def cmd_check(_args) -> int:
    endpoints, role_map = load_endpoints()
    print(f"Endpoints: {ENDPOINTS_FILE}\nNøgler:    {BACKEND_ENV} (+ miljøet)\n")
    print("Rolle      -> endpoint (model)")
    for role, name in role_map.items():
        print(f"  {role:<10} -> {name} ({endpoints[name].model})")
    failures = 0
    for ep in {role_map[r]: endpoints[role_map[r]] for r in role_map}.values():
        print(f"\n{ep.name}: {ep.base_url}")
        checks = []
        if not ep.api_key:
            checks.append((False, f"{ep.api_key_env} mangler"))
        health, _ = http_status(f"{ep.root_url}/health")
        checks.append((health == 200, f"/health -> {health or 'ingen forbindelse'}"))
        if health != 200:
            checks.append((False, "endpointet er ikke llm_backend (port optaget eller backend stoppet?)"))
        else:
            unauth, _ = http_status(f"{ep.base_url}/models")
            checks.append((unauth == 401, f"uden nøgle -> {unauth} (forventet 401, NFR-SEC-01)"))
        if ep.api_key and health == 200:
            code, body = http_status(f"{ep.base_url}/models", ep.api_key)
            served = [m.get("id") for m in json.loads(body).get("data", [])] if code == 200 else []
            checks.append((ep.model in served, f"med nøgle -> {code}, modeller {served}"))
        for ok, text in checks:
            print(f"  [{'ok' if ok else 'FEJL'}] {text}")
            failures += not ok
    print("\nRESULTAT:", "alt ok" if not failures else f"{failures} tjek fejlede")
    return 1 if failures else 0


def cmd_run(args) -> int:
    tasks = resolve_tasks(args.role, args.tasks)
    guard_demo_repo()
    statuses = [run_task(task, args.interactive) for task in tasks]
    return 0 if all(s == "ok" for s in statuses) else 2


def cmd_summary(_args) -> int:
    steps_file = LOG_DIR / STEPS_FILE
    if not steps_file.is_file():
        raise ToolchainError(f"{steps_file} findes ikke")
    rows = list(csv.DictReader(steps_file.open(encoding="utf-8"), delimiter="\t"))
    total = sum(int(r["seconds"]) for r in rows)
    per_endpoint: dict[str, int] = {}
    for r in rows:
        per_endpoint[r["model"]] = per_endpoint.get(r["model"], 0) + int(r["seconds"])

    def fmt(sec: int) -> str:
        return f"{sec // 60}m{sec % 60:02d}s"

    lines = [
        f"# Kørsel {DEMO_DIR.parent.name}",
        "",
        f"- Aider-trin: {len(rows)}, heraf ok: {sum(r['status'] == 'ok' for r in rows)}",
        f"- Samlet tid i Aider: {fmt(total)} ("
        + ", ".join(f"{k}: {fmt(v)}" for k, v in sorted(per_endpoint.items()))
        + ")",
        f"- Tokens: {sum(int(r['sent']) for r in rows):,} sendt, {sum(int(r['received']) for r in rows):,} modtaget",
        "- Commits pr. branch: "
        + ", ".join(f"{b}={git('rev-list', '--count', b)}" for b in git("branch", "--format=%(refname:short)").split()),
        "",
        "| Trin | Mode | Endpoint | Tid | Forsøg | Status | Commits | Tokens ind/ud | Note |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        endpoint = r["model"] + (f" + {r['editor']}" if r["editor"] else "")
        lines.append(
            f"| {r['step']} | {r['mode']} | {endpoint} | {fmt(int(r['seconds']))} | {r['attempts']} "
            f"| {r['status']} | {r['commits']} | {r['sent']}/{r['received']} | {r['note']} |"
        )
    for title, path in (("Kvalitet", DEMO_DIR / "reports/raw/summary.md"), ("Git-tjek", LOG_DIR / "check_git.txt")):
        if path.is_file():
            lines += ["", f"## {title}", "", path.read_text(encoding="utf-8").strip()]
    out = DEMO_DIR.parent / "summary.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check").set_defaults(func=cmd_check)
    run = sub.add_parser("run")
    run.add_argument("role")
    run.add_argument("tasks", nargs="*")
    run.add_argument("--interactive", action="store_true")
    run.set_defaults(func=cmd_run)
    sub.add_parser("summary").set_defaults(func=cmd_summary)
    args = parser.parse_args()
    try:
        return args.func(args)
    except ToolchainError as err:
        print(f"FEJL: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
