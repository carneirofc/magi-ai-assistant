"""`magi doctor` — is this deployment able to run?

Each check reads the effective `Config` and returns ok / warn / fail with a
one-line detail. Network probes go through `http_status` so tests replace it;
nothing here mutates state. Exit code is 1 when any check fails.
"""

import importlib.util
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TextIO

import httpx

from magi.core.config import Config

type Status = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str


type Check = Callable[[Config], list[CheckResult]]

_TIMEOUT_S = 3.0


def http_status(url: str, headers: dict[str, str] | None = None) -> int | str:
    """The HTTP status for GET `url`, or the error text when unreachable."""
    try:
        return httpx.get(url, headers=headers, timeout=_TIMEOUT_S).status_code
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}: {exc}"


def _probe(name: str, url: str, headers: dict[str, str] | None = None) -> CheckResult:
    status = http_status(url, headers)
    if isinstance(status, int) and status < 400:
        return CheckResult(name, "ok", f"{url} → {status}")
    if isinstance(status, int) and status in (401, 403):
        return CheckResult(name, "fail", f"{url} → {status} (check the API key)")
    return CheckResult(name, "fail", f"{url} → {status}")


def _bearer(key: str | None) -> dict[str, str] | None:
    return {"Authorization": f"Bearer {key}"} if key else None


def check_model(cfg: Config) -> list[CheckResult]:
    match cfg.model_provider:
        case "llamacpp":
            url = f"{cfg.llamacpp_base_url.rstrip('/')}/models"
            return [_probe("model (llamacpp)", url, _bearer(cfg.llamacpp_api_key))]
        case "openai":
            if not cfg.openai_api_key:
                return [CheckResult("model (openai)", "fail", "OPENAI_API_KEY is not set")]
            url = f"{cfg.openai_base_url.rstrip('/')}/models"
            return [_probe("model (openai)", url, _bearer(cfg.openai_api_key))]
        case "litellm":
            url = f"{cfg.litellm_base_url.rstrip('/')}/v1/models"
            return [_probe("model (litellm)", url, _bearer(cfg.litellm_api_key))]
        case "ollama":
            return [_probe("model (ollama)", f"{cfg.ollama_host.rstrip('/')}/api/tags")]


def check_channels(cfg: Config) -> list[CheckResult]:
    out: list[CheckResult] = []
    enabled = set(cfg.channels.enabled)
    out.append(CheckResult("channels", "ok", ", ".join(cfg.channels.enabled) or "none"))
    if "discord" in enabled and not cfg.DISCORD_BOT_TOKEN:
        out.append(CheckResult("discord token", "fail", "DISCORD_BOT_TOKEN is not set"))
    if "telegram" in enabled:
        if not cfg.telegram_bot_token:
            out.append(CheckResult("telegram token", "fail", "TELEGRAM_BOT_TOKEN is not set"))
        if not cfg.telegram_allowed_users:
            out.append(
                CheckResult(
                    "telegram access",
                    "warn",
                    "telegram_allowed_users is empty — everyone is refused (send /whoami)",
                )
            )
    public_api = "api" in enabled and cfg.api_host not in ("127.0.0.1", "localhost", "::1")
    if public_api and not cfg.api_auth_token:
        out.append(
            CheckResult("api auth", "warn", f"api binds {cfg.api_host} with no API_AUTH_TOKEN")
        )
    admin_on = "admin" in enabled or cfg.admin_enabled
    if admin_on and not cfg.admin_auth_token:
        out.append(CheckResult("admin auth", "warn", "admin surface has no ADMIN_AUTH_TOKEN"))
    return out


# Feature flag → (import name, extra) it needs.
def _needed_extras(cfg: Config) -> list[tuple[str, str, str]]:
    needs: list[tuple[str, str, str]] = []
    if cfg.semantic_memory or cfg.knowledge_enabled or cfg.items_archive_enabled:
        needs.append(("qdrant/knowledge", "qdrant_client", "semantic"))
    if cfg.storage_enabled and cfg.storage_backend == "s3":
        needs.append(("s3 storage", "boto3", "s3"))
    if cfg.mcp_servers or cfg.seanime_use_mcp:
        needs.append(("mcp servers", "mcp", "mcp"))
    if cfg.memory_git_enabled:
        needs.append(("git memory", "git", "git"))
    if cfg.websearch_enabled:
        needs.append(("web search", "ddgs", "websearch"))
    if "telegram" in cfg.channels.enabled:
        needs.append(("telegram bot", "telegram", "telegram"))
    if "desktop" in cfg.channels.enabled:
        needs.append(("desktop shell", "PySide6", "desktop"))
    return needs


def check_extras(cfg: Config) -> list[CheckResult]:
    out: list[CheckResult] = []
    for feature, module, extra in _needed_extras(cfg):
        if importlib.util.find_spec(module) is None:
            out.append(
                CheckResult(
                    f"extra: {extra}", "fail", f"{feature} needs `{module}` — install [{extra}]"
                )
            )
        else:
            out.append(CheckResult(f"extra: {extra}", "ok", f"{module} importable"))
    return out


