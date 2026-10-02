"""Sandboxed command execution for the `run_command` tool (see service.py)."""

from pathlib import Path

from magi.core.config import config
from magi.core.config_file import magi_home
from magi.core.log import log_info, log_warning
from magi.core.sandbox.approvals import ApprovalStore
from magi.core.sandbox.backend import ExecBackend, ExecResult
from magi.core.sandbox.policy import classify
from magi.core.sandbox.service import ExecOutcome, SandboxService, parse_approval


def build_sandbox_from_config() -> SandboxService | None:
    """The configured sandbox, or None when `sandbox.backend` is off."""
    cfg = config.sandbox
    backend: ExecBackend
    match cfg.backend:
        case "off":
            return None
        case "local":
            from magi.core.sandbox.local_backend import LocalBackend

            log_warning("sandbox: LOCAL backend — commands run unisolated as this user")
            backend = LocalBackend(cfg.output_max_chars)
        case "docker":
            from magi.core.sandbox.docker_backend import DockerBackend

            backend = DockerBackend(
                image=cfg.docker_image,
                network=cfg.docker_network,
                memory=cfg.docker_memory,
                pids=cfg.docker_pids,
                cpus=cfg.docker_cpus,
                output_limit=cfg.output_max_chars,
            )
    if not cfg.allowed_users:
        log_warning("sandbox: sandbox.allowed_users is empty — nobody can run commands")
    home = magi_home()
    log_info(f"sandbox: ENABLED (backend={cfg.backend}, approval={cfg.approval})")
    return SandboxService(
        backend,
        workspace_root=Path(cfg.workspace_dir).expanduser()
        if cfg.workspace_dir
        else home / "workspace",
        audit_path=home / "logs" / "exec.jsonl",
        allowed_users=list(cfg.allowed_users),
        approval=cfg.approval,
        timeout_seconds=cfg.timeout_seconds,
        approvals=ApprovalStore(cfg.approval_ttl_seconds),
    )


__all__ = [
    "ApprovalStore",
    "ExecOutcome",
    "ExecResult",
    "SandboxService",
    "build_sandbox_from_config",
    "classify",
    "parse_approval",
]
