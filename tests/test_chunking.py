from clients.chunking import DISCORD_MESSAGE_LIMIT, chunk


def test_short_text_is_a_single_unprefixed_part():
    text = "hello there"

    assert chunk(text, DISCORD_MESSAGE_LIMIT) == [text]


def test_empty_text_yields_no_parts():
    assert chunk("", DISCORD_MESSAGE_LIMIT) == []


def test_long_text_splits_into_parts_that_reconstruct_the_original():
    text = "x" * (DISCORD_MESSAGE_LIMIT * 2 + 7)

    parts = chunk(text, DISCORD_MESSAGE_LIMIT)

    assert len(parts) == 3
    assert all(len(p) <= DISCORD_MESSAGE_LIMIT for p in parts)
    assert "".join(parts) == text
