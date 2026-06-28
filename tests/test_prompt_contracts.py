"""Prompt/tool contracts for source-grounded media behavior.

These tests do not prove an LLM will obey every instruction, but they prevent
the guardrails for known failure modes from being edited away unnoticed.
"""

from pathlib import Path

from agent.tools.media import send_media_from_url

ROOT = Path(__file__).resolve().parents[1]


def _prompt(path: str) -> str:
    return (ROOT / "prompts" / path).read_text(encoding="utf-8")


def test_lead_routes_anime_media_to_seanime_not_generic_fetching():
    text = _prompt("team/lead.md")

    assert "covers, thumbnails, posters" in text
    assert "from Seanime" in text
    assert "Do not use generic `http_get`, `send_media_from_url`, Assistant, or Researcher" in text
    assert "show me a thumbnail for it" in text


def test_lead_forbids_unsourced_and_stale_media_urls():
    text = _prompt("team/lead.md")

    assert "Never invent, guess, repair, or reuse media URLs" in text
    assert "previous unrelated URL in memory is not a valid source" in text
    assert "Do not paste raw tool objects" in text


def test_lead_forbids_inventing_urls_for_attached_images():
    text = _prompt("team/lead.md")

    assert "already in your context" in text
    assert "never mention a file-upload" in text.lower()


def test_seanime_cover_contract_requires_current_tool_urls():
    text = _prompt("team/seanime.md")

    assert "thumbnail / cover / poster / image" in text
    assert "Use only the cover URLs returned by Seanime tools" in text
    assert "Do not reuse an image URL from an earlier assistant turn" in text
    assert "from the same Seanime result" in text


def test_media_tool_contract_is_delivery_only_not_search():
    instructions = send_media_from_url.instructions or ""
    doc = send_media_from_url.entrypoint.__doc__ or ""
    text = f"{instructions}\n{doc}"

    assert "Only use URLs supplied by the user or returned by a successful source-specific tool" in text
    assert "never invent, guess, repair, or reuse a stale media URL" in text
    assert "Do not use this to search for media" in text
