"""Reminders — deliberate, per-user, file-backed; surfaced when she next speaks.

A reminder is a small JSON record under the user's memory tree
(`users/<user>/reminders.json`): `{id, text, due, created, done}`. The model
sets/lists/cancels them via the tools here; delivery is deliberately modest —
there is no push infrastructure, so due reminders surface where the assistant
already speaks first: the greeting turn (the channel folds
`due_reminders_text` into the greeting instruction) and `GET /v1/reminders`
for a client panel. Honest v1: a reminder fires when you next open a
conversation, not as an OS notification.

Gated by `reminders_enabled`; `build_reminder_tools()` returns [] when off.
Pure file IO on the same tree the rest of deliberate memory uses (and thus
covered by memory-git versioning when that's on).
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from agno.tools import tool
from agno.utils.log import log_info, log_warning
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, fail, ok
from magi.core.config import config
from magi.core.memory import MemoryManager
from magi.core.memory.adapters import slug


class Reminder(BaseModel):
    id: str
    text: str
    due: str = Field(description="When it's due (ISO date or datetime, user-local).")
    created: str = ""
    done: bool = False


class ReminderData(BaseModel):
    reminder: Reminder


class ReminderListData(BaseModel):
    reminders: list[Reminder] = Field(default_factory=list)
    count: int = 0


def _reminders_path(root: Path, user_id: str) -> Path:
    return root / "users" / slug(user_id) / "reminders.json"


def read_reminders(root: Path, user_id: str) -> list[dict]:
    path = _reminders_path(root, user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log_warning(f"reminders: unreadable {path.name} ({type(exc).__name__}: {exc})")
        return []
    return data if isinstance(data, list) else []


def _write_reminders(root: Path, user_id: str, items: list[dict]) -> None:
    path = _reminders_path(root, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    # Ride the memory write observer so git-backed memory versions reminders too.
    from magi.core.memory.adapters import emit_write

    emit_write(path)


def _parse_due(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def due_reminders_text(root: Path, user_id: str, now: Optional[datetime] = None) -> str:
    """The user's due, not-done reminders as lines for the greeting instruction
    ('' when none). A date-only `due` counts as due from that day's start."""
    now = now or datetime.now()
    lines = []
    for item in read_reminders(root, user_id):
        if item.get("done"):
            continue
        due = _parse_due(str(item.get("due") or ""))
        if due is not None and due <= now:
            lines.append(f"- ({item.get('due')}) {item.get('text', '')}")
    return "\n".join(lines)


def build_reminder_tools(memory: MemoryManager) -> list:
    """The reminder tool set bound to `memory` (for scope + the tree root), or
    [] when reminders are off."""
    if not config.reminders_enabled:
        return []
    root = memory.store.root

    def _items() -> list[dict]:
        return read_reminders(root, memory.scope().user_id)

    def _save(items: list[dict]) -> None:
        _write_reminders(root, memory.scope().user_id, items)

    @tool(
        description="Set a reminder for the current user, delivered when they next talk to you after it's due.",
        instructions=(
            "Use when the user asks to be reminded of something. `due` must be an ISO "
            "date (2026-07-15) or datetime (2026-07-15T09:00) in the user's local "
            "time — resolve relative phrasing ('tomorrow', 'next friday') against the "
            "current date yourself. Be honest about delivery: the reminder surfaces "
            "when they next open a conversation after it's due, not as a push "
            "notification."
        ),
        show_result=True,
    )
    def set_reminder(
        text: Annotated[str, Field(min_length=2, description="What to remind them about.")],
        due: Annotated[
            str,
            Field(min_length=8, description="ISO date or datetime when it's due (user-local)."),
        ],
    ) -> ToolOutput[ReminderData]:
        """Store a reminder; it surfaces in greetings once due."""
        if _parse_due(due.strip()) is None:
            return fail(
                f"Couldn't parse {due!r} as an ISO date/datetime — resolve the date "
                "yourself and pass e.g. 2026-07-15 or 2026-07-15T09:00."
            )
        record = {
            "id": uuid.uuid4().hex[:8],
            "text": text.strip(),
            "due": due.strip(),
            "created": datetime.now().isoformat(timespec="seconds"),
            "done": False,
        }
        items = _items()
        items.append(record)
        _save(items)
        log_info(f"reminders: set {record['id']} due {record['due']}")
        return ok(
            f"Reminder set for {record['due']}. It will surface when they next talk to you after that.",
            ReminderData(reminder=Reminder(**record)),
        )

    @tool(
        description="List the current user's reminders (pending and done).",
        instructions="Use to review or confirm reminders. Takes no arguments.",
        show_result=True,
    )
    def list_reminders() -> ToolOutput[ReminderListData]:
        """All of the current user's reminders, pending first."""
        items = sorted(_items(), key=lambda r: (bool(r.get("done")), str(r.get("due", ""))))
        reminders = [Reminder(**{**r, "id": str(r.get("id", ""))}) for r in items]
        return ok(
            f"{len(reminders)} reminder(s).",
            ReminderListData(reminders=reminders, count=len(reminders)),
        )

    @tool(
        description="Mark a reminder done (or cancel it) by its id.",
        instructions=(
            "Use when the user says a reminder is handled or no longer wanted. The id "
            "comes from list_reminders or the set_reminder confirmation."
        ),
        show_result=True,
    )
    def cancel_reminder(
        reminder_id: Annotated[str, Field(min_length=1, description="The reminder's id.")],
    ) -> ToolOutput[ReminderData]:
        """Mark one reminder done; unknown ids are a failure, not a silent success."""
        items = _items()
        for item in items:
            if str(item.get("id")) == reminder_id.strip():
                item["done"] = True
                _save(items)
                log_info(f"reminders: done {reminder_id.strip()}")
                return ok(
                    "Marked done.",
                    ReminderData(reminder=Reminder(**{**item, "id": str(item.get("id", ""))})),
                )
        return fail(f"No reminder with id {reminder_id.strip()!r}.")

    return [set_reminder, list_reminders, cancel_reminder]
