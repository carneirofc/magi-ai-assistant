"""Generic HTTP GET: fetch a URL's text so the model can read it.

The docstring is not a comment — the model reads it to decide WHEN to call the
tool and WHAT each argument means. Keep it precise.

This returns the *text* body of a URL (JSON, HTML, plain text, etc.). For images
use `view_image_from_url` instead — it loads pixels into context, this does not.
"""

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning

# Don't pull a huge payload into context. 2 MB covers any reasonable API/page
# response; bigger is almost certainly not something the model should read inline.
_MAX_BYTES = 2 * 1024 * 1024
_FETCH_TIMEOUT_S = 20.0
# A browser-ish UA: some hosts 403 the default httpx agent.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AlyssaBot/1.0; +https://discord.com)"}


@tool
async def http_get(url: str) -> str:
    """Fetch the text body of an HTTP(S) URL via a GET request.

    Use this to read an API response (JSON), a web page's raw HTML, or any
    text resource the user links or asks you to look up. Returns the response
    body as text.

    This does a plain GET only — no auth, headers, POST, or page rendering. For
    image links use `view_image_from_url` instead. Returns an error string if
    the URL isn't reachable, isn't http(s), or the body is too large.
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
        log_warning(f"http_get: HTTP {exc.response.status_code} for {url}")
        return f"Could not fetch {url}: HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        log_warning(f"http_get: fetch failed for {url}: {exc}")
        return f"Could not fetch {url}: {exc}"

    data = resp.content
    if len(data) > _MAX_BYTES:
        return f"Response is too large to read ({len(data)} bytes; limit {_MAX_BYTES})."

    ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    log_info(f"http_get: fetched {len(data)} bytes ({ctype or 'unknown'}) from {url}")
    return resp.text


HTTP_TOOLS = [http_get]
