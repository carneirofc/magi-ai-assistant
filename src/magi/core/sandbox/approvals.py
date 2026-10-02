"""Pending command approvals — short-lived, per user, one-shot."""

import secrets
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingApproval:
    id: str
    user_id: str
    session_id: str
    command: str
    created: float


class ApprovalStore:
    """In-memory (a restart drops pending approvals, by design)."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl = ttl_seconds
        self._now = time.monotonic
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def request(self, *, user_id: str, session_id: str, command: str) -> PendingApproval:
        with self._lock:
            self._expire()
            approval = PendingApproval(
                id=secrets.token_hex(3),
                user_id=user_id,
                session_id=session_id,
                command=command,
                created=self._now(),
            )
            self._pending[approval.id] = approval
            return approval

    def take(self, approval_id: str, *, user_id: str) -> PendingApproval | None:
        """Remove and return the approval — only for the user who asked for it."""
        with self._lock:
            self._expire()
            approval = self._pending.get(approval_id)
            if approval is None or approval.user_id != user_id:
                return None
            del self._pending[approval_id]
            return approval

    def _expire(self) -> None:
        cutoff = self._now() - self.ttl
        for key in [k for k, a in self._pending.items() if a.created < cutoff]:
            del self._pending[key]
