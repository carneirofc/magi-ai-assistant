"""Remote backend: talk to a running magi HTTP service over the v1 contract.

`HttpClient` is a thin, typed client for `magi.channels.api` — it owns the bits
every caller would otherwise hand-roll: the bearer token, the session-scoped
paths, SSE parsing for the streaming turn, and (de)serializing media. It exposes
the same `MagiClient` surface as the in-process `EmbeddedClient`, so a desktop
app that outgrows in-process (a shared brain, a heavier backend on another host)
switches backend without touching call sites.

`httpx` is imported lazily so importing `magi.client` never requires it — only
constructing an `HttpClient` does. Inject a pre-built `httpx.AsyncClient` (the
`client=` arg) to customize transport/TLS or to test against an ASGI app in
process.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Optional, Union

from magi.client.types import Delta, InboundImage, Reply, inbound_to_wire, reply_from_wire

if TYPE_CHECKING:  # keep httpx off the import path unless actually used
    import httpx


async def _iter_sse(response: "httpx.Response") -> AsyncIterator[tuple[str, dict]]:
    """Yield `(event, data)` pairs from an SSE response.

    Frames are blank-line separated; we accumulate `event:` / `data:` lines and
    emit once the frame closes. Matches the `_sse` framing in `channels.api`.
    """
    event = "message"
    data_lines: list[str] = []
    async for raw in response.aiter_lines():
        line = raw.rstrip("\r")
        if line == "":  # frame boundary
            if data_lines:
                yield event, json.loads("\n".join(data_lines))
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):  # comment/keepalive
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
    if data_lines:  # a final frame with no trailing blank line
        yield event, json.loads("\n".join(data_lines))


class HttpClient:
    """A `MagiClient` over a running magi HTTP service."""

    def __init__(
        self,
        base_url: str,
        user_id: str,
        session_id: str = "default",
        *,
        auth_token: Optional[str] = None,
        timeout: float = 120.0,
        client: "Optional[httpx.AsyncClient]" = None,
    ) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self._base_url = base_url.rstrip("/")
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            import httpx  # lazy: only constructing an HttpClient needs it

            headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            self._client = httpx.AsyncClient(
                base_url=self._base_url, headers=headers, timeout=timeout
            )
            self._owns_client = True

    async def aopen(self) -> "HttpClient":
        return self  # httpx.AsyncClient connects lazily on first request

    async def aclose(self) -> None:
        # Only close a client we created; an injected one is the caller's to manage.
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "HttpClient":
        return await self.aopen()

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _body(self, text: str, images: Sequence[InboundImage]) -> dict:
        body: dict[str, object] = {"user_id": self.user_id, "text": text}
        wire = inbound_to_wire(list(images))
        if wire:
            body["images"] = wire
        return body

    async def send(self, text: str, *, images: Sequence[InboundImage] = ()) -> Reply:
        resp = await self._client.post(
            f"/v1/sessions/{self.session_id}/messages", json=self._body(text, images)
        )
        resp.raise_for_status()
        return reply_from_wire(resp.json())

    async def stream(
        self, text: str, *, images: Sequence[InboundImage] = ()
    ) -> AsyncIterator[Union[Delta, Reply]]:
        async with self._client.stream(
            "POST",
            f"/v1/sessions/{self.session_id}/messages/stream",
            json=self._body(text, images),
        ) as resp:
            resp.raise_for_status()
            async for event, data in _iter_sse(resp):
                if event == "delta":
                    yield Delta(text=data.get("text", ""))
                elif event == "done":
                    yield reply_from_wire(data)

    async def flush(self) -> int:
        resp = await self._client.post(
            f"/v1/sessions/{self.session_id}/flush", json={"user_id": self.user_id}
        )
        resp.raise_for_status()
        return int(resp.json()["dropped_turns"])

    async def context_stats(self) -> dict[str, object]:
        resp = await self._client.get(
            f"/v1/sessions/{self.session_id}/context", params={"user_id": self.user_id}
        )
        resp.raise_for_status()
        return resp.json()
