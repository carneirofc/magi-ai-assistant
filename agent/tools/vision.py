"""Vision tools: pull a remote image into the model's own context.

The model is multimodal but only ever *sees* media the host hands it via
`arun(images=...)` — i.e. Discord attachments. When a user instead pastes an
image *link* in their message text, nothing fetches it, so the model is left
guessing about pixels it never received (and tends to hallucinate a plausible
description).

`view_image_from_url` closes that gap: the model calls it with a URL, we
download the bytes here, and return them as a `ToolResult` image. agno then
appends a follow-up user message carrying that image, so the bytes land in the
model's context for its next step — it actually looks instead of guessing.
"""

import httpx
from agno.media import Image
from agno.tools import tool
from agno.tools.function import ToolResult
from agno.utils.log import log_info, log_warning

from core.media import view_only_id

# Don't pull a whole movie into context because a link happened to resolve to
# one. 20 MB comfortably covers any real image; bigger almost certainly isn't one.
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_FETCH_TIMEOUT_S = 20.0
# A browser-ish UA: some CDNs (Discord's included) 403 the default httpx agent.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AlyssaBot/1.0; +https://discord.com)"}


@tool
async def view_image_from_url(url: str) -> ToolResult:
    """Download an image from a URL so you can actually see and reason about it.

    Use this whenever the user shares a direct link to an image (a URL ending in
    .png/.jpg/.jpeg/.gif/.webp, or a CDN/attachment link that serves an image)
    and wants you to look at it. The image is loaded into your context, so after
    calling this you can describe its real contents instead of guessing.

    This fetches the bytes at the URL directly; it does not scrape web pages. If
    the user gives a link to a page (not the image itself), find the direct image
    URL first. Returns an error string if the URL isn't reachable or isn't an image.

    This is for YOU to look — the image is not sent to the user. To deliver an
    image (or any file) to the user as a real attachment, use
    `send_media_from_url` instead.
    """
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return ToolResult(content=f"Refusing to fetch non-http(s) URL: {url!r}")

    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_S, follow_redirects=True, headers=_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log_warning(f"view_image_from_url: HTTP {exc.response.status_code} for {url}")
        return ToolResult(content=f"Could not fetch image: HTTP {exc.response.status_code} for {url}")
    except httpx.HTTPError as exc:
        log_warning(f"view_image_from_url: fetch failed for {url}: {exc}")
        return ToolResult(content=f"Could not fetch image from {url}: {exc}")

    ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if not ctype.startswith("image/"):
        return ToolResult(
            content=(
                f"The URL did not return an image (content-type: {ctype or 'unknown'}). "
                "If this is a web page, find the direct image URL and try again."
            )
        )

    data = resp.content
    if len(data) > _MAX_IMAGE_BYTES:
        return ToolResult(
            content=f"Image is too large to load ({len(data)} bytes; limit {_MAX_IMAGE_BYTES})."
        )

    # "image/png" -> "png"; drop any "image/svg+xml" style suffix to the base name.
    subtype = ctype.split("/", 1)[1] or None
    log_info(f"view_image_from_url: fetched {len(data)} bytes ({ctype}) from {url}")
    return ToolResult(
        content=f"Loaded the image from {url} ({ctype}, {len(data)} bytes). It is now visible to you.",
        # view-only id: this image is model input, not a deliverable — reply
        # media collection (core/media.py) must not repost it to the user.
        images=[Image(id=view_only_id(), content=data, mime_type=ctype, format=subtype)],
    )


VISION_TOOLS = [view_image_from_url]
