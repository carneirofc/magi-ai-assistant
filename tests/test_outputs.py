"""Tests for the structured tool-output envelope (agent/tools/outputs).

The envelope's `data` is typed `DataT | None` with `DataT` bound to `BaseModel`.
A plain dict slipping through is the bug class that produced a `MockValSer`
serializer at runtime: pydantic coerced the dict to a *bare* BaseModel, dropping
the payload and breaking `model_dump_json`. These tests pin that down — a dict
must fail loudly at construction, and concrete payloads must round-trip.
"""

import pytest

from magi.agent.tools.outputs import FlexiblePayload, fail, ok


def test_ok_rejects_raw_dict_payload():
    with pytest.raises(TypeError, match="concrete BaseModel"):
        ok("done", {"reason": "x", "members": ["a"]})


def test_fail_rejects_raw_dict_payload():
    with pytest.raises(TypeError, match="concrete BaseModel"):
        fail("nope", {"reason": "x"})


def test_ok_with_none_payload_serializes():
    assert ok("done").model_dump_json()  # must not raise


def test_ok_with_concrete_payload_round_trips():
    result = ok("done", FlexiblePayload(text="payload"))

    assert '"data":{"text":"payload"}' in result.model_dump_json()
