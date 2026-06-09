"""Pure message-splitting for Discord sends — no Discord, no IO.

Discord's hard per-message cap is 2000 chars; we send below that with undocumented
headroom. The limit lives here once as `DISCORD_MESSAGE_LIMIT`; `chunk` is a plain
function so the splitting can be tested without a Discord client.
"""

# Discord's real cap is 2000; this leaves headroom for the `[i/n]` prefix and
# any wrapping the send layer adds. Named once so the size has a single home.
DISCORD_MESSAGE_LIMIT = 1500


def chunk(text: str, limit: int) -> list[str]:
    """Split `text` into in-order parts each at most `limit` chars.

    Concatenating the parts reproduces `text` exactly. Empty text yields no parts.
    """
    return [text[i : i + limit] for i in range(0, len(text), limit)]
