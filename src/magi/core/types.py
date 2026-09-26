"""Shared type aliases and parsers for genuinely free-form data.

`JsonValue` types anything that came from (or is going to) JSON — upstream API
payloads, config blobs, LLM tool arguments — without resorting to `typing.Any`.
Re-exported from pydantic so it composes with `BaseModel` / `TypeAdapter`
validation at the boundaries that receive it.

`parse_json_object` / `parse_json_array` are the pydantic-validated replacement
for `json.loads` + `isinstance` at boundaries whose callers degrade on bad input:
they return None instead of raising on malformed JSON or the wrong top-level shape.
"""

from pydantic import JsonValue, TypeAdapter, ValidationError

type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]

# Raising adapters, for boundaries where malformed input is a hard error.
JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_ARRAY: TypeAdapter[JsonArray] = TypeAdapter(JsonArray)


def parse_json_object(text: str | bytes) -> JsonObject | None:
    """`text` parsed as a JSON object, or None when malformed / not an object."""
    try:
        return JSON_OBJECT.validate_json(text)
    except ValidationError:
        return None


def parse_json_array(text: str | bytes) -> JsonArray | None:
    """`text` parsed as a JSON array, or None when malformed / not an array."""
    try:
        return _ARRAY.validate_json(text)
    except ValidationError:
        return None


__all__ = [
    "JSON_OBJECT",
    "JSON_VALUE",
    "JsonArray",
    "JsonObject",
    "JsonValue",
    "parse_json_array",
    "parse_json_object",
]
