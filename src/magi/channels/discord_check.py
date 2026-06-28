"""Discord debug client — Phase 1 check.

Connects to the server, announces in #general, then logs all events to stdout.
Each handler is its own function — easy to add breakpoints or extend.

Covered event types:
  - Text messages
  - Attachments (images, files)
  - Emoji reactions (Unicode + custom guild emoji)
  - Voice state changes (join / leave / move / mute / stream)

Run: python main_discord.py check
"""

import re

import discord

GUILD_ID = 1511488658350542878


# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------


def _build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True           # see member joins/leaves
    intents.messages = True
    intents.message_content = True   # privileged — Dev Portal → Bot → Privileged Gateway Intents
    intents.reactions = True
    intents.voice_states = True
    intents.presences = False        # not needed; requires extra permission if enabled
    return intents


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


async def _on_ready(client: discord.Client) -> None:
    _log("ready", f"Connected as {client.user} (id={client.user.id})")

    guild = client.get_guild(GUILD_ID)
    if guild is None:
        _log("ready", f"ERROR: guild {GUILD_ID} not found — bot not in this server")
        await client.close()
        return

    _log("ready", f"Guild   : {guild.name} ({guild.id})")
    _log("ready", f"Members : {guild.member_count}")
    _log("ready", f"Text    : {[c.name for c in guild.text_channels]}")
    _log("ready", f"Voice   : {[c.name for c in guild.voice_channels]}")

    general = discord.utils.get(guild.text_channels, name="general")
    if general is None:
        _log("ready", "WARNING: #general not found — check channel list above")
        return

    _log("ready", "Sending announcement to #general...")
    try:
        await general.send("Bot online. Listening for messages.")
        _log("ready", f"Announced in #{general.name}. Ctrl+C to stop.")
    except discord.Forbidden:
        _log("ready", "ERROR: missing Send Messages permission in #general")
    except discord.HTTPException as e:
        _log("ready", f"ERROR: send failed — {e}")


async def _on_message(message: discord.Message, bot_user: discord.ClientUser) -> None:
    if message.author == bot_user:
        return

    _log("msg", f"#{message.channel} | {message.author}: {message.content!r}")

    for attachment in message.attachments:
        _on_attachment(attachment)

    for emoji_str in _extract_custom_emoji(message.content):
        _log("emoji", f"custom in message — {emoji_str}")


def _on_attachment(attachment: discord.Attachment) -> None:
    is_image = bool(attachment.content_type and attachment.content_type.startswith("image/"))
    kind = "image" if is_image else "file"
    size_kb = attachment.size // 1024
    dims = f" {attachment.width}x{attachment.height}px" if is_image and attachment.width else ""
    _log("attach", f"{kind} — {attachment.filename} ({size_kb} KB{dims})")
    _log("attach", f"  content_type={attachment.content_type}")
    _log("attach", f"  url={attachment.url}")


async def _on_reaction_add(
    reaction: discord.Reaction, user: discord.User | discord.Member
) -> None:
    if isinstance(reaction.emoji, str):
        _log("react", f"{user} added Unicode emoji {reaction.emoji!r} on msg {reaction.message.id}")
    else:
        animated = "animated " if getattr(reaction.emoji, "animated", False) else ""
        _log("react", f"{user} added {animated}custom emoji :{reaction.emoji.name}: (id={reaction.emoji.id})")


async def _on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    if before.channel is None and after.channel is not None:
        _log("voice", f"{member} joined #{after.channel.name}")
    elif before.channel is not None and after.channel is None:
        _log("voice", f"{member} left #{before.channel.name}")
    elif before.channel != after.channel:
        _log("voice", f"{member} moved #{before.channel.name} → #{after.channel.name}")
    else:
        # same channel — mute / deafen / stream state flip
        changes: list[str] = []
        if before.self_mute != after.self_mute:
            changes.append(f"self_mute={after.self_mute}")
        if before.self_deaf != after.self_deaf:
            changes.append(f"self_deaf={after.self_deaf}")
        if before.self_stream != after.self_stream:
            changes.append(f"streaming={after.self_stream}")
        if before.self_video != after.self_video:
            changes.append(f"video={after.self_video}")
        if changes:
            channel_name = after.channel.name if after.channel else "?"
            _log("voice", f"{member} in #{channel_name}: {', '.join(changes)}")


def _extract_custom_emoji(content: str) -> list[str]:
    # <:name:id>  or  <a:name:id> (animated)
    return re.findall(r"<a?:\w+:\d+>", content)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run(token: str) -> None:
    client = discord.Client(intents=_build_intents())

    @client.event
    async def on_ready() -> None:
        await _on_ready(client)

    @client.event
    async def on_message(message: discord.Message) -> None:
        await _on_message(message, client.user)

    @client.event
    async def on_reaction_add(
        reaction: discord.Reaction, user: discord.User | discord.Member
    ) -> None:
        await _on_reaction_add(reaction, user)

    @client.event
    async def on_voice_state_update(
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        await _on_voice_state_update(member, before, after)

    await client.start(token)
