"""HTTP API channel — the standalone integration point for external clients.

Serves the same brain as Discord over plain HTTP/JSON (FastAPI), so any client —
a desktop app, a web UI, another service — can talk to the agent with standard
tooling. v1 is deliberately small and session-scoped, mirroring the
`ConversationService` surface one-to-one:

    GET  /healthz                                   liveness probe (no auth)
    POST /v1/sessions/{session_id}/messages         run one turn, return the reply
    POST /v1/sessions/{session_id}/messages/stream  same turn, streamed over SSE
    POST /v1/sessions/{session_id}/flush            close the session (fold + wipe)
    GET  /v1/sessions/{session_id}/context          context size stats

Alongside the native contract, an OpenAI-compatible shim lets off-the-shelf chat
UIs (Open WebUI, LibreChat, ...) talk to the same brain with zero custom code:

    GET  /v1/models                                 advertise one model id
    POST /v1/chat/completions                       OpenAI chat completions (+ stream)

The shim bridges a stateless wire format onto a stateful brain: OpenAI clients
resend the whole transcript each call, but the agent keeps its own session
memory, so only the *last user message* is forwarded — the rest is carried by
memory. OpenAI carries no session id, so one is derived from a stable hash of the
chat's first user message (same chat → same server session); pass `X-Session-Id`
(and `X-User-Id`) to be exact. The `user` field, when sent, scopes memory.
Inbound `image_url` parts (uploads arrive as `data:` URIs) are forwarded for the
agent to see; replies have no media slot in that format, so reply media is folded
back into the message text as markdown. See `create_app` for the mapping.

The two message endpoints are interchangeable per request — same body, same brain,
same memory semantics; the client picks whole-reply JSON or SSE. The SSE stream
emits `delta` events (`{"text": chunk}`) while the model produces text, then one
terminal `done` event carrying the full `MessageReply` JSON — the authoritative
result (render it over the assembled deltas; errors arrive as `done` with
`is_error: true`).

`user_id` scopes memory and `session_id` scopes the conversation — the client
owns both ids (a desktop app might use its install id + one session per window).
Auth: when `API_AUTH_TOKEN` is set, /v1 requires `Authorization: Bearer <token>`.
Browser clients: set `api_cors_origins` so the service returns CORS headers for
those web origins (auth is a Bearer token, not a cookie, so "*" is safe).

If a team member talks to Seanime over MCP (`seanime_use_mcp`), the app opens
that MCP connection at startup and closes it at shutdown via the FastAPI
lifespan, so the session is warm before the first request.

Two factories, mirroring the other channel:

  - `create_app(conversation, auth_token)` — pure, fully injected (what tests use)
  - `build_api_app(db)` — composition root wiring the real stack from config

Media flows both ways. Replies carry media the agent delivered — each item is a
kind + mime type + either a base64 payload or a URL. Requests may carry inbound
images for the agent to see: `images[]` on the native message body, or OpenAI
`image_url` content parts on the shim. Inline bytes (base64 / `data:` URIs) are
decoded so a local backend can see them; plain http(s) URLs pass by reference.
"""

import base64
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Optional, Protocol, Union

from agno.db.base import BaseDb
from agno.media import Image
from agno.utils.log import log_info, log_warning
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, model_validator

from core.conversation import ConversationDelta, ConversationReply, ConversationService


# --- wire format (the public contract; version it, don't break it) -----------
class InboundImage(BaseModel):
    """An image the client sends for the agent to see. Exactly one of
    `data_base64` / `url` is set; a base64 `data:` URI may also ride in `url`."""

    mime_type: Optional[str] = None
    url: Optional[str] = None
    data_base64: Optional[str] = None


class MessageRequest(BaseModel):
    user_id: str = Field(min_length=1, description="Stable id of the end user (scopes memory).")
    text: str = Field(default="", description="The user's message (optional when images are sent).")
    images: list[InboundImage] = Field(
        default_factory=list, description="Images the agent should see this turn."
    )

    @model_validator(mode="after")
    def _require_content(self) -> "MessageRequest":
        """A turn must carry something — text or at least one image."""
        if not self.text.strip() and not self.images:
            raise ValueError("provide text or at least one image")
        return self


