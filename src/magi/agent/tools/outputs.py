"""Shared structured output envelopes for model-facing tools."""

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    @field_validator("data")
    @classmethod
    def _data_must_be_concrete(cls, value: object) -> object:
        """Fail fast on a payload that lost its serializer.

        `data` is typed `DataT | None` with `DataT` bound to `BaseModel`. Hand it a
        plain ``dict`` and pydantic silently coerces it to a *bare* ``BaseModel`` —
        keys dropped, serializer left as a ``MockValSer`` — so the result both loses
        its payload and blows up on ``model_dump_json`` downstream. Catch it here,
        at construction, with a message that points at the fix instead of leaving a
        cryptic serialization error to surface in a tool hook later.
        """
        if value is not None and type(value) is BaseModel:
            raise TypeError(
                "ToolOutput.data must be a concrete BaseModel, not a bare dict. "
                "Wrap dynamic dicts in FlexiblePayload(**data) or a dedicated model."
            )
        return value

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
