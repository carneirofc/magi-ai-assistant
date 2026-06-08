"""Discord moderation tools bound to the current live conversation context."""

import json
from datetime import UTC, datetime, timedelta

from agno.tools import tool

from core.discord_context import get_current_discord_context

try:
    import discord

except (ImportError, ModuleNotFoundError):
    raise ImportError("`discord.py` not installed. Please install using `pip install discord.py`")


def _message_preview(message, limit: int = 200) -> str:
    text = " ".join((getattr(message, "content", "") or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


@tool
def describe_current_discord_context() -> str:
    """Return the exact guild/channel/message context for THIS Discord conversation.

    Use this before moderation actions if you need to confirm where you are.
    Never invent ids; the host app already provides the live context here.
    """
    context = get_current_discord_context()
    return json.dumps(context.as_dict(), indent=2)


@tool
async def list_recent_discord_messages(limit: int = 20) -> str:
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


@tool
async def delete_discord_message(message_id: str) -> str:
    """Delete one specific message from the current Discord conversation by id.

    Use only after the user clearly identifies the target message. This tool
    acts only in the current conversation's channel; it cannot delete elsewhere.
    """
    context = get_current_discord_context()
    try:
        message = await context.channel.fetch_message(int(message_id))
        await message.delete()
        return (
            f"Deleted message {message_id} from "
            f"{context.channel_name or context.channel_kind} ({context.channel_id})."
        )
    except discord.NotFound:
        return (
            f"Message {message_id} was not found in "
            f"{context.channel_name or context.channel_kind} ({context.channel_id})."
        )
    except discord.Forbidden as exc:
        return f"Cannot delete message {message_id}: missing Discord permissions ({exc})."
    except discord.HTTPException as exc:
        return f"Discord rejected deleting message {message_id}: {exc}."


@tool
async def delete_recent_discord_messages(count: int) -> str:
    """Delete the most recent non-pinned messages in the current conversation.

    Use only when the user explicitly asks to clear the last N recent messages
    from THIS conversation. `count` must be between 1 and 20.
    """
    context = get_current_discord_context()
    count = max(1, min(count, 20))
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

    deleted_ids: list[str] = []
    for message in candidates:
        try:
            await message.delete()
            deleted_ids.append(str(message.id))
        except discord.Forbidden as exc:
            return f"Stopped after deleting {len(deleted_ids)} message(s): missing permissions ({exc})."
        except discord.HTTPException as exc:
            return f"Stopped after deleting {len(deleted_ids)} message(s): Discord error ({exc})."

    return (
        f"Deleted {len(deleted_ids)} recent message(s) from "
        f"{context.channel_name or context.channel_kind} ({context.channel_id}): "
        f"{', '.join(deleted_ids)}"
    )


DISCORD_TOOLS = [
    describe_current_discord_context,
    list_recent_discord_messages,
    delete_discord_message,
    delete_recent_discord_messages,
]
