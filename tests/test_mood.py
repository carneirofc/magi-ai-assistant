"""Tests for the pre-reply mood pass (agent.mood).

The pass's reliability comes from constrained decoding (output_schema →
response_format json_schema), so what's testable offline is the defensive rim:
extraction from both content shapes, the out-of-vocabulary rejection, and the
build-time vocabulary guard. The fallback path through a live turn is covered in
test_conversation (an injected mood_fn) and the wire in test_api.
"""

from types import SimpleNamespace

import pytest

from magi.agent.mood import _extract_mood, build_mood_pass
from magi.core.config import config, configure

_VALID = frozenset({"neutral", "wry"})


def test_extract_mood_reads_the_parsed_schema_instance():
    assert _extract_mood(SimpleNamespace(mood="wry"), _VALID) == "wry"


def test_extract_mood_rejects_out_of_vocabulary_values():
    assert _extract_mood(SimpleNamespace(mood="sassy"), _VALID) is None


def test_extract_mood_parses_a_raw_json_string():
    assert _extract_mood('{"mood": "neutral"}', _VALID) == "neutral"


def test_extract_mood_parses_json_wrapped_in_prose():
    assert _extract_mood('sure thing: {"mood": "wry"} — done', _VALID) == "wry"


def test_extract_mood_none_for_garbage():
    assert _extract_mood("no json here", _VALID) is None
    assert _extract_mood('{"mood": 3}', _VALID) is None
    assert _extract_mood(None, _VALID) is None


def test_build_mood_pass_requires_a_vocabulary():
    old = config.mood_vocabulary
    configure(mood_vocabulary={})
    try:
        with pytest.raises(ValueError):
            build_mood_pass()
    finally:
        configure(mood_vocabulary=old)
