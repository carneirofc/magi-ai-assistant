"""The sandbox as one service: policy → approval → backend → audit.

`request()` is what the model's `run_command` tool calls; `decide()` is what a
user's `/approve <id>` or `/deny <id>` message triggers (handled once, in
`ConversationService`, so every channel supports it). Only users listed in
`sandbox.allowed_users` (scoped ids like `discord:123`) may run anything.
Every request and decision is appended to an audit JSONL file.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from magi.core.log import log_warning
from magi.core.sandbox.approvals import ApprovalStore
from magi.core.sandbox.backend import ExecBackend, ExecResult
from magi.core.sandbox.policy import Verdict, classify

type ApprovalMode = Literal["always", "dangerous", "deny_dangerous"]
_APPROVAL_RE = re.compile(r"^\s*[/!](approve|deny)\s+([0-9a-f]{6})\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ExecOutcome:
    status: Literal["ran", "pending", "refused"]
    verdict: Verdict
    result: ExecResult | None = None
    approval_id: str | None = None
    reason: str = ""
    command: str = ""


def parse_approval(text: str) -> tuple[bool, str] | None:
    """`/approve abc123` → (True, "abc123"); `/deny …` → (False, …); else None."""
    match = _APPROVAL_RE.match(text)
    if match is None:
        return None
    return match.group(1).lower() == "approve", match.group(2).lower()


def approval_turn_text(approval_id: str, outcome: ExecOutcome | None) -> str:
    """What the model sees in place of a `/approve` / `/deny` message."""
    if outcome is None:
        return (
            f"[The user sent /approve or /deny for {approval_id}, but no such pending "
            "command exists for them (unknown, expired, or someone else's). Tell them.]"
        )
    if outcome.status != "ran" or outcome.result is None:
        return (
            f"[The user DENIED command {approval_id}: `{outcome.command}`. It did not run. "
            "Don't retry it; ask how they'd like to proceed.]"
        )
    result = outcome.result
    note = " (timed out and was killed)" if result.timed_out else ""
    return (
        f"[The user approved command {approval_id}: `{outcome.command}`. It ran with exit "
        f"code {result.exit_code}{note}. Output:]\n```\n{result.output}\n```\n"
        "[Continue the task and tell the user what happened.]"
    )


def workspace_name(user_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", user_id)[:80] or "anonymous"


class SandboxService:
    def __init__(
        self,
        backend: ExecBackend,
        *,
        workspace_root: Path,
        audit_path: Path,
        allowed_users: list[str],
        approval: ApprovalMode,
        timeout_seconds: float,
        approvals: ApprovalStore,
    ) -> None:
        self.backend = backend
        self.workspace_root = workspace_root
        self.audit_path = audit_path
        self.allowed_users = frozenset(allowed_users)
        self.approval = approval
        self.timeout = timeout_seconds
        self.approvals = approvals

    def workspace(self, user_id: str) -> Path:
        return self.workspace_root / workspace_name(user_id)

    def request(self, *, user_id: str, session_id: str, command: str) -> ExecOutcome:
        verdict = classify(command)
        if user_id not in self.allowed_users:
            return self._refuse(user_id, command, verdict, "this user may not run commands")
        if verdict == "forbidden":
            return self._refuse(user_id, command, verdict, "this command is never allowed")
        if verdict == "dangerous" and self.approval == "deny_dangerous":
            return self._refuse(user_id, command, verdict, "risky commands are disabled here")
        if self.approval == "always" or verdict == "dangerous":
            pending = self.approvals.request(
                user_id=user_id, session_id=session_id, command=command
            )
            self._audit(user_id, command, verdict, "pending", approval_id=pending.id)
            return ExecOutcome(
                status="pending", verdict=verdict, approval_id=pending.id, command=command
            )
        return self._run(user_id, command, verdict, "auto")

    def decide(self, *, user_id: str, approval_id: str, approve: bool) -> ExecOutcome | None:
        """Resolve one pending approval; None when unknown, expired, or not theirs."""
        pending = self.approvals.take(approval_id, user_id=user_id)
        if pending is None:
            return None
        verdict = classify(pending.command)
        if not approve:
            self._audit(user_id, pending.command, verdict, "denied", approval_id=approval_id)
            return ExecOutcome(
                status="refused",
                verdict=verdict,
                reason="denied by the user",
                approval_id=approval_id,
                command=pending.command,
            )
        return self._run(user_id, pending.command, verdict, "approved", approval_id=approval_id)

    def _run(
        self,
        user_id: str,
        command: str,
        verdict: Verdict,
        how: str,
        *,
        approval_id: str | None = None,
    ) -> ExecOutcome:
        result = self.backend.run(command, workspace=self.workspace(user_id), timeout=self.timeout)
        self._audit(
            user_id, command, verdict, how, approval_id=approval_id, exit_code=result.exit_code
        )
        return ExecOutcome(
            status="ran", verdict=verdict, result=result, approval_id=approval_id, command=command
        )

    def _refuse(self, user_id: str, command: str, verdict: Verdict, reason: str) -> ExecOutcome:
        self._audit(user_id, command, verdict, "refused", reason=reason)
        return ExecOutcome(status="refused", verdict=verdict, reason=reason, command=command)

    def _audit(
        self,
        user_id: str,
        command: str,
        verdict: Verdict,
        decision: str,
        *,
        approval_id: str | None = None,
        exit_code: int | None = None,
        reason: str = "",
    ) -> None:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "user": user_id,
            "backend": self.backend.name,
            "command": command,
            "verdict": verdict,
            "decision": decision,
            "approval_id": approval_id,
            "exit_code": exit_code,
            "reason": reason,
        }
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log_warning(f"sandbox: audit write failed: {exc}")
