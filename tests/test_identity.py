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


# --- expression pack (mood-keyed portraits; issue #26) ------------------------
_JPG = _PNG  # any bytes will do; the store trusts the declared mime


def test_expressions_empty_on_a_blank_store():
    store = _store()
    assert store.expressions() == {}
    assert store.expression_bytes("wry") is None


def test_set_expression_round_trips_bytes_and_mime():
    store = _store()
    store.set_expression("wry", _PNG, "image/png", "wry.png")

    got = store.expression_bytes("wry")
    assert got == (_PNG, "image/png")
    pack = store.expressions()
    assert pack["wry"]["mime"] == "image/png"
    assert pack["wry"]["filename"] == "wry.png"
    assert pack["wry"]["version"]


def test_neutral_expression_is_the_avatar_slot():
    store = _store()
    # Uploading via the legacy avatar path surfaces as the neutral expression…
    store.set_avatar(_PNG, "image/png", "face.png")
    assert store.expression_bytes("neutral") == (_PNG, "image/png")
    assert "neutral" in store.expressions()

    # …and uploading the neutral expression IS an avatar upload.
    store.clear_avatar()
    store.set_expression("neutral", _JPG, "image/jpeg")
    assert store.avatar_bytes() == (_JPG, "image/jpeg")
    assert store.read().has_avatar

    # Clearing neutral clears the avatar.
    store.clear_expression("neutral")
    assert store.avatar_bytes() is None
    assert store.expressions() == {}


def test_replacing_an_expression_drops_the_old_extension():
    store = _store()
    store.set_expression("warm", _PNG, "image/png")
    store.set_expression("warm", _JPG, "image/jpeg")

    files = list(store.avatar_dir.glob("expression-warm.*"))
    assert [f.suffix for f in files] == [".jpg"]
    assert store.expression_bytes("warm") == (_JPG, "image/jpeg")


def test_clear_expression_removes_file_and_metadata():
    store = _store()
    store.set_expression("focused", _PNG, "image/png")
    store.clear_expression("focused")

    assert store.expression_bytes("focused") is None
    assert store.expressions() == {}
    assert list(store.avatar_dir.glob("expression-focused.*")) == []


def test_expression_rejects_bad_mood_keys_and_mimes():
    store = _store()
    with pytest.raises(ValueError):
        store.set_expression("Wry Face", _PNG, "image/png")  # spaces/case
    with pytest.raises(ValueError):
        store.set_expression("../evil", _PNG, "image/png")  # path-ish
    with pytest.raises(ValueError):
        store.set_expression("wry", _PNG, "text/html")  # not an image


def test_version_moves_on_expression_edits():
    store = _store()
    v0 = store.version()
    store.set_expression("wry", _PNG, "image/png")
    v1 = store.version()
    assert v1 != v0
    # Re-uploading the same mood with different bytes must move the token even
    # though the stored filename stays identical.
    store.set_expression("wry", _PNG + b"x", "image/png")
    v2 = store.version()
    assert v2 != v1
    store.clear_expression("wry")
    assert store.version() != v2