def check_qdrant(cfg: Config) -> list[CheckResult]:
    if not (cfg.semantic_memory or cfg.knowledge_enabled or cfg.items_archive_enabled):
        return []
    headers = {"api-key": cfg.qdrant_api_key} if cfg.qdrant_api_key else None
    return [_probe("qdrant", f"{cfg.qdrant_url.rstrip('/')}/readyz", headers)]


def check_sqlite_fts5(cfg: Config) -> list[CheckResult]:
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    except sqlite3.OperationalError as exc:
        status: Status = "fail" if cfg.session_search_enabled else "warn"
        return [CheckResult("sqlite fts5", status, f"unavailable ({exc}); session search off")]
    state = "on" if cfg.session_search_enabled else "off (session_search_enabled)"
    return [CheckResult("session search", "ok", f"FTS5 available; {state}")]


def docker_reachable() -> str | None:
    """None when the Docker daemon answers, else the error text."""
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:  # noqa: BLE001 — any failure means "not usable".
        return f"{type(exc).__name__}: {exc}"
    return None


def check_sandbox(cfg: Config) -> list[CheckResult]:
    sb = cfg.sandbox
    if sb.backend == "off":
        return []
    out: list[CheckResult] = []
    if sb.backend == "local":
        out.append(
            CheckResult("sandbox", "warn", "LOCAL backend: commands run unisolated as this user")
        )
    elif (error := docker_reachable()) is not None:
        out.append(CheckResult("sandbox", "fail", f"docker backend, daemon unreachable: {error}"))
    else:
        out.append(
            CheckResult("sandbox", "ok", f"docker ({sb.docker_image}), approval={sb.approval}")
        )
    if not sb.allowed_users:
        out.append(
            CheckResult(
                "sandbox users", "warn", "sandbox.allowed_users is empty — nobody can run commands"
            )
        )
    return out


def check_skills(cfg: Config) -> list[CheckResult]:
    from magi.agent.toolsets import TOOLSETS
    from magi.core.skills_fs import SKILL_FILE, SkillFileError, load_skill, skill_dirs

    out: list[CheckResult] = []
    count = 0
    for base in skill_dirs():
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not (child / SKILL_FILE).is_file():
                continue
            try:
                load_skill(child)
                count += 1
            except SkillFileError as exc:
                out.append(CheckResult(f"skill {child.name}", "warn", str(exc)))
    out.insert(0, CheckResult("skills", "ok", f"{count} file skill(s)"))
    if typos := [n for n in cfg.toolsets.disabled if n not in TOOLSETS]:
        out.append(CheckResult("toolsets", "warn", f"unknown in toolsets.disabled: {typos}"))
    if cfg.skills.agent_write == "propose" and not cfg.evolution_enabled:
        out.append(
            CheckResult(
                "skill writing",
                "warn",
                "agent_write=propose needs evolution_enabled — the assistant can't save skills",
            )
        )
    return out


def check_memory_learning(cfg: Config) -> list[CheckResult]:
    """Whether the assistant can learn at all: nothing but the curator writes
    durable memory, and persona learning may route through the queue."""
    if not cfg.memory_curation:
        return [
            CheckResult(
                "memory learning",
                "warn",
                "memory_curation is off — nothing records durable facts, episodes, or "
                "persona rules; memory stays read-only",
            )
        ]
    out = [
        CheckResult("memory learning", "ok", f"curator on, persona_learning={cfg.persona_learning}")
    ]
    if cfg.persona_learning == "propose" and not cfg.evolution_enabled:
        out.append(
            CheckResult(
                "persona learning",
                "warn",
                "persona_learning=propose needs evolution_enabled — learned persona "
                "rules are dropped",
            )
        )
    return out


CHECKS: list[Check] = [
    check_channels,
    check_model,
    check_extras,
    check_qdrant,
    check_sqlite_fts5,
    check_skills,
    check_memory_learning,
    check_sandbox,
]

_ICON: dict[Status, str] = {"ok": "✓", "warn": "!", "fail": "✗"}


def run_checks(cfg: Config, checks: list[Check] | None = None) -> list[CheckResult]:
    results: list[CheckResult] = []
    for check in checks if checks is not None else CHECKS:
        try:
            results.extend(check(cfg))
        except Exception as exc:  # noqa: BLE001 — a broken check is itself a finding.
            results.append(CheckResult(check.__name__, "fail", f"check crashed: {exc}"))
    return results


def report(results: list[CheckResult], out: TextIO) -> int:
    """Print `results`; the exit code is 1 when anything failed."""
    width = max((len(r.name) for r in results), default=0)
    for r in results:
        out.write(f"{_ICON[r.status]} {r.name.ljust(width)}  {r.detail}\n")
    failed = sum(r.status == "fail" for r in results)
    warned = sum(r.status == "warn" for r in results)
    out.write(f"\n{failed} failed, {warned} warning(s), {len(results)} checks\n")
    return 1 if failed else 0
