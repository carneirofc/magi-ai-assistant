"""Identity tools: let the bot look at, or hand over, its own profile picture.

The bot's appearance is a picture on disk (the global identity, see
magi/core/identity). It is deliberately NOT fed into the model's context each
turn — a standing image reads as user-supplied content and derails the model.
Instead the run context just *tells* the model it has one, and these two tools
let it pull the picture in on demand, mirroring the URL-media split:

  - `view_profile_picture` loads the bytes into the model's own context (like
    `view_image_from_url`) so it can actually see itself and describe it.
  - `send_profile_picture` stages the bytes in the run's media outbox (like
    `send_media_from_url`) so the picture is delivered to the user as a real
    attachment.

Both are bound to the injected `MemoryManager` (its `store.identity`), so they
always act on the *current* picture, and both degrade to an honest message when
no picture is set.
"""

from agno.media import Image
from agno.tools import tool
from agno.tools.function import ToolResult
from agno.utils.log import log_info

from magi.core.identity import _MIME_EXT
from magi.core.media import stage_bytes, view_only_id
from magi.core.memory import MemoryManager


def _no_picture() -> str:
    return (
        "You don't have a profile picture set right now, so there's nothing to show. "
        "Tell the user you don't have one yet."
    )


def build_identity_tools(memory: MemoryManager) -> list:
    """The bot's own-picture tools, bound to `memory`'s identity store.

    Empty of nothing — always two tools; each checks for a picture at call time
    (so a deployment that never sets one just gets a graceful "no picture" reply).
    """

    @tool(
        description="Load your own profile picture into your context so you can actually see your appearance.",
        instructions=(
            "Use when you need to look at your OWN appearance — e.g. the user asks what you look like and you "
            "want to describe your picture accurately. This loads your profile picture into your context; it is "
            "view-only and is NOT sent to the user. To hand the picture to the user, use send_profile_picture."
        ),
        show_result=True,
    )
    def view_profile_picture() -> ToolResult:
        """Load your own profile picture into your context so you can see it.

        Your appearance is a real picture, but you are not shown it every turn.
        Call this when you need to actually look at it — for instance to describe
        what you look like precisely. After this, the image is in your context and
        you can reason about its real contents instead of guessing.

        This is for YOU to look at — it is not delivered to the user. To send the
        user your picture, use `send_profile_picture` instead. Returns a message
        saying you have no picture when none is set.
        """
        avatar = memory.store.identity.avatar_bytes()
        if avatar is None:
            return ToolResult(content=_no_picture())
        data, mime = avatar
        log_info(f"view_profile_picture: loaded own avatar ({len(data)} bytes, {mime})")
        return ToolResult(
            content="Loaded your profile picture into your context — you can see it now.",
            # view-only id: model input, not a deliverable — reply-media collection
            # (magi/core/media.py) must not repost it to the user.
            images=[
                Image(id=view_only_id(), content=data, mime_type=mime, format=_MIME_EXT.get(mime)),
            ],
        )

    @tool(
        description="Attach your own profile picture to your reply so the user can see what you look like.",
        instructions=(
            "Use when the user asks to SEE your picture / what you look like and wants the actual image. This "
            "delivers your profile picture to the user as a real attachment; it does NOT load it into your own "
            "context (use view_profile_picture for that). Don't also paste a link — the picture rides the reply."
        ),
        show_result=True,
    )
    def send_profile_picture() -> ToolResult:
        """Send the user your own profile picture as a real attachment.

        Use when the user wants to see what you look like and expects the actual
        image, not a description. The picture is attached to your reply by the
        host; don't paste a URL as well.

        This does NOT load the picture into your own context — to look at it
        yourself use `view_profile_picture`. Returns a message saying you have no
        picture when none is set, or that delivery isn't available in this run.
        """
        ident = memory.store.identity
        avatar = ident.avatar_bytes()
        if avatar is None:
            return ToolResult(content=_no_picture())
        data, mime = avatar
        filename = ident.read().avatar_filename or f"profile.{_MIME_EXT.get(mime, 'png')}"
        kind, staged = stage_bytes(data, mime, filename)
        if not staged:
            # No outbox open (bare run outside ConversationService) — be honest.
            return ToolResult(
                content="Picture delivery isn't available in this run, so nothing was sent."
            )
        log_info(f"send_profile_picture: staged own avatar '{filename}' ({len(data)} bytes, {mime})")
        return ToolResult(
            content=(
                f"Attached your profile picture ('{filename}') to your reply — it will be "
                "delivered to the user with your message. Don't paste a link as well."
            ),
        )

    return [view_profile_picture, send_profile_picture]
