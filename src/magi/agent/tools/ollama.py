"""Ollama introspection tools.

Query the Ollama server (config.ollama_host) for what models exist, their
capabilities / context length, and what is currently loaded. Each docstring is
read by the model to decide WHEN to call the tool — keep it precise.

Tools return short human-readable strings (and never raise): on failure they
return an error line the model can relay, so a down server degrades gracefully.
"""

from typing import Annotated

import httpx
from agno.tools import tool
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, fail, ok
from magi.core.config import config
from magi.core.types import JsonObject, JsonValue

_TIMEOUT = 10.0


class OllamaModelRow(BaseModel):
    name: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None
    size_gb: float | None = None


class OllamaModelsData(BaseModel):
    models: list[OllamaModelRow]


class OllamaModelErrorData(BaseModel):
    model: str | None = None


class OllamaModelInfoData(BaseModel):
    model: str
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    native_context_length: int | None = None


class RunningOllamaModelRow(BaseModel):
    name: str | None = None
    loaded_context: int | None = None
    vram_gb: float | None = None


class RunningOllamaModelsData(BaseModel):
    models: list[RunningOllamaModelRow]


# --- Ollama wire shapes: the slice of each /api response the tools read. ---
# Untrusted upstream JSON is validated here, never navigated with .get() chains.


class _WireDetails(BaseModel):
    family: str | None = None
    parameter_size: str | None = None
    quantization_level: str | None = None


class _WireTagsModel(BaseModel):
    name: str | None = None
    size: int | None = None
    details: _WireDetails | None = None


class _WireTags(BaseModel):
    models: list[_WireTagsModel] = Field(default_factory=list)


class _WireShow(BaseModel):
    capabilities: list[str] | None = None
    model_info: dict[str, JsonValue] | None = None
    details: _WireDetails | None = None


class _WirePsModel(BaseModel):
    name: str | None = None
    size_vram: int | None = None
    context_length: int | None = None


class _WirePs(BaseModel):
    models: list[_WirePsModel] = Field(default_factory=list)


def _get[M: BaseModel](path: str, shape: type[M], timeout: float = _TIMEOUT) -> M:
    r = httpx.get(f"{config.ollama_host}{path}", timeout=timeout)
    r.raise_for_status()
    return shape.model_validate_json(r.content)


def _post[M: BaseModel](
    path: str, body: JsonObject, shape: type[M], timeout: float = _TIMEOUT
) -> M:
    r = httpx.post(f"{config.ollama_host}{path}", json=body, timeout=timeout)
    r.raise_for_status()
    return shape.model_validate_json(r.content)


def _context_length(model_info: dict[str, JsonValue] | None) -> int | None:
    """Pull the "<arch>.context_length" value out of an /api/show model_info."""
    return next(
        (
            v
            for k, v in (model_info or {}).items()
            if k.endswith(".context_length") and isinstance(v, int)
        ),
        None,
    )


@tool(
    description="List models installed on the configured Ollama server.",
    instructions="Use when asked which local Ollama models are available. Takes no arguments.",
    show_result=True,
)
def list_ollama_models() -> ToolOutput[OllamaModelsData]:
    """List models installed on the Ollama server.

    Use when asked what models Ollama has / are available locally. Returns each
    model's name, parameter size, quantization, and on-disk size.
    """
    try:
        data = _get("/api/tags", _WireTags)
    except Exception as e:
        return fail(f"Failed to reach Ollama at {config.ollama_host}: {e}")
    if not data.models:
        return ok("No models installed on Ollama.", OllamaModelsData(models=[]))
    items: list[OllamaModelRow] = []
    for m in data.models:
        d = m.details or _WireDetails()
        items.append(
            OllamaModelRow(
                name=m.name,
                parameter_size=d.parameter_size,
                quantization_level=d.quantization_level,
                size_gb=round((m.size or 0) / 1e9, 1),
            )
        )
    return ok("Ollama models.", OllamaModelsData(models=items))


@tool(
    description="Show capabilities, context length, and details for one Ollama model.",
    instructions="Use to check whether a local model supports tools, vision, or a required context window. Pass the exact Ollama model name.",
    show_result=True,
)
def show_ollama_model(
    model: Annotated[
        str,
        Field(min_length=1, description="Exact Ollama model name to inspect."),
    ],
) -> ToolOutput[OllamaModelInfoData | OllamaModelErrorData]:
    """Show details for one Ollama model: capabilities, context length, params.

    `model` is the model name (e.g. "qwen3.5-9b-uncensored" or "gemma4:e4b"). Use
    to check whether a model supports vision/tools or how large its native context
    window is.
    """
    try:
        data = _post("/api/show", {"model": model}, _WireShow)
    except Exception as e:
        return fail(f"Failed to show '{model}': {e}", OllamaModelErrorData(model=model))
    d = data.details or _WireDetails()
    return ok(
        f"Model {model}.",
        OllamaModelInfoData(
            model=model,
            family=d.family,
            parameter_size=d.parameter_size,
            quantization_level=d.quantization_level,
            capabilities=data.capabilities or [],
            native_context_length=_context_length(data.model_info),
        ),
    )


@tool(
    description="List Ollama models currently loaded in memory.",
    instructions="Use to see which models are running now and what context size they loaded with. Takes no arguments.",
    show_result=True,
)
def list_running_ollama_models() -> ToolOutput[RunningOllamaModelsData]:
    """List models currently loaded in Ollama memory, with loaded context size.

    Use to see what is running now and the context window each model was loaded
    with — handy to confirm a model actually loaded at the expected num_ctx.
    """
    try:
        data = _get("/api/ps", _WirePs)
    except Exception as e:
        return fail(f"Failed to reach Ollama at {config.ollama_host}: {e}")
    if not data.models:
        return ok("No models currently loaded in Ollama.", RunningOllamaModelsData(models=[]))
    items = [
        RunningOllamaModelRow(
            name=m.name,
            loaded_context=m.context_length,
            vram_gb=round((m.size_vram or 0) / 1e9, 1),
        )
        for m in data.models
    ]
    return ok("Loaded Ollama models.", RunningOllamaModelsData(models=items))
