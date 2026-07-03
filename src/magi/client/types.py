"""The GUI-facing data types — plain, dependency-light dataclasses.

A desktop app codes against these, never against agno media objects or the
FastAPI wire models. They carry only what a UI needs to render a turn:
`stdlib` only (no agno, no fastapi, no pydantic), so `import magi.client.types`
is cheap and pulls in nothing heavy.

Both backends normalize onto these: `EmbeddedClient` maps agno's
`ConversationReply` onto them (see `magi.client.embedded`); `HttpClient` maps the
JSON wire body onto them via `reply_from_wire` here. Inbound images go the other
way — `inbound_to_wire` serializes them for the HTTP body; the embedded backend
maps them to agno `Image`s itself.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Media:
    """One piece of media on a reply.

    Exactly one of `data` (inline bytes, already decoded) / `url` (by reference)
    is set — the same split the wire and outbox use. `data_uri` renders inline
    bytes as a `data:` URI, handy for dropping straight into an `<img src>`.
    """

    kind: str  # "image" | "video" | "audio" | "file"
    mime_type: Optional[str] = None
    filename: Optional[str] = None
    url: Optional[str] = None
    data: Optional[bytes] = None

    @property
    def data_uri(self) -> Optional[str]:
        if self.data is None:
            return None
        b64 = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type or 'application/octet-stream'};base64,{b64}"


@dataclass(frozen=True)
class Reply:
    """The result of one turn, in terms a UI can render directly."""

    text: str
    reasoning: Optional[str] = None
    is_error: bool = False
    media: tuple[Media, ...] = ()

    @property
    def has_media(self) -> bool:
        return bool(self.media)


@dataclass(frozen=True)
class Delta:
    """One streamed chunk of reply text (see `MagiClient.stream`)."""

    text: str


@dataclass(frozen=True)
class InboundImage:
    """An image the app sends for the agent to see.

    Give inline bytes (`data`) or an http(s) `url`; set `mime_type` when known
    (`data` without it still works, but backends see the type when you pass it).
    """

    data: Optional[bytes] = None
    url: Optional[str] = None
    mime_type: Optional[str] = None


# --- HTTP wire <-> types (pure stdlib; the embedded backend maps agno itself) --
def reply_from_wire(payload: dict) -> Reply:
    """Map an HTTP `MessageReply` JSON body onto a `Reply` (base64 → bytes)."""
    media: list[Media] = []
    for m in payload.get("media") or []:
        b64 = m.get("data_base64")
        media.append(
            Media(
                kind=m["kind"],
                mime_type=m.get("mime_type"),
                filename=m.get("filename"),
                url=m.get("url"),
                data=base64.b64decode(b64) if b64 else None,
            )
        )
    return Reply(
        text=payload.get("text", ""),
        reasoning=payload.get("reasoning"),
        is_error=bool(payload.get("is_error", False)),
        media=tuple(media),
    )


def inbound_to_wire(images: "list[InboundImage] | tuple[InboundImage, ...]") -> list[dict]:
    """Serialize inbound images for the HTTP message body (`images: [...]`).

    Bytes ride as base64; an http(s) URL rides by reference. Items with neither
    are dropped rather than sent empty.
    """
    out: list[dict] = []
    for img in images:
        item: dict[str, str] = {}
        if img.data is not None:
            item["data_base64"] = base64.b64encode(img.data).decode("ascii")
        if img.url:
            item["url"] = img.url
        if img.mime_type:
            item["mime_type"] = img.mime_type
        # Must carry a payload (bytes or url), not just a mime type.
        if item.get("data_base64") or item.get("url"):
            out.append(item)
    return out
