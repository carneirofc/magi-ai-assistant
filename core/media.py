"""Channel-neutral reply media: a per-run outbox plus run-output collection.

Two paths bring media into a `ConversationReply`:

1. The media outbox — a ContextVar holding this run's outgoing media. Tools
   (agent/tools/media.py) *stage* deliverables here deliberately: the bytes
   never enter the model's context (a vision-only backend chokes on audio
   parts, and a big image would burn tokens for nothing) — they ride straight
   to the channel with the reply. Same per-run ContextVar pattern as
   `core.discord_context`.
2. Run-output media — whatever agno aggregated onto the RunOutput (tool-result
   media, member-run media, model-generated media). `view_image_from_url`
   marks its images VIEW-ONLY (the model loads them to *look*, not to repost),
   so collection drops those.

`ConversationService` opens the outbox before each run and merges both paths
into the reply after.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass, field, fields
from typing import Any, Optional
from uuid import uuid4

from agno.media import Audio, File, Image, Video

# Id prefix for images the model fetched to look at (view_image_from_url).
# They are model input, not deliverables — reply collection skips them.
VIEW_ONLY_ID_PREFIX = "view-only:"


def view_only_id() -> str:
    return f"{VIEW_ONLY_ID_PREFIX}{uuid4()}"


def is_view_only(item: Any) -> bool:
    return str(getattr(item, "id", "") or "").startswith(VIEW_ONLY_ID_PREFIX)


@dataclass
class MediaOutbox:
    """Media staged during one run for delivery with the reply."""

    images: list[Image] = field(default_factory=list)
    videos: list[Video] = field(default_factory=list)
    audio: list[Audio] = field(default_factory=list)
    files: list[File] = field(default_factory=list)


_OUTBOX: ContextVar[Optional[MediaOutbox]] = ContextVar("media_outbox", default=None)


def open_media_outbox() -> Token:
    """Install a fresh outbox for this run; returns the token for `close`."""
    return _OUTBOX.set(MediaOutbox())


def close_media_outbox(token: Token) -> MediaOutbox:
    """Drain and uninstall the current outbox.

    Tools may run in copied contexts (asyncio tasks), but they mutate the same
    `MediaOutbox` object installed here, so everything staged is visible.
    """
    outbox = _OUTBOX.get() or MediaOutbox()
    _OUTBOX.reset(token)
    return outbox


def stage_media(
    *,
    images: tuple[Image, ...] = (),
    videos: tuple[Video, ...] = (),
    audio: tuple[Audio, ...] = (),
    files: tuple[File, ...] = (),
) -> bool:
    """Add media to the current run's outbox.

    Returns False when no outbox is open (a bare run outside
    `ConversationService`) — the caller should then report the URL instead of
    claiming delivery.
    """
    outbox = _OUTBOX.get()
    if outbox is None:
        return False
    outbox.images.extend(images)
    outbox.videos.extend(videos)
    outbox.audio.extend(audio)
    outbox.files.extend(files)
    return True


def collect_reply_media(response: Any, outbox: Optional[MediaOutbox] = None) -> dict[str, tuple]:
    """Merge run-output media (minus view-only) with the outbox.

    Returns the media kwargs for `ConversationReply` — one tuple per kind,
    deduped by media id (agno auto-ids every media object).
    """
    merged: dict[str, list] = {"images": [], "videos": [], "audio": [], "files": []}
    seen: set[str] = set()

    def add(kind: str, items) -> None:
        for item in items or []:
            if item is None or is_view_only(item):
                continue
            item_id = getattr(item, "id", None)
            if item_id is not None:
                if item_id in seen:
                    continue
                seen.add(item_id)
            merged[kind].append(item)

    if response is not None:
        add("images", getattr(response, "images", None))
        add("videos", getattr(response, "videos", None))
        add("audio", getattr(response, "audio", None))
        add("files", getattr(response, "files", None))
        # Model-spoken audio (e.g. an audio-output model) is a deliverable too.
        response_audio = getattr(response, "response_audio", None)
        if response_audio is not None:
            add("audio", [response_audio])
    if outbox is not None:
        for f in fields(outbox):
            add(f.name, getattr(outbox, f.name))

    return {kind: tuple(items) for kind, items in merged.items()}
