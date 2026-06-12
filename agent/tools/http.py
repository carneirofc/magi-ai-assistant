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
    is set, except GETs to Seanime's configured image-proxy endpoint (SSRF guard
    — the model can be steered by untrusted page content).

For image links use `view_image_from_url` (agent/tools/vision) instead — it loads
pixels into context; these return text only.
"""

import asyncio
import ipaddress
import socket
from typing import Annotated, Any, Final, Literal
from urllib.parse import urlsplit

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning
from pydantic import BaseModel, Field

from agent.tools.outputs import ToolOutput, fail, ok
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


class HttpGetData(BaseModel):
    url: str = Field(description="Fetched or rejected URL.")
    status_code: int | None = Field(default=None, description="HTTP status code, when available.")
    content_type: str | None = Field(default=None, description="Response MIME type, when available.")
    bytes: int | None = Field(default=None, description="Response byte count, when available.")
    limit: int | None = Field(default=None, description="Maximum allowed byte count, for oversized responses.")
    body: str | None = Field(default=None, description="Response body text, when returned.")
    reason: str | None = Field(default=None, description="Reason the request was refused, when relevant.")


class HttpRequestData(BaseModel):
    method: str = Field(description="HTTP method used or rejected.")
    url: str | None = Field(default=None, description="Requested URL.")
    status_code: int | None = Field(default=None, description="HTTP status code, when available.")
    reason: str | None = Field(default=None, description="HTTP reason phrase or refusal reason.")
    headers: dict[str, str] = Field(default_factory=dict, description="Selected response headers echoed back.")
    bytes: int | None = Field(default=None, description="Response byte count, when available.")
    body_omitted: bool | None = Field(default=None, description="Whether the body was omitted due to size.")
    body: str | None = Field(default=None, description="Response body text, when returned.")
    text: str | None = Field(default=None, description="Compact model-readable HTTP report.")
    allowed: list[str] = Field(default_factory=list, description="Allowed methods, for unsupported method errors.")


def _scheme_ok(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


def _is_seanime_image_proxy_get(url: str, method: str) -> bool:
    if method.upper() != "GET":
        return False
    target = urlsplit(url)
    base = urlsplit(config.seanime_base_url.rstrip("/"))
    return (
        target.scheme == base.scheme
        and target.netloc == base.netloc
        and target.path == "/api/v1/image-proxy"
    )


async def _host_allowed(url: str, method: str = "GET") -> tuple[bool, str]:
    """SSRF guard: resolve the URL's host and reject private/loopback targets.

    Returns (allowed, reason). Skipped entirely when the deployment opts into
    private hosts via `config.http_allow_private_hosts` (e.g. to reach a local
    service on purpose), or for GET requests to the configured Seanime image
    proxy endpoint.
    """
    if config.http_allow_private_hosts or _is_seanime_image_proxy_get(url, method):
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


@tool(
    description="Fetch the text body of an HTTP(S) URL with a plain GET request.",
    instructions=(
        "Use for raw HTML, JSON, or text resources that need no auth, headers, "
        "method, or body. For custom methods/headers/body use http_request; for image pixels use view_image_from_url."
    ),
    show_result=True,
)
async def http_get(
    url: Annotated[
        str,
        Field(
            min_length=8,
            description="Full HTTP(S) URL to fetch with a plain GET request.",
        ),
    ],
) -> ToolOutput[HttpGetData]:
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
        return fail(f"Refusing to fetch non-http(s) URL: {url!r}", HttpGetData(url=url))

    allowed, reason = await _host_allowed(url, method="GET")
    if not allowed:
        log_warning(f"http_get: blocked {url} ({reason})")
        return fail(f"Refusing to fetch {url}: {reason}.", HttpGetData(url=url, reason=reason))

    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_S, follow_redirects=True, headers=_DEFAULT_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log_warning(f"http_get: HTTP {exc.response.status_code} for {url}")
        return fail(
            f"Could not fetch {url}: HTTP {exc.response.status_code}",
            HttpGetData(url=url, status_code=exc.response.status_code),
        )
    except httpx.HTTPError as exc:
        log_warning(f"http_get: fetch failed for {url}: {exc}")
        return fail(f"Could not fetch {url}: {exc}", HttpGetData(url=url))

    data = resp.content
    if len(data) > _MAX_BYTES:
        return fail(
            f"Response is too large to read ({len(data)} bytes; limit {_MAX_BYTES}).",
            HttpGetData(url=url, bytes=len(data), limit=_MAX_BYTES),
        )

    ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    log_info(f"http_get: fetched {len(data)} bytes ({ctype or 'unknown'}) from {url}")
    return ok(
        f"Fetched {url}.",
        HttpGetData(
            url=url,
            status_code=resp.status_code,
            content_type=ctype or None,
            bytes=len(data),
            body=resp.text,
        ),
    )


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


@tool(
    description="Perform one explicit HTTP request and return status, key headers, and body text.",
    instructions=(
        "Use only when the user supplied or clearly requested the URL and request details. "
        "Do not invent auth headers or payloads. Mutating methods require clear user intent."
    ),
    show_result=True,
)
async def http_request(
    url: Annotated[
        str,
        Field(min_length=8, description="Full HTTP(S) URL to call."),
    ],
    method: Annotated[
        Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        Field(default="GET", description="HTTP method to use for the request."),
    ] = "GET",
    headers: Annotated[
        dict[str, str] | None,
        Field(
            default=None,
            description="Optional flat request headers, e.g. Authorization or Content-Type.",
        ),
    ] = None,
    body: Annotated[
        str | None,
        Field(
            default=None,
            description="Optional raw request body. For JSON, pass serialized JSON text.",
        ),
    ] = None,
) -> ToolOutput[HttpRequestData]:
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
        return fail(f"Refusing to call non-http(s) URL: {url!r}", HttpRequestData(url=url, method=method))
    if method not in _ALLOWED_METHODS:
        return fail(
            f"Unsupported HTTP method {method!r}. Allowed: {', '.join(sorted(_ALLOWED_METHODS))}.",
            HttpRequestData(method=method, allowed=sorted(_ALLOWED_METHODS)),
        )

    allowed, reason = await _host_allowed(url, method=method)
    if not allowed:
        log_warning(f"http_request: blocked {method} {url} ({reason})")
        return fail(f"Refusing to call {url}: {reason}.", HttpRequestData(url=url, method=method, reason=reason))

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
        return fail(f"Request to {url} failed: {exc}", HttpRequestData(url=url, method=method))

    log_info(f"http_request: {method} {url} -> {resp.status_code} ({len(resp.content)} bytes)")
    body_omitted = len(resp.content) > _MAX_BYTES
    echoed_headers = {name: resp.headers.get(name) for name in _ECHO_HEADERS if resp.headers.get(name)}
    return ok(
        f"HTTP {method} {url} returned {resp.status_code}.",
        HttpRequestData(
            method=method,
            url=url,
            status_code=resp.status_code,
            reason=resp.reason_phrase,
            headers=echoed_headers,
            bytes=len(resp.content),
            body_omitted=body_omitted,
            body=None if body_omitted else (resp.text if resp.text.strip() else ""),
            text=_format_response(method, url, resp),
        ),
    )


# Read-only fetch + the controllable arbitrary-request escape hatch.
HTTP_TOOLS: Final[list[Any]] = [http_get, http_request]
