"""Prompt/tool contracts for source-grounded media behavior.

These tests do not prove an LLM will obey every instruction, but they prevent
the guardrails for known failure modes from being edited away unnoticed.
"""

from magi.agent.tools.media import send_media_from_url
from magi.core.prompts import BUNDLED_PROMPTS_DIR


def _prompt(path: str) -> str:
    return (BUNDLED_PROMPTS_DIR / path).read_text(encoding="utf-8")


# Persona-specific routing contracts (anime → Seanime, etc.) live with the
# persona that owns those members — see alyssa/tests. The engine keeps only the
# generic, persona-free guardrails its neutral demo lead.md still carries.


def test_lead_forbids_unsourced_and_stale_media_urls():
    text = _prompt("team/lead.md")

    assert "Never invent, guess, repair, or reuse media URLs" in text
    assert "previous unrelated URL in memory is not a valid source" in text
    assert "Do not paste raw tool objects" in text


def test_lead_forbids_inventing_urls_for_attached_images():
    text = _prompt("team/lead.md")

    assert "already in your context" in text
    assert "never mention a file-upload" in text.lower()


def test_media_tool_contract_is_delivery_only_not_search():
    instructions = send_media_from_url.instructions or ""
    doc = send_media_from_url.entrypoint.__doc__ or ""
    text = f"{instructions}\n{doc}"

    assert "Only use URLs supplied by the user or returned by a successful source-specific tool" in text
    assert "never invent, guess, repair, or reuse a stale media URL" in text
    assert "Do not use this to search for media" in text
