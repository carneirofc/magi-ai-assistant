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
from typing import Final, Optional
from urllib.parse import urlparse

import httpx
from agno.media import Audio, File, Image, Video
from agno.tools import tool
from agno.utils.log import log_info, log_warning

from core.media import stage_media

# Generous fetch cap — the channel applies its own upload limits (and falls
# back to the link when a file exceeds them).
_MAX_FETCH_BYTES: Final[int] = 50 * 1024 * 1024
_FETCH_TIMEOUT_S: Final[float] = 30.0
# A browser-ish UA: some CDNs (Discord's included) 403 the default httpx agent.
_HEADERS: Final[dict] = {
    "User-Agent": "Mozilla/5.0 (compatible; AlyssaBot/1.0; +https://discord.com)"
}


def _filename(url: str, ctype: str, explicit: Optional[str]) -> str:
    """A sensible filename for the attachment: explicit > URL basename > mime."""
    if explicit:
        return explicit
    name = Path(urlparse(url).path).name
    if name and "." in name:
        return name
    ext = mimetypes.guess_extension(ctype) or ""
    return f"attachment{ext}"


@tool
async def send_media_from_url(url: str, filename: Optional[str] = None) -> str:
    """Fetch a file from a direct URL and deliver it to the user as a real
    attachment (image, audio, video, or document) instead of a link.

    Use this whenever the user wants the actual media — an icon, a cover, a
    picture, a sound — and you only have its URL (from a team member, a search
    result, an API response). Posting the URL as text is NOT delivering the
    media; call this tool and the host attaches the real file to your reply.

    The file is sent to the user but NOT loaded into your own context — to look
    at an image yourself, use `view_image_from_url`. `filename` optionally
    overrides the attachment's display name. Returns a confirmation or a
    readable error; on error, fall back to sharing the URL as text.
    """
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return f"Refusing to fetch non-http(s) URL: {url!r}"

    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_S, follow_redirects=True, headers=_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log_warning(f"send_media_from_url: HTTP {exc.response.status_code} for {url}")
        return f"Could not fetch media: HTTP {exc.response.status_code} for {url}"
    except httpx.HTTPError as exc:
        log_warning(f"send_media_from_url: fetch failed for {url}: {exc}")
        return f"Could not fetch media from {url}: {exc}"

    data = resp.content
    if not data:
        return f"The URL returned an empty body: {url}"
    if len(data) > _MAX_FETCH_BYTES:
        return (
            f"File is too large to attach ({len(data)} bytes; limit {_MAX_FETCH_BYTES}). "
            "Share the URL as text instead."
        )

    ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    ctype = ctype or "application/octet-stream"
    subtype = ctype.split("/", 1)[1] if "/" in ctype else None
    name = _filename(url, ctype, filename)

    if ctype.startswith("image/"):
        kind = "image"
        staged = stage_media(images=(Image(content=data, mime_type=ctype, format=subtype),))
    elif ctype.startswith("audio/"):
        kind = "audio"
        staged = stage_media(audio=(Audio(content=data, mime_type=ctype, format=subtype),))
    elif ctype.startswith("video/"):
        kind = "video"
        staged = stage_media(videos=(Video(content=data, mime_type=ctype, format=subtype),))
    else:
        kind = "file"
        staged = stage_media(files=(File(content=data, mime_type=ctype, filename=name),))

    if not staged:
        # No outbox open (bare run outside ConversationService) — be honest.
        log_warning("send_media_from_url: no media outbox open; nothing delivered")
        return (
            "Media delivery is not available in this run. "
            f"Share the URL as text instead: {url}"
        )

    log_info(f"send_media_from_url: staged {kind} '{name}' ({len(data)} bytes, {ctype}) from {url}")
    return (
        f"Attached the {kind} '{name}' ({ctype}, {len(data)} bytes) to your reply — "
        "it will be delivered to the user with your message. Don't paste the URL as well."
    )


MEDIA_TOOLS: Final[list] = [send_media_from_url]
