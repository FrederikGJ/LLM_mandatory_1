"""Backend preparation that works before installing any third-party Python packages."""

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def prepare_backend(backend: Path) -> list[str]:
    messages = []
    env = backend / ".env"
    source = env if env.exists() else backend / ".env.example"
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    values = {}
    for line in lines:
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    missing = {key for key in ("LLM_A_API_KEY", "LLM_B_API_KEY") if not values.get(key)}
    for index, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in missing:
            lines[index] = f"{key}={secrets.token_hex(32)}"
            missing.remove(key)
    for key in sorted(missing):
        lines.append(f"{key}={secrets.token_hex(32)}")
    result = ("\n".join(lines) + "\n").encode()
    if not env.exists():
        with env.open("xb") as handle:
            handle.write(result)
        messages.append("Oprettet llm_backend/.env med to lokale API-nøgler.")
    elif env.read_bytes().replace(b"\r\n", b"\n") != result:
        env.write_bytes(result)
        messages.append("Udfyldt manglende API-nøgler; eksisterende værdier bevaret.")
    else:
        messages.append("Eksisterende .env og API-nøgler bevaret.")
    for name in ("download-models.sh", "smoke-test.sh"):
        path = backend / "scripts" / name
        content = path.read_bytes()
        if b"\r\n" in content:
            path.write_bytes(content.replace(b"\r\n", b"\n"))
            messages.append(f"Rettet linjeskift til LF i {name}.")
    return messages


def main() -> int:
    for message in prepare_backend(ROOT / "llm_backend"):
        print(message)
    print("Start Docker Desktop. Kør derefter docker compose up -d i llm_backend.")
    return 0
