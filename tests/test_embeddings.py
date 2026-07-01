"""Tests for the embedding route switch (core/embeddings).

`embed_text` is crash-proof and needs no live endpoint here — these pin the pure
routing decision (`_route`): which model prefix + base URL + key the configured
`embeddings_provider` selects. The litellm path keeps the historical
`litellm_proxy/` routing; the openai path targets the remote OpenAI-compatible
endpoint so a deployment can serve chat locally and embeddings remotely.
"""

import dataclasses

import magi.core.embeddings as emb


def _with(**overrides):
    return dataclasses.replace(emb.config, **overrides)


def test_route_litellm_is_default(monkeypatch):
    monkeypatch.setattr(emb, "config", _with(embeddings_provider="litellm"))
    model, base, key = emb._route("nomic-embed-text")
    assert model == "litellm_proxy/nomic-embed-text"
    assert base == emb.config.litellm_base_url
    assert key == emb.config.litellm_api_key


def test_route_litellm_prefix_not_doubled(monkeypatch):
    monkeypatch.setattr(emb, "config", _with(embeddings_provider="litellm"))
    model, _base, _key = emb._route("litellm_proxy/nomic-embed-text")
    assert model == "litellm_proxy/nomic-embed-text"


def test_route_openai_targets_remote(monkeypatch):
    monkeypatch.setattr(
        emb,
        "config",
        _with(
            embeddings_provider="openai",
            openai_base_url="https://api.example.com/v1",
            openai_api_key="sk-remote",
        ),
    )
    model, base, key = emb._route("text-embedding-3-small")
    assert model == "openai/text-embedding-3-small"
    assert base == "https://api.example.com/v1"
    assert key == "sk-remote"


def test_route_openai_prefix_not_doubled(monkeypatch):
    monkeypatch.setattr(emb, "config", _with(embeddings_provider="openai"))
    model, _base, _key = emb._route("openai/text-embedding-3-small")
    assert model == "openai/text-embedding-3-small"


def test_embed_empty_text_is_none():
    assert emb.embed_text("   ") is None
