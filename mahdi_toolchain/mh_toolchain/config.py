"""Read shared role routing without embedding keys in prompts or checkpoints."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml
from dotenv import dotenv_values

TOOLCHAIN = Path(__file__).resolve().parents[1]
REPO = TOOLCHAIN.parent
ROLES = ("architect", "tech_lead", "coder_1", "coder_2", "tester", "docs", "deploy")


class WorkflowError(Exception):
    """A failure that the user can act on without exposing secrets."""


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    model: str
    api_key_env: str
    context_window: int
    max_tokens: int
    timeout_seconds: int
    api_key: str = field(repr=False, default="")

    @property
    def root_url(self) -> str:
        return self.base_url.removesuffix("/v1")

    def public(self) -> dict:
        return {key: value for key, value in vars(self).items() if key != "api_key"}


@dataclass
class Settings:
    endpoints: dict[str, Endpoint]
    roles: dict[str, str]
    workflow: dict

    def snapshot(self) -> dict:
        return {
            "endpoints": {key: value.public() for key, value in self.endpoints.items()},
            "roles": self.roles,
            "workflow": self.workflow,
        }

    def for_role(self, role: str) -> Endpoint:
        return self.endpoints[self.roles[role]]


def load_settings() -> Settings:
    endpoints_path = Path(
        os.environ.get("ENDPOINTS_FILE", REPO / "llm_backend/config/endpoints.yaml")
    )
    env_path = Path(os.environ.get("LLM_BACKEND_ENV", REPO / "llm_backend/.env"))
    workflow_path = Path(os.environ.get("WORKFLOW_FILE", TOOLCHAIN / "config/workflow.yaml"))
    try:
        config = yaml.safe_load(endpoints_path.read_text(encoding="utf-8-sig"))
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8-sig"))
        env = {**dotenv_values(env_path), **os.environ}
        endpoints = {}
        for name, item in config["endpoints"].items():
            url = item["base_url"].rstrip("/")
            parsed = urlparse(url)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path != "/v1"
            ):
                raise WorkflowError(f"{name}: base_url skal være et lokalt endpoint med /v1.")
            endpoint = Endpoint(
                name,
                url,
                item["model"],
                item["api_key_env"],
                int(item["context_window"]),
                int(item["max_tokens"]),
                int(item.get("timeout_seconds", 300)),
                env.get(item["api_key_env"], "") or "",
            )
            if not 0 < endpoint.max_tokens < endpoint.context_window:
                raise WorkflowError(f"{name}: max_tokens skal være mindre end context_window.")
            endpoints[name] = endpoint
        roles = {role: config["roles"][role] for role in ROLES}
        if any(name not in endpoints for name in roles.values()):
            raise WorkflowError("En rolle henviser til et endpoint, som ikke findes.")
        addresses = {endpoints[name].base_url for name in roles.values()}
        if len(addresses) < 2:
            raise WorkflowError("HR-01: rollerne skal anvende mindst to separate lokale endpoints.")
        if not 0 <= int(workflow["max_fix_rounds"]) <= 5:
            raise WorkflowError("max_fix_rounds skal være 0-5.")
        if not 0 <= int(workflow["format_retries"]) <= 3:
            raise WorkflowError("format_retries skal være 0-3.")
        return Settings(endpoints, roles, workflow)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        raise WorkflowError(f"Konfiguration kunne ikke læses: {type(error).__name__}.") from error
