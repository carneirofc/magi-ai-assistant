"""Channel-neutral reply media: a per-run outbox plus run-output collection.

Two paths bring media into a `ConversationReply`:

1. The media outbox — a ContextVar holding this run's outgoing media. Tools
   (magi/agent/tools/media.py) *stage* deliverables here deliberately: the bytes
   never enter the model's context (a vision-only backend chokes on audio
   parts, and a big image would burn tokens for nothing) — they ride straight
   to the channel with the reply. Same per-run ContextVar pattern as
   `magi.core.discord_context`.
2. Run-output media — whatever agno aggregated onto the RunOutput (tool-result
   media, member-run media, model-generated media). `view_image_from_url`
   marks its images VIEW-ONLY (the model loads them to *look*, not to repost),
   so collection drops those.

`ConversationService` opens the outbox before each run and merges both paths
into the reply after.
"""

import re
from collections.abc import Iterable
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
_ALLOWED_MEDIA_URLS: ContextVar[Optional[set[str]]] = ContextVar(
    "allowed_media_urls", default=None
)
_HTTP_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)


def _clean_url(url: str) -> str:
    return (url or "").strip().rstrip(".,;:!?)\"]}")


def extract_http_urls(text: str) -> set[str]:
    """Direct URLs the user supplied in the current message."""
    return {_clean_url(m.group(0)) for m in _HTTP_URL_RE.finditer(text or "")}


def open_allowed_media_urls(
    user_text: str = "", extra_urls: Iterable[str] = ()
) -> Token:
    """Start this run's allowlist for media URLs a tool may fetch.

    Tools may add source-provided URLs as they run. Direct user URLs are seeded
    here so "send this image" / "look at this image" still works without a
    source-specific lookup. `extra_urls` seeds URLs the user attached out of band
    — e.g. an inbound image passed by reference rather than pasted in the text —
    so the same by-reference image stays fetchable for inspection.
    """
    seeded = extract_http_urls(user_text)
    seeded.update(_clean_url(u) for u in extra_urls if u)
    return _ALLOWED_MEDIA_URLS.set(seeded)


def close_allowed_media_urls(token: Token) -> None:
    _ALLOWED_MEDIA_URLS.reset(token)


def allow_media_url(url: str | None) -> None:
    """Mark one source-returned URL as deliverable during the current run."""
    cleaned = _clean_url(url or "")
    if not cleaned:
        return
    allowed = _ALLOWED_MEDIA_URLS.get()
    if allowed is not None:
        allowed.add(cleaned)


def is_media_url_allowed(url: str) -> bool:
    """Whether a media tool may fetch this URL in the current run.

    Gates both delivery (`send_media_from_url`) and inspection
    (`view_image_from_url`): a URL the model invented is on neither path. Outside
    ConversationService there is no allowlist, preserving direct tool tests and
    ad-hoc local calls. During a conversation, the URL must have come from the
    user (typed or attached) or from a source tool that explicitly registered it.
    """
    allowed = _ALLOWED_MEDIA_URLS.get()
    if allowed is None:
        return True
    return _clean_url(url) in allowed


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


def stage_bytes(data: bytes, content_type: str | None, filename: str) -> tuple[str, bool]:
    """Classify raw bytes by content-type and stage them in the run's outbox.

    The one place that maps a MIME type to an agno media kind (image/audio/video/
    file) and constructs the right object — shared by every tool that delivers
    fetched bytes (the URL media tool, the object-store recall tool). Returns the
    chosen `kind` and whether staging happened (False => no outbox open, the
    caller should be honest about non-delivery).
    """
    ctype = (content_type or "").split(";", 1)[0].strip().lower() or "application/octet-stream"
    subtype = ctype.split("/", 1)[1] if "/" in ctype else None
    if ctype.startswith("image/"):
        return "image", stage_media(images=(Image(content=data, mime_type=ctype, format=subtype),))
    if ctype.startswith("audio/"):
        return "audio", stage_media(audio=(Audio(content=data, mime_type=ctype, format=subtype),))
    if ctype.startswith("video/"):
        return "video", stage_media(videos=(Video(content=data, mime_type=ctype, format=subtype),))
    return "file", stage_media(files=(File(content=data, mime_type=ctype, filename=filename),))


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
