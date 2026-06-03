"""OpenAI-compatible HTTP shim so OpenWebUI (or any OpenAI client) can talk to
the Agno agent. Agno has no native /v1/chat/completions server, so we map
OpenAI requests -> AgentService -> OpenAI-shaped responses.

OpenWebUI: Admin Settings -> Connections -> OpenAI -> URL http://host:port/v1, key = API_TOKEN.
"""

import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from core.agent_service import AgentService
from core.config import config

app = FastAPI(title="chatbot OpenAI-compatible API")
_service = AgentService()
_auth = HTTPBearer(auto_error=True)


def _check_auth(creds: HTTPAuthorizationCredentials = Depends(_auth)) -> None:
    if creds.credentials != config.api_token:
        raise HTTPException(status_code=401, detail="Invalid API token")


class ChatMessage(BaseModel):
    role: str
    content: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False


def _session_id(messages: list[dict]) -> str:
    """Stable per-conversation id (first user message) for agno run grouping."""
    seed = next((m["content"] for m in messages if m.get("role") == "user"), "")
    return "owui-" + hashlib.sha256((seed or "").encode()).hexdigest()[:16]


@app.get("/v1/models")
async def list_models(_: None = Depends(_check_auth)) -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": config.model_name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "chatbot",
            }
        ],
    }


def _stream_chunks(
    completion_id: str, model: str, deltas: AsyncIterator[str]
) -> AsyncIterator[bytes]:
    async def gen() -> AsyncIterator[bytes]:
        created = int(time.time())

        def chunk(delta: dict, finish: str | None) -> bytes:
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(payload)}\n\n".encode()

        yield chunk({"role": "assistant"}, None)
        async for text in deltas:
            yield chunk({"content": text}, None)
        yield chunk({}, "stop")
        yield b"data: [DONE]\n\n"

    return gen()


@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest, _: None = Depends(_check_auth)
):
    messages = [m.model_dump() for m in req.messages]
    session_id = _session_id(messages)
    completion_id = "chatcmpl-" + uuid.uuid4().hex
    model = req.model or config.model_name

    if req.stream:
        return StreamingResponse(
            _stream_chunks(completion_id, model, _service.astream(messages, session_id)),
            media_type="text/event-stream",
        )

    content = await _service.arun(messages, session_id)
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
