"""Tests for the global bot identity store (core.identity).

Pure filesystem IO over a temp root: fields + avatar round-trip, the version
token that guards concurrent edits, and the context text injected into a run.
"""

import base64
import tempfile
from pathlib import Path

import pytest

from magi.core.identity import BotIdentity, IdentityStore

# A 1x1 transparent PNG — the smallest valid avatar payload.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def _store() -> IdentityStore:
    return IdentityStore(Path(tempfile.mkdtemp()))


def test_empty_store_reads_blank_identity():
    ident = _store().read()
    assert ident == BotIdentity()
    assert ident.is_empty and not ident.has_avatar


def test_set_fields_round_trips_and_strips():
    store = _store()
    store.set_fields(display_name="  Alyssa  ", description="  calm, precise  ")
    ident = store.read()
    assert ident.display_name == "Alyssa" and ident.description == "calm, precise"
    assert not ident.has_avatar and not ident.is_empty


def test_avatar_set_read_and_clear():
    store = _store()
    store.set_avatar(_PNG, "image/png", "me.png")
    ident = store.read()
    assert ident.has_avatar and ident.avatar_mime == "image/png"
    assert ident.avatar_filename == "me.png"
    assert store.avatar_bytes() == (_PNG, "image/png")

    store.clear_avatar()
    assert not store.read().has_avatar
    assert store.avatar_bytes() is None


def test_set_fields_preserves_existing_avatar():
    store = _store()
    store.set_avatar(_PNG, "image/png")
    store.set_fields(display_name="Alyssa", description="")
    ident = store.read()
    assert ident.display_name == "Alyssa" and ident.has_avatar


def test_replacing_avatar_drops_the_old_file_extension():
    store = _store()
    store.set_avatar(_PNG, "image/png")
    store.set_avatar(_PNG, "image/gif")
    # Only the current extension survives — no orphaned avatar.png alongside avatar.gif.
    stored = sorted(p.name for p in store.avatar_dir.glob("avatar.*"))
    assert stored == ["avatar.gif"]
    assert store.read().avatar_mime == "image/gif"


def test_unsupported_mime_is_rejected():
    with pytest.raises(ValueError):
        _store().set_avatar(b"not-an-image", "application/pdf")


def test_version_moves_on_every_edit():
    store = _store()
    v_empty = store.version()
    store.set_fields(display_name="Alyssa", description="")
    v_named = store.version()
    store.set_avatar(_PNG, "image/png")
    v_avatar = store.version()
    store.clear_avatar()
    v_cleared = store.version()
    assert len({v_empty, v_named, v_avatar}) == 3
    # Clearing returns to the named-but-pictureless state's token (same bytes).
    assert v_cleared == v_named


def test_context_text_empty_when_nothing_set():
    assert _store().context_text() == ""


def test_context_text_describes_name_and_avatar():
    store = _store()
    store.set_fields(display_name="Alyssa", description="calm and precise")
    store.set_avatar(_PNG, "image/png")
    text = store.context_text()
    assert "Your name is Alyssa." in text
    assert "calm and precise" in text
    # The model is told it HAS a picture (to view/send on demand), not fed the pixels.
    assert "profile picture" in text
    assert "every turn" in text  # spells out that it isn't shown the image each turn


def test_context_text_without_avatar_omits_picture_note():
    store = _store()
    store.set_fields(display_name="Alyssa", description="")
    text = store.context_text()
    assert "Alyssa" in text and "profile picture" not in text


def test_corrupt_metadata_reads_as_blank():
    store = _store()
    store.meta_path.parent.mkdir(parents=True, exist_ok=True)
    store.meta_path.write_text("{not json", encoding="utf-8")
    assert store.read().is_empty