class MediaItem(BaseModel):
    """One piece of reply media. Exactly one of `data_base64` / `url` is set."""

    kind: str  # "image" | "video" | "audio" | "file"
    mime_type: Optional[str] = None
    filename: Optional[str] = None
    url: Optional[str] = None
    data_base64: Optional[str] = None


class MessageReply(BaseModel):
    text: str
    reasoning: Optional[str] = None
    is_error: bool = False
    media: list[MediaItem] = Field(default_factory=list)


def _media_items(reply: ConversationReply) -> list[MediaItem]:
    """Serialize the reply's agno media objects onto the wire: inline bytes as
    base64, by-reference media as its URL; items with neither are dropped."""
    items: list[MediaItem] = []
    for kind, media_list in (
        ("image", reply.images),
        ("video", reply.videos),
        ("audio", reply.audio),
        ("file", reply.files),
    ):
        for m in media_list:
            content = getattr(m, "content", None)
            url = getattr(m, "url", None)
            if not isinstance(content, bytes) and not url:
                log_warning(f"reply media ({kind}) has no content or url; dropped")
                continue
            items.append(
                MediaItem(
                    kind=kind,
                    mime_type=getattr(m, "mime_type", None),
                    filename=getattr(m, "filename", None),
                    url=None if isinstance(content, bytes) else url,
                    data_base64=base64.b64encode(content).decode("ascii")
                    if isinstance(content, bytes)
                    else None,
                )
            )
    return items


def _to_wire(reply: ConversationReply) -> MessageReply:
    return MessageReply(
        text=reply.text,
        reasoning=reply.reasoning,
        is_error=reply.is_error,
        media=_media_items(reply),
    )


# --- inbound media (client → agent) ------------------------------------------
def _subtype(mime: Optional[str]) -> Optional[str]:
    """The subtype of a mime type, agno's `format` ("image/png" -> "png")."""
    return mime.split("/", 1)[1] if mime and "/" in mime else None


def _decode_data_uri(uri: str) -> Optional[tuple[bytes, Optional[str]]]:
    """`(bytes, mime)` for a base64 `data:` URI, or None if it isn't one/decodes."""
    if not uri.startswith("data:"):
        return None
    header, _, b64 = uri.partition(",")
    if not b64:
        return None
    mime = header[len("data:") :].split(";", 1)[0].strip() or None
    try:
        return base64.b64decode(b64), mime
    except ValueError:  # binascii.Error subclasses ValueError
        return None


def _inbound_image(
    url: Optional[str], data_base64: Optional[str], mime_type: Optional[str]
) -> Optional[Image]:
    """Build an inbound agno Image from a client reference.

    Inline bytes (`data_base64`, or a `data:` URI in `url`) are decoded so any
    backend can see them — local llama-server can't fetch URLs. A plain http(s)
    URL is passed by reference for backends that do fetch.
    """
    if data_base64:
        try:
            content = base64.b64decode(data_base64)
        except ValueError:
            return None
        return Image(content=content, mime_type=mime_type, format=_subtype(mime_type))
    if url:
        decoded = _decode_data_uri(url)
        if decoded is not None:
            content, mime = decoded
            mime = mime_type or mime
            return Image(content=content, mime_type=mime, format=_subtype(mime))
        if url.lower().startswith(("http://", "https://")):
            return Image(url=url, mime_type=mime_type)
    return None


def _inbound_media(images: Sequence[InboundImage]) -> dict:
    """The agno `arun(**media)` kwargs for a turn's inbound images (drops any
    item that has neither usable bytes nor an http(s)/data URL)."""
    built = [
        img
        for img in (_inbound_image(i.url, i.data_base64, i.mime_type) for i in images)
        if img is not None
    ]
    return {"images": built} if built else {}


