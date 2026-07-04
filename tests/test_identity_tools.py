"""Tests for the bot's own-picture tools (agent.tools.identity).

`view_profile_picture` loads the avatar into the model's context (view-only, not
delivered); `send_profile_picture` stages it in the run's media outbox for
delivery. Both act on the injected memory's identity store and degrade to an
honest message when no picture is set.
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace

from magi.agent.tools.identity import build_identity_tools
from magi.core.identity import IdentityStore
from magi.core.media import close_media_outbox, is_view_only, open_media_outbox

_PNG = (
    b"\x89PNG\r\n\x1a\n"  # a plausible png header; the tools never decode it
)


def _tools(with_avatar: bool):
    store = IdentityStore(Path(tempfile.mkdtemp()))
    if with_avatar:
        store.set_avatar(_PNG, "image/png", "me.png")
    memory = SimpleNamespace(store=SimpleNamespace(identity=store))
    view, send = build_identity_tools(memory)
    return view, send, store


def test_view_loads_the_avatar_view_only():
    view, _send, _ = _tools(with_avatar=True)

    result = view.entrypoint()

    assert len(result.images) == 1
    image = result.images[0]
    assert image.content == _PNG and image.mime_type == "image/png"
    assert is_view_only(image)  # model input, never reposted to the user
    assert "context" in result.content.lower()


def test_view_without_a_picture_reports_it():
    view, _send, _ = _tools(with_avatar=False)

    result = view.entrypoint()

    assert not result.images
    assert "don't have a profile picture" in result.content


def test_send_stages_the_avatar_in_the_outbox():
    _view, send, _ = _tools(with_avatar=True)

    token = open_media_outbox()
    try:
        result = send.entrypoint()
    finally:
        outbox = close_media_outbox(token)

    # Delivered via the outbox, NOT loaded into context (no images on the result).
    assert not getattr(result, "images", None)
    assert len(outbox.images) == 1 and outbox.images[0].content == _PNG
    assert "attached your profile picture" in result.content.lower()


def test_send_without_an_outbox_is_honest():
    _view, send, _ = _tools(with_avatar=True)

    result = send.entrypoint()  # no outbox open (bare run)

    assert "isn't available" in result.content


def test_send_without_a_picture_reports_it():
    _view, send, _ = _tools(with_avatar=False)

    token = open_media_outbox()
    try:
        result = send.entrypoint()
    finally:
        outbox = close_media_outbox(token)

    assert not outbox.images
    assert "don't have a profile picture" in result.content
