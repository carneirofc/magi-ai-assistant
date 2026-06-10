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

The two message endpoints are interchangeable per request — same body, same brain,
same memory semantics; the client picks whole-reply JSON or SSE. The SSE stream
emits `delta` events (`{"text": chunk}`) while the model produces text, then one
terminal `done` event carrying the full `MessageReply` JSON — the authoritative
result (render it over the assembled deltas; errors arrive as `done` with
`is_error: true`).

`user_id` scopes memory and `session_id` scopes the conversation — the client
owns both ids (a desktop app might use its install id + one session per window).
Auth: when `API_AUTH_TOKEN` is set, /v1 requires `Authorization: Bearer <token>`.

Two factories, mirroring the other channel:

  - `create_app(conversation, auth_token)` — pure, fully injected (what tests use)
  - `build_api_app(db)` — composition root wiring the real stack from config

Text-only for now; media is the natural v2 extension.
"""

import json
from typing import Optional

from agno.db.base import BaseDb
from agno.utils.log import log_info
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from core.conversation import ConversationDelta, ConversationService


# --- wire format (the public contract; version it, don't break it) -----------
class MessageRequest(BaseModel):
    user_id: str = Field(min_length=1, description="Stable id of the end user (scopes memory).")
    text: str = Field(min_length=1, description="The user's message.")


class MessageReply(BaseModel):
    text: str
    reasoning: Optional[str] = None
    is_error: bool = False


class FlushRequest(BaseModel):
    user_id: str = Field(min_length=1)


class FlushReply(BaseModel):
    dropped_turns: int


def _sse(event: str, data: dict) -> str:
    """One SSE frame: named event + single-line JSON payload."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(conversation: ConversationService, auth_token: Optional[str] = None) -> FastAPI:
    """The FastAPI app over an already-built `ConversationService` (pure factory)."""
    app = FastAPI(title="chatbot", version="1")
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
            user_id=body.user_id, session_id=session_id, text=body.text
        )
        # Errors travel in-band (`is_error`), not as HTTP errors: the run finished
        # and produced an honest reply for the client to show — that's a 200.
        return MessageReply(text=reply.text, reasoning=reply.reasoning, is_error=reply.is_error)

    @app.post(
        "/v1/sessions/{session_id}/messages/stream",
        dependencies=[Depends(require_auth)],
    )
    async def post_message_stream(session_id: str, body: MessageRequest) -> StreamingResponse:
        async def events():
            async for item in conversation.handle_stream(
                user_id=body.user_id, session_id=session_id, text=body.text
            ):
                if isinstance(item, ConversationDelta):
                    yield _sse("delta", {"text": item.text})
                else:
                    yield _sse(
                        "done",
                        MessageReply(
                            text=item.text, reasoning=item.reasoning, is_error=item.is_error
                        ).model_dump(),
                    )

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

    return app


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
    return create_app(conversation, auth_token=config.api_auth_token)