class FlushRequest(BaseModel):
    user_id: str = Field(min_length=1)


class FlushReply(BaseModel):
    dropped_turns: int


def _sse(event: str, data: dict) -> str:
    """One SSE frame: named event + single-line JSON payload."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# --- OpenAI-compatible shim (so stock chat UIs work unchanged) ----------------
# The one model id this service advertises; OpenAI clients require a model to
# select, but the brain is fixed so the request's `model` is otherwise ignored.
_OPENAI_MODEL_ID = "chatbot"


class _ImageUrl(BaseModel):
    """The `image_url` payload of an OpenAI content part (a URL or `data:` URI)."""

    url: str
    detail: Optional[str] = None


class _ContentPart(BaseModel):
    """One element of OpenAI's array-form message content (text or image)."""

    type: str
    text: Optional[str] = None
    image_url: Optional[_ImageUrl] = None


class ChatMessage(BaseModel):
    role: str
    content: Union[str, list[_ContentPart], None] = None


class ChatCompletionRequest(BaseModel):
    """The slice of OpenAI's chat-completions body this shim honors."""

    messages: list[ChatMessage] = Field(min_length=1)
    model: Optional[str] = None
    stream: bool = False
    user: Optional[str] = None


def _message_text(content: Union[str, list[_ContentPart], None]) -> str:
    """The plain text of a message, flattening OpenAI's array content form."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.text for p in content if p.type == "text" and p.text)
    return ""


def _message_images(content: Union[str, list[_ContentPart], None]) -> list[Image]:
    """The inbound images of a message (OpenAI `image_url` parts → agno Images)."""
    if not isinstance(content, list):
        return []
    out: list[Image] = []
    for p in content:
        if p.type == "image_url" and p.image_url:
            img = _inbound_image(p.image_url.url, None, None)
            if img is not None:
                out.append(img)
    return out


def _last_user_turn(messages: Sequence[ChatMessage]) -> tuple[str, list[Image]]:
    """The latest user turn's text + images — the only message forwarded; memory
    carries the rest."""
    for msg in reversed(messages):
        if msg.role == "user":
            text = _message_text(msg.content).strip()
            images = _message_images(msg.content)
            if text or images:
                return text, images
    return "", []


def _derive_session_id(messages: Sequence[ChatMessage], user_id: str) -> str:
    """A stable session id for a stateless client: hash the chat's first user
    message (constant across the chat's turns) so the same chat maps to the same
    server-side session. Distinct chats opening identically collide — pass
    `X-Session-Id` for an exact mapping."""
    for msg in messages:
        if msg.role == "user":
            seed = _message_text(msg.content)
            if seed:
                digest = hashlib.sha1(f"{user_id}:{seed}".encode()).hexdigest()
                return f"oai-{digest[:16]}"
    return "oai-default"


def _media_markdown(media: Sequence[MediaItem]) -> str:
    """Reply media folded into chat text: images inline, everything else linked.
    Empty string when there's nothing to append."""
    lines: list[str] = []
    for m in media:
        if m.url:
            src = m.url
        elif m.data_base64:
            src = f"data:{m.mime_type or 'application/octet-stream'};base64,{m.data_base64}"
        else:
            continue
        label = m.filename or m.kind
        lines.append(f"![{label}]({src})" if m.kind == "image" else f"[{label}]({src})")
    return ("\n\n" + "\n".join(lines)) if lines else ""


def _chat_chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: dict,
    finish_reason: Optional[str] = None,
) -> str:
    """One `chat.completion.chunk` SSE frame (OpenAI streams bare `data:` lines)."""
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class MCPConnection(Protocol):
    """The slice of an agno MCP toolkit this app drives over its lifespan: open
    the connection at startup, close it at shutdown."""

    async def connect(self) -> None: ...
    async def close(self) -> None: ...


