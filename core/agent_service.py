"""Shared agent service. Every channel (OpenWebUI now; Discord/Telegram later)
normalizes its inbound payload into messages and calls this one brain."""

from collections.abc import AsyncIterator

from agno.models.message import Message
from agno.run.agent import RunContentEvent

from agent import build_stateless_agent


class AgentService:
    def __init__(self) -> None:
        self._agent = build_stateless_agent()

    @staticmethod
    def _to_messages(messages: list[dict]) -> list[Message]:
        """OpenAI-style [{role, content}] -> Agno Messages.

        The agent owns the system prompt, so drop inbound system messages to
        avoid duplicate/conflicting instructions.
        """
        return [
            Message(role=m["role"], content=m.get("content", ""))
            for m in messages
            if m.get("role") != "system" and m.get("content")
        ]

    async def arun(self, messages: list[dict], session_id: str | None = None) -> str:
        result = await self._agent.arun(
            input=self._to_messages(messages), session_id=session_id
        )
        return result.content or ""

    async def astream(
        self, messages: list[dict], session_id: str | None = None
    ) -> AsyncIterator[str]:
        async for event in self._agent.arun(
            input=self._to_messages(messages), session_id=session_id, stream=True
        ):
            if isinstance(event, RunContentEvent) and event.content:
                yield event.content
