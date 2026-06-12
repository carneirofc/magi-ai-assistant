"""Shared structured output envelopes for model-facing tools."""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class EmptyData(BaseModel):
    """Payload type for tool results that do not carry structured data."""


DataT = TypeVar("DataT", bound=BaseModel)


class ToolOutput(BaseModel, Generic[DataT]):
    """Structured result envelope for model-facing tools."""

    success: bool = Field(description="Whether the tool completed successfully.")
    status: Literal["ok", "error"] = Field(description="Machine-readable result status.")
    message: str = Field(description="Human-readable summary of the result.")
    data: DataT | None = Field(default=None, description="Structured payload returned by the tool, if any.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings from the tool.")

    def get(self, key: str, default=None):
        value = getattr(self, key, default)
        if key == "data" and isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return value

    def __getitem__(self, key: str):
        return self.get(key)


class FlexiblePayload(BaseModel):
    """Named Pydantic payload for legacy endpoints whose response shape is dynamic."""

    model_config = ConfigDict(extra="allow")


def ok(
    message: str,
    data: DataT | None = None,
    warnings: list[str] | None = None,
) -> ToolOutput[DataT]:
    """Return a successful tool result."""
    return ToolOutput(
        success=True,
        status="ok",
        message=message,
        data=data,
        warnings=warnings or [],
    )


def fail(
    message: str,
    data: DataT | None = None,
    warnings: list[str] | None = None,
) -> ToolOutput[DataT]:
    """Return a failed tool result."""
    return ToolOutput(
        success=False,
        status="error",
        message=message,
        data=data,
        warnings=warnings or [],
    )