def _mcp_lifespan(mcp_toolkits: Sequence[MCPConnection]):
    """A FastAPI lifespan that opens each member's MCP connection at startup and
    closes it at shutdown, on the serving event loop.

    Pre-connecting (vs. letting agno connect per delegation) keeps the session
    warm, surfaces the server's tool names in the lead's roster, and fails loud
    at startup if the MCP server is misconfigured. Best-effort: a connect error
    is logged, not fatal — agno still retries on the member's next run, and a
    deployment with no MCP toolkit (the default direct-HTTP Seanime) gets a
    plain no-op.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        for tk in mcp_toolkits:
            try:
                await tk.connect()
                log_info(f"api: connected MCP toolkit {type(tk).__name__}")
            except Exception as exc:  # noqa: BLE001 - degrade, don't crash startup
                log_warning(f"api: MCP toolkit connect failed ({exc}); will retry per run")
        try:
            yield
        finally:
            for tk in mcp_toolkits:
                try:
                    await tk.close()
                except Exception as exc:  # noqa: BLE001 - shutdown is best-effort
                    log_warning(f"api: MCP toolkit close failed ({exc})")

    return lifespan


def create_app(
    conversation: ConversationService,
    auth_token: Optional[str] = None,
    *,
    cors_origins: Optional[Sequence[str]] = None,
    mcp_toolkits: Optional[Sequence[MCPConnection]] = None,
) -> FastAPI:
    """The FastAPI app over an already-built `ConversationService` (pure factory).

    `cors_origins`: web origins allowed to call /v1 from a browser (empty/None =
    no CORS headers). `mcp_toolkits`: agno MCP toolkits to connect at startup and
    close at shutdown (empty/None = none) — see `_mcp_lifespan`.
    """
    app = FastAPI(title="chatbot", version="1", lifespan=_mcp_lifespan(mcp_toolkits or ()))

    if cors_origins:
        # Auth is a Bearer token, not a cookie, so credentials are not allowed —
        # which also makes the "*" origin wildcard valid (browsers reject "*"
        # together with allow_credentials).
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    bearer = HTTPBearer(auto_error=False)

    def require_auth(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    ) -> None:
        if auth_token is None:
            return
        if credentials is None or credentials.credentials != auth_token:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post(
        "/v1/sessions/{session_id}/messages",
        response_model=MessageReply,
        dependencies=[Depends(require_auth)],
    )
    async def post_message(session_id: str, body: MessageRequest) -> MessageReply:
        reply = await conversation.handle(
            user_id=body.user_id,
            session_id=session_id,
            text=body.text,
            media=_inbound_media(body.images),
        )
        # Errors travel in-band (`is_error`), not as HTTP errors: the run finished
        # and produced an honest reply for the client to show — that's a 200.
        return _to_wire(reply)

    @app.post(
        "/v1/sessions/{session_id}/messages/stream",
        dependencies=[Depends(require_auth)],
    )
    async def post_message_stream(session_id: str, body: MessageRequest) -> StreamingResponse:
        media = _inbound_media(body.images)

        async def events():
            async for item in conversation.handle_stream(
                user_id=body.user_id, session_id=session_id, text=body.text, media=media
            ):
                if isinstance(item, ConversationDelta):
                    yield _sse("delta", {"text": item.text})
                else:
                    yield _sse("done", _to_wire(item).model_dump())

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            # SSE responses must never be buffered or cached along the way.
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/v1/sessions/{session_id}/flush",
        response_model=FlushReply,
        dependencies=[Depends(require_auth)],
    )
    def post_flush(session_id: str, body: FlushRequest) -> FlushReply:
        return FlushReply(dropped_turns=conversation.flush(body.user_id, session_id))

    @app.get("/v1/sessions/{session_id}/context", dependencies=[Depends(require_auth)])
    def get_context(session_id: str, user_id: str = Query(min_length=1)) -> dict:
        return conversation.context_stats(user_id, session_id)

    # --- OpenAI-compatible shim -------------------------------------------
    @app.get("/v1/models", dependencies=[Depends(require_auth)])
    def list_models() -> dict:
        """The one model this service advertises — lets UIs auto-discover it."""
        return {
            "object": "list",
            "data": [
                {
                    "id": _OPENAI_MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "chatbot",
                }
            ],
        }

    @app.post(
        "/v1/chat/completions",
        response_model=None,
        dependencies=[Depends(require_auth)],
    )
    async def chat_completions(
        body: ChatCompletionRequest,
        x_session_id: Optional[str] = Header(default=None),
        x_user_id: Optional[str] = Header(default=None),
    ) -> Union[dict, StreamingResponse]:
        text, images = _last_user_turn(body.messages)
        if not text and not images:
            raise HTTPException(
                status_code=400, detail="no user message with text or image content"
            )
        user_id = x_user_id or body.user or "openai"
        session_id = x_session_id or _derive_session_id(body.messages, user_id)
        media = {"images": images} if images else {}
        model = body.model or _OPENAI_MODEL_ID
        created = int(time.time())
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"

        if not body.stream:
            wire = _to_wire(
                await conversation.handle(
                    user_id=user_id, session_id=session_id, text=text, media=media
                )
            )
            message: dict = {
                "role": "assistant",
                "content": wire.text + _media_markdown(wire.media),
            }
            if wire.reasoning:
                message["reasoning_content"] = wire.reasoning
            return {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        async def events() -> AsyncIterator[str]:
            # Lead frame announces the assistant role (OpenAI convention).
            yield _chat_chunk(completion_id, created, model, {"role": "assistant"})
            streamed_any = False
            final: Optional[ConversationReply] = None
            async for item in conversation.handle_stream(
                user_id=user_id, session_id=session_id, text=text, media=media
            ):
                if isinstance(item, ConversationDelta):
                    if item.text:
                        streamed_any = True
                        yield _chat_chunk(completion_id, created, model, {"content": item.text})
                else:
                    final = item
            # Trailing content: the full text only if nothing streamed (error /
            # non-delta path), plus any media that has no slot in the wire format.
            if final is not None:
                wire = _to_wire(final)
                tail = (wire.text if not streamed_any else "") + _media_markdown(wire.media)
                if tail:
                    yield _chat_chunk(completion_id, created, model, {"content": tail})
            yield _chat_chunk(completion_id, created, model, {}, finish_reason="stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return app


def _collect_mcp_toolkits(runner: object) -> list[MCPConnection]:
    """The MCP toolkits hanging off a team's members, so the app can manage their
    connection lifecycle (see `_mcp_lifespan`). Duck-typed and defensive: a plain
    Agent runner, or members with no MCP tools, yields an empty list."""
    from agent.tools.seanime_mcp import is_mcp_toolkit

    toolkits: list[MCPConnection] = []
    for member in getattr(runner, "members", []) or []:
        for tool in getattr(member, "tools", []) or []:
            if is_mcp_toolkit(tool):
                toolkits.append(tool)
    return toolkits


def build_api_app(db: Optional[BaseDb] = None) -> FastAPI:
    """Composition root: the real stack from config, served over HTTP."""
    from channels.bootstrap import build_conversation_service
    from agent.members import MEMBER_BUILDERS, build_discord_agent
    from core.config import config
    from core.prompts import load_prompt

    log_info(f"building api app (db={'injected' if db else 'default'})")
    conversation = build_conversation_service(
        channel_guidance=load_prompt("channels/api.md"),
        db=db,
        # The Discord specialist needs a live Discord conversation context; over
        # the API there is none, so it's left off the roster.
        member_builders=[b for b in MEMBER_BUILDERS if b is not build_discord_agent],
    )
    if config.api_auth_token is None:
        log_info("api: auth DISABLED (API_AUTH_TOKEN not set) — keep the bind local")
    if config.api_cors_origins:
        log_info(f"api: CORS enabled for origins {config.api_cors_origins}")
    return create_app(
        conversation,
        auth_token=config.api_auth_token,
        cors_origins=config.api_cors_origins,
        # Seanime-over-MCP is the only MCP member today; connect it at startup.
        mcp_toolkits=_collect_mcp_toolkits(conversation.runner),
    )
