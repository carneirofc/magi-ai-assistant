"""Media delivery tool: turn a URL into an actual attachment in the reply.

The model often ends a task holding only a *link* to the thing the user asked
for — an icon URL from a member agent, a cover from Seanime, a sound file. A
link is not the deliverable; the user expects the image/audio itself in the
chat. This tool fetches the bytes here and stages them in the per-run media
outbox (core/media.py); the channel then posts them natively (Discord uploads
an attachment, the API serializes them).

Staging — not returning the media in the ToolResult — is deliberate: outbox
media never enters the model's context, so a vision-only backend never sees an
`input_audio` part it can't handle, and a 5 MB image costs zero tokens. To
*look* at an image instead, `view_image_from_url` is the right tool.
"""

import mimetypes
from pathlib import Path
from typing import Annotated, Any, Final, Optional
from urllib.parse import urlparse

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, ok, fail
from magi.core.media import is_media_url_allowed, stage_bytes

# Generous fetch cap — the channel applies its own upload limits (and falls
# back to the link when a file exceeds them).
_MAX_FETCH_BYTES: Final[int] = 50 * 1024 * 1024
_FETCH_TIMEOUT_S: Final[float] = 30.0
# A browser-ish UA: some CDNs (Discord's included) 403 the default httpx agent.
_HEADERS: Final[dict] = {
    "User-Agent": "Mozilla/5.0 (compatible; AlyssaBot/1.0; +https://discord.com)"
}


class MediaFetchData(BaseModel):
    url: str = Field(description="Source URL that was fetched or rejected.")
    filename: str | None = Field(default=None, description="Attachment filename, when known.")
    kind: str | None = Field(default=None, description="Delivered media kind: image, audio, video, or file.")
    content_type: str | None = Field(default=None, description="Detected MIME type, when known.")
    bytes: int | None = Field(default=None, description="Fetched byte count, when known.")
    limit: int | None = Field(default=None, description="Maximum allowed byte count, for oversized files.")
    status_code: int | None = Field(default=None, description="HTTP status code, when relevant.")
    delivered: bool | None = Field(default=None, description="Whether the file was staged for delivery.")


def _filename(url: str, ctype: str, explicit: Optional[str]) -> str:
    """A sensible filename for the attachment: explicit > URL basename > mime."""
    if explicit:
        return explicit
    name = Path(urlparse(url).path).name
    if name and "." in name:
        return name
    ext = mimetypes.guess_extension(ctype) or ""
    return f"attachment{ext}"


@tool(
    description="Fetch a direct URL and attach the file to the user's reply as real media.",
    instructions=(
        "This delivers a file the user asked for; it does NOT fetch an image for you to see. An image already "
        "attached to this turn is in your context — never call this (or invent a URL) to 're-send' or re-fetch it. "
        "Use when the deliverable is the actual file, image, audio, or video rather than a link. "
        "Only use URLs supplied by the user or returned by a successful source-specific tool in the current turn; "
        "never invent, guess, repair, or reuse a stale media URL. For source-specific media such as Seanime covers, "
        "call that source tool first and pass through its exact URL. This does not load the media into model "
        "context; use view_image_from_url when you need to inspect pixels."
    ),
    show_result=True,
)
async def send_media_from_url(
    url: Annotated[
        str,
        Field(min_length=8, description="Direct HTTP(S) URL of the file to attach."),
    ],
    filename: Annotated[
        Optional[str],
        Field(default=None, description="Optional display filename for the delivered attachment."),
    ] = None,
) -> ToolOutput[MediaFetchData]:
    """Fetch a file from a direct URL and deliver it to the user as a real
    attachment (image, audio, video, or document) instead of a link.

    Use this whenever the user wants the actual media — an icon, a cover, a
    picture, a sound — and you have its URL from the user or from a successful
    source-specific tool result in the current turn. Posting the URL as text is
    NOT delivering the media; call this tool and the host attaches the real file
    to your reply.

    Do not use this to search for media and do not pass guessed, reconstructed,
    or stale URLs. For source-specific media (for example, Seanime thumbnails),
    call the source tool first and pass through the exact URL it returned.

    The file is sent to the user but NOT loaded into your own context — to look
    at an image yourself, use `view_image_from_url`. `filename` optionally
    overrides the attachment's display name. Returns a confirmation or a
    readable error; on error, fall back to sharing the URL as text.
    """
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return fail(f"Refusing to fetch non-http(s) URL: {url!r}", MediaFetchData(url=url))
    if not is_media_url_allowed(url):
        return fail(
            "Refusing to attach an unsourced media URL. Use a URL from the user "
            "or from a successful source-specific tool result in this turn.",
            MediaFetchData(url=url, delivered=False),
        )

    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_S, follow_redirects=True, headers=_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log_warning(f"send_media_from_url: HTTP {exc.response.status_code} for {url}")
        return fail(
            f"Could not fetch media: HTTP {exc.response.status_code} for {url}",
            MediaFetchData(url=url, status_code=exc.response.status_code),
        )
    except httpx.HTTPError as exc:
        log_warning(f"send_media_from_url: fetch failed for {url}: {exc}")
        return fail(f"Could not fetch media from {url}: {exc}", MediaFetchData(url=url))

    data = resp.content
    if not data:
        return fail(f"The URL returned an empty body: {url}", MediaFetchData(url=url))
    if len(data) > _MAX_FETCH_BYTES:
        return fail(
            f"File is too large to attach ({len(data)} bytes; limit {_MAX_FETCH_BYTES}). "
            "Share the URL as text instead.",
            MediaFetchData(url=url, bytes=len(data), limit=_MAX_FETCH_BYTES),
        )

    ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    ctype = ctype or "application/octet-stream"
    name = _filename(url, ctype, filename)
    kind, staged = stage_bytes(data, ctype, name)

    if not staged:
        # No outbox open (bare run outside ConversationService) — be honest.
        log_warning("send_media_from_url: no media outbox open; nothing delivered")
        return fail(
            "Media delivery is not available in this run. "
            f"Share the URL as text instead: {url}",
            MediaFetchData(url=url, filename=name, content_type=ctype, bytes=len(data)),
        )

    log_info(f"send_media_from_url: staged {kind} '{name}' ({len(data)} bytes, {ctype}) from {url}")
    return ok(
        f"Attached the {kind} '{name}' ({ctype}, {len(data)} bytes) to your reply — "
        "it will be delivered to the user with your message. Don't paste the URL as well.",
        MediaFetchData(
            url=url,
            filename=name,
            kind=kind,
            content_type=ctype,
            bytes=len(data),
            delivered=True,
        ),
    )


MEDIA_TOOLS: Final[list] = [send_media_from_url]
