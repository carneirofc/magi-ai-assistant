"""Discord moderation tools bound to the current live conversation context."""

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

from agno.tools import tool
from agno.utils.log import log_info, log_warning
from pydantic import Field

from core.discord_context import get_current_discord_context

try:
    import discord

except (ImportError, ModuleNotFoundError):
    raise ImportError("`discord.py` not installed. Please install using `pip install discord.py`")

_DELETE_VERB_RE = re.compile(r"\b(delete|remove|purge|erase)\b", re.IGNORECASE)
_CLEAR_VERB_RE = re.compile(r"\bclear\b", re.IGNORECASE)
_CLEAR_SCOPE_RE = re.compile(r"\b(message|messages|post|posts|chat|history|recent|last)\b", re.IGNORECASE)


def _message_preview(message, limit: int = 200) -> str:
    text = " ".join((getattr(message, "content", "") or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _delete_intent_error(request_text: str) -> str | None:
    normalized = " ".join(request_text.split()).lower()
    if _DELETE_VERB_RE.search(normalized):
        return None
    if _CLEAR_VERB_RE.search(normalized) and _CLEAR_SCOPE_RE.search(normalized):
        return None
    return (
        "Refusing to delete Discord messages because the user's current request "
        "does not clearly ask for deleting messages. Use delete tools only for "
        "explicit delete/remove/purge/clear message requests."
    )


@tool(
    description="Return the exact guild, channel, and message context for the current Discord conversation.",
    instructions="Use before moderation actions or whenever exact Discord ids are needed. Never invent ids. Takes no arguments.",
    show_result=True,
)
def describe_current_discord_context() -> str:
    """Return the exact guild/channel/message context for THIS Discord conversation.

    Use this before moderation actions if you need to confirm where you are.
    Never invent ids; the host app already provides the live context here.
    """
    context = get_current_discord_context()
    return json.dumps(context.as_dict(), indent=2)


@tool(
    description="List recent messages in the current Discord channel with concrete message ids.",
    instructions="Use to identify message ids before deleting. Limit is clamped to 1-50; keep it small and practical.",
    show_result=True,
)
async def list_recent_discord_messages(
    limit: Annotated[int, Field(description="Number of recent messages to list, clamped to 1-50.")] = 20,
) -> str:
    """List recent messages in the current Discord conversation with concrete ids.

    Use this to identify which messages the user means before deleting anything.
    Keep `limit` small and practical: 1 to 50.
    """
    context = get_current_discord_context()
    limit = max(1, min(limit, 50))
    messages: list[dict[str, object]] = []

    async for message in context.channel.history(limit=limit):
        messages.append(
            {
                "message_id": str(message.id),
                "author": getattr(message.author, "name", None),
                "author_id": str(getattr(message.author, "id", "")),
                "created_at": getattr(message, "created_at", None).isoformat()
                if getattr(message, "created_at", None)
                else None,
                "pinned": bool(getattr(message, "pinned", False)),
                "has_attachments": bool(getattr(message, "attachments", None)),
                "content_preview": _message_preview(message),
            }
        )

    return json.dumps(
        {
            "channel_id": context.channel_id,
            "channel_name": context.channel_name,
            "messages": list(reversed(messages)),
        },
        indent=2,
    )


@tool(
    description="Delete one specific Discord message from the current channel by id.",
    instructions=(
        "Use only for explicit delete/remove/purge/clear-message requests after the target message is identified. "
        "This cannot edit, rename, or affect other channels."
    ),
    show_result=True,
)
async def delete_discord_message(
    message_id: Annotated[str, Field(description="Exact Discord message id to delete.")],
) -> str:
    """Delete one specific message from the current Discord conversation by id.

    Use only after the user clearly identifies the target message. This tool
    acts only in the current conversation's channel; it cannot delete elsewhere.
    Never use it for renaming threads, editing content, or any other non-delete
    action.
    """
    context = get_current_discord_context()
    target = f"{context.channel_name or context.channel_kind} ({context.channel_id})"
    if error := _delete_intent_error(context.message_text):
        log_warning(
            f"delete_discord_message: refused non-delete request in {target}: "
            f"{context.message_text!r}"
        )
        return error
    try:
        # Partial message issues only a DELETE; fetch_message would add a
        # needless GET per id and is what gets rate limited (429) in bulk.
        message = context.channel.get_partial_message(int(message_id))
        log_info(f"delete_discord_message: deleting {message_id} from {target}")
        await message.delete()
        log_info(f"delete_discord_message: deleted {message_id} from {target}")
        return f"Deleted message {message_id} from {target}."
    except discord.NotFound:
        log_warning(f"delete_discord_message: {message_id} not found in {target}")
        return f"Message {message_id} was not found in {target}."
    except discord.Forbidden as exc:
        log_warning(f"delete_discord_message: forbidden for {message_id} in {target} ({exc})")
        return f"Cannot delete message {message_id}: missing Discord permissions ({exc})."
    except discord.HTTPException as exc:
        log_warning(f"delete_discord_message: HTTP error for {message_id} in {target} ({exc})")
        return f"Discord rejected deleting message {message_id}: {exc}."


@tool(
    description="Delete several specific Discord messages from the current channel by id.",
    instructions=(
        "Prefer over repeated single-message deletes when the user identified multiple targets. "
        "Use only for explicit delete/remove/purge/clear-message requests."
    ),
    show_result=True,
)
async def delete_discord_messages(
    message_ids: Annotated[
        list[str],
        Field(description="Exact Discord message ids to delete from the current channel."),
    ],
) -> str:
    """Delete several messages in the current conversation by id in one call.

    Prefer this over calling the single-message tool in a loop: it deletes the
    whole batch in a single tool call, throttled to stay under Discord's rate
    limits. Pass the exact message ids the user identified. Never use it for
    renaming threads, editing content, or any other non-delete action.
    """
    context = get_current_discord_context()
    if not message_ids:
        return "No message ids were provided to delete."
    target = f"{context.channel_name or context.channel_kind} ({context.channel_id})"
    if error := _delete_intent_error(context.message_text):
        log_warning(
            f"delete_discord_messages: refused non-delete request in {target}: "
            f"{context.message_text!r}"
        )
        return error
    ids = ", ".join(str(mid) for mid in message_ids)
    messages = [context.channel.get_partial_message(int(mid)) for mid in message_ids]
    try:
        log_info(f"delete_discord_messages: deleting {len(messages)} message(s) from {target}: {ids}")
        await context.delete_messages(messages)
    except discord.Forbidden as exc:
        log_warning(f"delete_discord_messages: forbidden in {target} ({exc})")
        return f"Could not delete messages: missing permissions ({exc})."
    except discord.HTTPException as exc:
        log_warning(f"delete_discord_messages: HTTP error in {target} ({exc})")
        return f"Could not delete messages: Discord error ({exc})."
    log_info(f"delete_discord_messages: deleted {len(messages)} message(s) from {target}: {ids}")
    return f"Deleted {len(messages)} message(s) from {target}: {ids}"


@tool(
    description="Delete the most recent non-pinned messages in the current Discord channel.",
    instructions=(
        "Use only when the user explicitly asks to clear/delete the last N recent messages in this conversation. "
        "Count is clamped to 1-20; pinned and too-old messages are skipped."
    ),
    show_result=True,
)
async def delete_recent_discord_messages(
    count: Annotated[int, Field(description="Number of recent non-pinned messages to delete, clamped to 1-20.")],
) -> str:
    """Delete the most recent non-pinned messages in the current conversation.

    Use only when the user explicitly asks to clear the last N recent messages
    from THIS conversation. `count` must be between 1 and 20. Never use it for
    renaming threads, editing content, or any other non-delete action.
    """
    context = get_current_discord_context()
    count = max(1, min(count, 20))
    target = f"{context.channel_name or context.channel_kind} ({context.channel_id})"
    if error := _delete_intent_error(context.message_text):
        log_warning(
            f"delete_recent_discord_messages: refused non-delete request in {target}: "
            f"{context.message_text!r}"
        )
        return error
    cutoff = datetime.now(UTC) - timedelta(days=14)
    candidates = []

    async for message in context.channel.history(limit=count + 10):
        if str(message.id) == context.message_id:
            continue
        if getattr(message, "pinned", False):
            continue
        created_at = getattr(message, "created_at", None)
        if created_at is not None and created_at < cutoff:
            continue
        candidates.append(message)
        if len(candidates) >= count:
            break

    if not candidates:
        return "No recent deletable messages were found in the current conversation."

    ids = ", ".join(str(message.id) for message in candidates)
    try:
        # Deletes one at a time, throttled, to stay under the per-message DELETE
        # rate limits that the agent was hitting.
        log_info(
            f"delete_recent_discord_messages: deleting {len(candidates)} recent "
            f"message(s) from {target}: {ids}"
        )
        await context.delete_messages(candidates)
    except discord.Forbidden as exc:
        log_warning(f"delete_recent_discord_messages: forbidden in {target} ({exc})")
        return f"Could not delete messages: missing permissions ({exc})."
    except discord.HTTPException as exc:
        log_warning(f"delete_recent_discord_messages: HTTP error in {target} ({exc})")
        return f"Could not delete messages: Discord error ({exc})."

    log_info(
        f"delete_recent_discord_messages: deleted {len(candidates)} recent "
        f"message(s) from {target}: {ids}"
    )
    return f"Deleted {len(candidates)} recent message(s) from {target}: {ids}"


DISCORD_TOOLS = [
    describe_current_discord_context,
    list_recent_discord_messages,
    delete_discord_message,
    delete_discord_messages,
    delete_recent_discord_messages,
]
