"""HTTP tools: let the model read a URL or perform an explicit, user-specified request.

Each docstring is the model's contract — it reads them to decide WHEN to call a
tool and WHAT each argument means. Keep them precise.

Two tools, narrow to broad:

  - `http_get(url)` — read the text body of a URL. The default, no-surprise reader
    for fetching a page/API response when no auth, method, or body is needed.
  - `http_request(...)` — one arbitrary request the user described: method, URL,
    headers, and body all come from the conversation. This is the controllable
    escape hatch for talking to real APIs (POST/PUT/PATCH/DELETE, auth headers,
    JSON payloads). Mutating methods change external state — the model must have
    the user's explicit intent before calling it (enforced in the prompt).

Safety is owned here, in our code, not delegated to the framework:
  - scheme is restricted to http(s);
  - method is checked against an allowlist;
  - response size is capped so a huge body can't blow up the context;
  - private / loopback hosts are blocked unless `config.http_allow_private_hosts`
    is set (SSRF guard — the model can be steered by untrusted page content).

For image links use `view_image_from_url` (agent/tools/vision) instead — it loads
pixels into context; these return text only.
"""

import asyncio
import ipaddress
import socket
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning

from core.config import config

# Don't pull a huge payload into context. 2 MB covers any reasonable API/page
# response; bigger is almost certainly not something the model should read inline.
_MAX_BYTES: Final[int] = 2 * 1024 * 1024
_FETCH_TIMEOUT_S: Final[float] = 20.0
# A browser-ish UA: some hosts 403 the default httpx agent.
_DEFAULT_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (compatible; AlyssaBot/1.0; +https://discord.com)"
}
# Methods the model may invoke. HEAD/OPTIONS are read-only probes; the rest can
# mutate state, which the prompt gates behind explicit user intent.
_ALLOWED_METHODS: Final[frozenset[str]] = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
)
# Response headers worth echoing back to the model; the full set is noise.
_ECHO_HEADERS: Final[tuple[str, ...]] = ("content-type", "content-length", "location")


def _scheme_ok(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


async def _host_allowed(url: str) -> tuple[bool, str]:
    """SSRF guard: resolve the URL's host and reject private/loopback targets.

    Returns (allowed, reason). Skipped entirely when the deployment opts into
    private hosts via `config.http_allow_private_hosts` (e.g. to reach a local
    service on purpose).
    """
    if config.http_allow_private_hosts:
        return True, ""
    host = urlsplit(url).hostname
    if not host:
        return False, "missing host"
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except OSError as exc:
        return False, f"could not resolve host {host!r}: {exc}"
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"host {host!r} resolves to a non-public address ({ip})"
    return True, ""


@tool
async def http_get(url: str) -> str:
    """Fetch the text body of an HTTP(S) URL via a GET request.

    Use this to read an API response (JSON), a web page's raw HTML, or any
    text resource the user links or asks you to look up. Returns the response
    body as text.

    This does a plain GET only — no auth, headers, or body. For a request that
    needs a method, headers, or a payload, use `http_request`. For image links
    use `view_image_from_url`. Returns an error string if the URL isn't
    reachable, isn't http(s), or the body is too large.
    """
    url = (url or "").strip()
    if not _scheme_ok(url):
        return f"Refusing to fetch non-http(s) URL: {url!r}"

    allowed, reason = await _host_allowed(url)
    if not allowed:
        log_warning(f"http_get: blocked {url} ({reason})")
        return f"Refusing to fetch {url}: {reason}."

    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_S, follow_redirects=True, headers=_DEFAULT_HEADERS
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


def _normalize_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """Merge the model-supplied headers over the default UA (model wins)."""
    merged: dict[str, str] = dict(_DEFAULT_HEADERS)
    if headers:
        merged.update({str(k): str(v) for k, v in headers.items()})
    return merged


def _format_response(method: str, url: str, resp: httpx.Response) -> str:
    """Render a response as a compact, model-readable report (status + body)."""
    lines: list[str] = [f"HTTP {method} {url} -> {resp.status_code} {resp.reason_phrase}".rstrip()]
    for name in _ECHO_HEADERS:
        value = resp.headers.get(name)
        if value:
            lines.append(f"{name}: {value}")

    data = resp.content
    if len(data) > _MAX_BYTES:
        lines.append(f"\n(body omitted: {len(data)} bytes exceeds the {_MAX_BYTES}-byte limit)")
        return "\n".join(lines)

    body = resp.text
    lines.append("")
    lines.append(body if body.strip() else "(empty body)")
    return "\n".join(lines)


@tool
async def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> str:
    """Perform one HTTP request the user described, returning its status and body.

    Use this when a plain `http_get` is not enough: a different method (POST,
    PUT, PATCH, DELETE), custom request headers (auth tokens, content-type), or a
    request body/payload. Collect every part from the user — never invent a URL,
    header, or payload.

    Arguments (all but `url` optional):
      - url: the full http(s) URL to call.
      - method: HTTP method, e.g. "GET", "POST", "PUT", "PATCH", "DELETE".
      - headers: request headers as a flat object, e.g.
        {"Authorization": "Bearer ...", "Content-Type": "application/json"}.
      - body: the raw request body/payload as a string. For JSON, pass the
        serialized JSON text and set a "Content-Type: application/json" header.

    Methods other than GET/HEAD/OPTIONS can change external state — only call
    those after the user has clearly asked for that action. Returns the status
    line, key response headers, and the response body as text, or an error
    string if the request can't be made (bad scheme, blocked host, network
    failure, oversized body).
    """
    url = (url or "").strip()
    method = (method or "GET").strip().upper()

    if not _scheme_ok(url):
        return f"Refusing to call non-http(s) URL: {url!r}"
    if method not in _ALLOWED_METHODS:
        return f"Unsupported HTTP method {method!r}. Allowed: {', '.join(sorted(_ALLOWED_METHODS))}."

    allowed, reason = await _host_allowed(url)
    if not allowed:
        log_warning(f"http_request: blocked {method} {url} ({reason})")
        return f"Refusing to call {url}: {reason}."

    req_headers = _normalize_headers(headers)
    content: bytes | None = body.encode("utf-8") if body is not None else None
    log_info(
        f"http_request: {method} {url} "
        f"(headers={sorted(req_headers)}, body={len(content) if content else 0} bytes)"
    )

    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_S, follow_redirects=True
        ) as client:
            resp = await client.request(method, url, headers=req_headers, content=content)
    except httpx.HTTPError as exc:
        log_warning(f"http_request: {method} {url} failed: {exc}")
        return f"Request to {url} failed: {exc}"

    log_info(f"http_request: {method} {url} -> {resp.status_code} ({len(resp.content)} bytes)")
    return _format_response(method, url, resp)


# Read-only fetch + the controllable arbitrary-request escape hatch.
HTTP_TOOLS: Final[list[Any]] = [http_get, http_request]
