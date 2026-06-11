"""Discord presentation layer.

This module owns ONLY Discord concerns: parsing inbound messages + attachments,
resolving where to reply (channel / thread / DM), thread creation with a
confirmation prompt, control commands, and chunked sending. The conversation
itself — running the agent/team and managing memory — lives behind the injected
`ConversationService` (core.conversation), which knows nothing about Discord.

Both the `ConversationService` and the `discord.Client` are injected by the
composition root (channels/discord.py); nothing is constructed here.
"""

import asyncio
import io
import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path
from textwrap import dedent
from urllib.parse import urlparse

import httpx
from agno.media import Audio, File, Image, Video
from agno.utils.log import log_info, log_warning

from clients.chunking import DISCORD_MESSAGE_LIMIT, chunk
from core.conversation import ConversationReply, ConversationService
from core.discord_context import (
    DiscordRunContext,
    reset_current_discord_context,
    set_current_discord_context,
)

try:
    import discord

except (ImportError, ModuleNotFoundError):
    raise ImportError("`discord.py` not installed. Please install using `pip install discord.py`")


# Intent verbs that, when paired with the word "thread", signal the user wants a
# brand-new thread. Kept narrow on purpose so a passing mention of "thread" alone
# won't trigger a confirmation prompt.
_NEW_THREAD_RE = re.compile(
    r"\b(?:new|start|create|open|fresh|separate|another|split|spin up|begin)\b[\s\w]*?\bthread\b",
    re.IGNORECASE,
)

# Discord custom emoji in message text: <:name:id> (static) / <a:name:id> (animated).
_CUSTOM_EMOJI_RE = re.compile(r"<(a?):(\w+):(\d{15,21})>")

# --- inbound media limits ----------------------------------------------------
# Total media items passed into one run (attachments + emoji + stickers).
_MAX_INBOUND_ITEMS = 10
# Per-item byte cap: media rides base64 into the model request; bigger than
# this is almost certainly not meant for the model.
_MAX_INBOUND_BYTES = 25 * 1024 * 1024
# Custom emoji per message wired into the model (each is one image).
_MAX_EMOJI = 4

# --- outbound media limits ---------------------------------------------------
# Discord free-tier per-file upload cap; bigger files fall back to a link.
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Discord hard limit on attachments per message.
_MAX_FILES_PER_MESSAGE = 10
_FETCH_TIMEOUT_S = 30.0
# Browser-ish UA — some CDNs (Discord's included) 403 the default httpx agent.
_FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AlyssaBot/1.0; +https://discord.com)"}


def _wants_new_thread(text: str) -> bool:
    """True when the user explicitly asks for a new thread.

    Conservative match: an intent verb (new/start/create/...) must precede the
    word "thread". Tune `_NEW_THREAD_RE` if it over- or under-fires.
    """
    if not text:
        return False
    return bool(_NEW_THREAD_RE.search(text))


class RequiresConfirmationView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.primary)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.value = True
        button.disabled = True
        await interaction.response.edit_message(view=self)
        self.clear_items()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.value = False
        button.disabled = True
        await interaction.response.edit_message(view=self)
        self.clear_items()
        self.stop()

    async def on_timeout(self):
        log_warning("Agent Timeout Error")


class DiscordClient:
    def __init__(
        self,
        conversation: ConversationService,
        client: discord.Client,
        token: str,
        supports_audio: bool = True,
    ):
        self.conversation = conversation
        self.client = client
        self.token = token
        # Whether the backing model can hear audio. When False, inbound audio
        # is not wired into the run (a vision-only backend would reject the
        # `input_audio` part) — the model gets a context note instead.
        self.supports_audio = supports_audio
        log_info(f"DiscordClient init (supports_audio={supports_audio})")
        self._setup_events()

    def _setup_events(self):
        @self.client.event
        async def on_ready():
            user = self.client.user
            guilds = self.client.guilds
            log_info(
                f"discord ready: logged in as {user} (id={getattr(user, 'id', '?')}); "
                f"{len(guilds)} guild(s): {[g.name for g in guilds]}"
            )

        @self.client.event
        async def on_message(message):
            if message.author == self.client.user:
                log_info(f"sent {message.content}")
                return

            message_text = message.content
            message_url = message.jump_url
            message_user = message.author.name
            message_user_id = message.author.id

            channel = message.channel

            # Control commands (`!flush`, `!ctx`, `!help`) short-circuit before we
            # fetch media or run the team. They act on THIS chat's session.
            if await self._maybe_handle_command(channel, message_text, message_user_id):
                return

            media, media_notes = await self._extract_media(message)
            channel_kind = type(channel).__name__
            log_info(
                f"message from {message_user} (id={message_user_id}) in {channel_kind} "
                f"(id={getattr(channel, 'id', '?')}): {message_text!r} | url={message_url}"
            )

            # Resolve where to reply (`target`) and the session it belongs to.
            # Default keeps the conversation in the SAME chat (channel / current
            # thread / DM). A brand-new thread is only created in a TextChannel,
            # on explicit user request, and after a confirmation prompt.
            target, session_id = await self._resolve_target(message, channel, message_text, message_user)
            if target is None:
                log_info(
                    f"received {message.content!r} but not in a supported channel "
                    f"({channel_kind}); ignoring"
                )
                return

            # The channel layer (not the team) owns Discord threads. If
            # _resolve_target just opened a new one, `target` is a Thread distinct
            # from the channel the message came in on. The original text ("start a
            # new thread") is then replayed into the team inside that thread, so we
            # must tell the lead the thread already exists — otherwise it tries to
            # open ANOTHER (which it can't: no Discord tools) and loops.
            created_thread = getattr(target, "id", None) != getattr(channel, "id", None)
            log_info(
                f"routing to session_id={session_id} (target={type(target).__name__}"
                f"{', new thread' if created_thread else ''})"
            )

            async with self._safe_typing(target):
                run_context = self._build_run_context(
                    message=message,
                    target=target,
                    message_text=message_text,
                    message_user=message_user,
                    message_user_id=message_user_id,
                    message_url=message_url,
                )
                extra_context = self._build_additional_context(run_context)
                if media_notes:
                    extra_context += "\nMedia notes:\n" + "\n".join(
                        f"- {note}" for note in media_notes
                    ) + "\n"
                if created_thread:
                    extra_context += (
                        "\nNote: a new thread was just opened for this user at their "
                        "request and you are now replying inside it. The thread "
                        "already exists — don't try to create another. Greet them "
                        "and carry on here.\n"
                    )
                token = set_current_discord_context(run_context)
                try:
                    reply = await self.conversation.handle(
                        user_id=message_user_id,
                        session_id=session_id,
                        text=message_text,
                        media=media,
                        extra_context=extra_context,
                    )
                    await self._send_reply(reply, target)
                finally:
                    reset_current_discord_context(token)

    async def _extract_media(self, message) -> tuple[dict, list[str]]:
        """Map ALL of a message's media to agno media kwargs for `*.arun(**media)`.

        Everything is read as raw bytes and handed to agno, which formats it per
        the OpenAI vision/audio spec (base64 data URLs / `input_audio` parts) —
        passing Discord CDN URLs through would break on local backends that
        can't fetch, and the URLs expire anyway. Covered sources: every
        attachment (image/audio/video/file), custom emoji in the text, and
        stickers. Returns `(media_kwargs, notes)` — notes are short lines for
        the model's context (what was attached, what was skipped and why).
        """
        images: list[Image] = []
        videos: list[Video] = []
        audio: list[Audio] = []
        files: list[File] = []
        notes: list[str] = []

        def total() -> int:
            return len(images) + len(videos) + len(audio) + len(files)

        for att in message.attachments:
            if total() >= _MAX_INBOUND_ITEMS:
                notes.append(
                    f"More attachments were sent than the {_MAX_INBOUND_ITEMS}-item limit; "
                    "the rest were skipped."
                )
                break
            ctype = (att.content_type or "").split(";", 1)[0].strip().lower()
            if not ctype:
                ctype = mimetypes.guess_type(att.filename or "")[0] or ""
            log_info(
                f"attachment: name={att.filename} type={ctype or '?'} size={att.size} bytes"
            )
            if att.size and att.size > _MAX_INBOUND_BYTES:
                notes.append(
                    f"Attachment '{att.filename}' ({att.size} bytes) is too large to process."
                )
                continue
            if ctype.startswith("audio/") and not self.supports_audio:
                notes.append(
                    f"The user attached the audio file '{att.filename}' ({ctype}), but the "
                    "current model cannot listen to audio. Say so if the audio matters."
                )
                continue
            try:
                data = await att.read()
            except discord.HTTPException as exc:
                log_warning(f"attachment read failed for {att.filename}: {exc}")
                notes.append(f"Attachment '{att.filename}' could not be downloaded.")
                continue

            subtype = ctype.split("/", 1)[1] if "/" in ctype else None
            if ctype.startswith("image/"):
                images.append(Image(content=data, format=subtype, mime_type=ctype))
                notes.append(f"The user attached the image '{att.filename}'.")
            elif ctype.startswith("audio/"):
                audio.append(Audio(content=data, format=subtype, mime_type=ctype))
                notes.append(f"The user attached the audio '{att.filename}'.")
            elif ctype.startswith("video/"):
                videos.append(Video(content=data, format=subtype, mime_type=ctype))
                notes.append(f"The user attached the video '{att.filename}'.")
            else:
                files.append(
                    File(content=data, mime_type=ctype or None, filename=att.filename)
                )
                notes.append(f"The user attached the file '{att.filename}' ({ctype or '?'}).")

        images, notes = await self._extract_emoji(message, images, notes)
        images, notes = await self._extract_stickers(message, images, notes)
        media = {
            "images": images or None,
            "videos": videos or None,
            "audio": audio or None,
            "files": files or None,
        }
        return media, notes

    async def _extract_emoji(
        self, message, images: list[Image], notes: list[str]
    ) -> tuple[list[Image], list[str]]:
        """Wire custom emoji (`<:name:id>`) into the run as images.

        The model otherwise sees only the raw tag and has no idea what the
        emoji looks like. Unicode emoji are plain text and need nothing.
        """
        seen: set[str] = set()
        for animated, name, emoji_id in _CUSTOM_EMOJI_RE.findall(message.content or ""):
            if emoji_id in seen:
                continue
            seen.add(emoji_id)
            if len(seen) > _MAX_EMOJI or len(images) >= _MAX_INBOUND_ITEMS:
                break
            ext = "gif" if animated else "png"
            url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
            data = await self._fetch_bytes(url)
            if data is None:
                continue
            images.append(Image(content=data, format=ext, mime_type=f"image/{ext}"))
            notes.append(f"The custom emoji :{name}: from the message is attached as an image.")
        return images, notes

    async def _extract_stickers(
        self, message, images: list[Image], notes: list[str]
    ) -> tuple[list[Image], list[str]]:
        """Wire message stickers into the run as images (lottie ones are JSON
        animations, not pixels — skipped with a note)."""
        for sticker in getattr(message, "stickers", None) or []:
            if len(images) >= _MAX_INBOUND_ITEMS:
                break
            if getattr(sticker, "format", None) == discord.StickerFormatType.lottie:
                notes.append(f"The sticker '{sticker.name}' is an animation and can't be viewed.")
                continue
            data = await self._fetch_bytes(sticker.url)
            if data is None:
                notes.append(f"The sticker '{sticker.name}' could not be downloaded.")
                continue
            ext = Path(urlparse(sticker.url).path).suffix.lstrip(".") or "png"
            images.append(Image(content=data, format=ext, mime_type=f"image/{ext}"))
            notes.append(f"The sticker '{sticker.name}' from the message is attached as an image.")
        return images, notes

    @staticmethod
    async def _fetch_bytes(url: str) -> bytes | None:
        """Fetch a CDN asset; None on any failure (callers degrade gracefully)."""
        try:
            async with httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT_S, follow_redirects=True, headers=_FETCH_HEADERS
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content
        except httpx.HTTPError as exc:
            log_warning(f"media fetch failed for {url}: {exc}")
            return None

    def _build_run_context(
        self,
        *,
        message,
        target,
        message_text: str,
        message_user: str,
        message_user_id,
        message_url: str,
    ) -> DiscordRunContext:
        guild = getattr(message, "guild", None)
        return DiscordRunContext(
            guild_id=str(guild.id) if guild is not None else None,
            guild_name=getattr(guild, "name", None),
            channel_id=str(getattr(target, "id", "")),
            channel_name=getattr(target, "name", None),
            channel_kind=type(target).__name__,
            message_id=str(message.id),
            message_url=message_url,
            message_text=message_text,
            user_id=str(message_user_id),
            username=message_user,
            channel=target,
        )

    def _build_additional_context(self, context: DiscordRunContext) -> str:
        lines = [
            f"Discord username: {context.username}",
            f"Discord userid: {context.user_id}",
            f"Discord url: {context.message_url}",
            f"Discord channel kind: {context.channel_kind}",
            f"Discord channel id: {context.channel_id}",
        ]
        if context.channel_name:
            lines.append(f"Discord channel name: {context.channel_name}")
        if context.guild_id:
            lines.append(f"Discord guild id: {context.guild_id}")
        if context.guild_name:
            lines.append(f"Discord guild name: {context.guild_name}")
        lines.append("Never invent Discord ids or placeholder channel names; use only the exact ids above.")
        return dedent("\n".join(lines))

    @asynccontextmanager
    async def _safe_typing(self, target):
        """Typing is cosmetic; timeouts here should not abort the reply."""
        typing_cm = None
        try:
            typing_cm = target.typing()
            await typing_cm.__aenter__()
        except (asyncio.TimeoutError, discord.HTTPException) as exc:
            log_warning(
                f"typing indicator failed for target id={getattr(target, 'id', '?')}: "
                f"{type(exc).__name__}: {exc}"
            )
            typing_cm = None

        try:
            yield
        except BaseException as exc:
            if typing_cm is not None:
                try:
                    if await typing_cm.__aexit__(type(exc), exc, exc.__traceback__):
                        return
                except (asyncio.TimeoutError, discord.HTTPException) as exit_exc:
                    log_warning(
                        f"typing indicator cleanup failed for target id={getattr(target, 'id', '?')}: "
                        f"{type(exit_exc).__name__}: {exit_exc}"
                    )
            raise
        else:
            if typing_cm is not None:
                try:
                    await typing_cm.__aexit__(None, None, None)
                except (asyncio.TimeoutError, discord.HTTPException) as exc:
                    log_warning(
                        f"typing indicator cleanup failed for target id={getattr(target, 'id', '?')}: "
                        f"{type(exc).__name__}: {exc}"
                    )

    async def _maybe_handle_command(self, channel, text: str, user_id) -> bool:
        """Handle `!`-prefixed control commands. Returns True if one was handled.

        Commands act on THIS chat's session (`channel.id`) — no new-thread logic —
        and reply inline. An unknown `!`-leading message is left to flow to the
        team as a normal message.
        """
        raw = (text or "").strip()
        if not raw.startswith("!"):
            return False
        cmd = raw.split()[0].lower().lstrip("!")
        session_id = str(getattr(channel, "id", "?"))

        if cmd in ("flush", "reset", "clear"):
            log_info(f"command !{cmd} from user={user_id} session={session_id}")
            dropped = self.conversation.flush(user_id, session_id)
            await channel.send(
                f"🧹 Cleared **{dropped}** turn(s) of short-term history for this chat. "
                "Long-term memory and past episodes are kept."
            )
            return True

        if cmd in ("ctx", "context"):
            log_info(f"command !{cmd} from user={user_id} session={session_id}")
            st = self.conversation.context_stats(user_id, session_id)
            sec = st["sections"]
            await channel.send(
                f"📊 Context **~{st['est_tokens']} tok** ({st['ratio']:.0%} of {st['budget_tokens']})\n"
                f"• short-term: {st['short_term_turns']} turns (~{sec['short_term']}t)\n"
                f"• long-term ~{sec['long_term']}t · episodes ~{sec['episodes']}t · "
                f"persona ~{sec['persona']}t\n"
                "Use `!flush` to reset this chat's short-term history."
            )
            return True

        if cmd in ("help", "commands"):
            await channel.send(
                "**Commands**\n"
                "• `!flush` — clear this chat's short-term history (keeps long-term)\n"
                "• `!ctx` — show context size + breakdown\n"
                "• `!help` — this message"
            )
            return True

        return False

    async def _resolve_target(self, message, channel, message_text: str, message_user: str):
        """Pick the reply target and its session id.

        Default = same chat (current channel / thread / DM). A brand-new thread is
        created only in a TextChannel, when the user asks for one and confirms the
        prompt. Returns (target, session_id), or (None, "") for unsupported channels.
        """
        # Already inside a thread → stay there (Discord has no sub-threads).
        if isinstance(channel, discord.Thread):
            if _wants_new_thread(message_text):
                log_info("new-thread request ignored: already inside a thread")
            return channel, str(channel.id)

        # DMs can't host threads → stay in the DM.
        if isinstance(channel, discord.channel.DMChannel):
            if _wants_new_thread(message_text):
                log_info("new-thread request ignored: DMs cannot host threads")
            return channel, str(channel.id)

        # Text channel → default is the channel itself (same chat). Only branch
        # off into a new thread on explicit request + confirmation.
        if isinstance(channel, discord.TextChannel):
            if _wants_new_thread(message_text):
                log_info(f"new-thread request detected from {message_user}; asking for confirmation")
                view = RequiresConfirmationView()
                await channel.send(
                    f"{message_user}, start a **new thread** for this conversation? "
                    "Otherwise I'll keep replying here.",
                    view=view,
                )
                await view.wait()
                if view.value:
                    thread = await message.create_thread(
                        name=self._thread_name(message_user, message_text)
                    )
                    log_info(f"created thread '{thread.name}' (id={thread.id}) for {message_user}")
                    return thread, str(thread.id)
                log_info(f"new thread declined/timed out; staying in channel id={channel.id}")
            return channel, str(channel.id)

        return None, ""

    @staticmethod
    def _thread_name(user: str, text: str) -> str:
        """Readable, mostly-unique thread name: user + short topic snippet."""
        snippet = " ".join(text.split())[:40].strip()
        return f"{user}: {snippet}" if snippet else f"{user}'s thread"

    async def _send_reply(self, reply: ConversationReply, target) -> None:
        """Render a channel-neutral reply onto the Discord target: text first,
        then any media as real attachments."""
        sent_any = False
        if reply.reasoning:
            sent_any = await self._send_discord_messages(
                thread=target, message=f"Reasoning: \n{reply.reasoning}", italics=True
            )
        sent_any = await self._send_discord_messages(thread=target, message=reply.text) or sent_any
        sent_any = await self._send_media(reply, target) or sent_any
        if not sent_any:
            log_warning(
                f"response produced no sendable text for target id={getattr(target, 'id', '?')}; "
                "sending fallback notice"
            )
            await self._send_discord_messages(
                thread=target,
                message="I finished processing that, but there was no text content to send.",
            )

    async def _send_media(self, reply: ConversationReply, target) -> bool:
        """Upload the reply's media as Discord attachments.

        Each agno media object is resolved to bytes (inline content, local
        file, or URL fetch) and uploaded; whatever can't be uploaded (fetch
        failed, over Discord's size cap) degrades to its URL as text so the
        user still gets *something*. Returns True if anything was sent.
        """
        if not reply.has_media:
            return False

        uploads: list[discord.File] = []
        fallback_lines: list[str] = []
        counters: dict[str, int] = {}
        for kind, items in (
            ("image", reply.images),
            ("video", reply.videos),
            ("audio", reply.audio),
            ("file", reply.files),
        ):
            for item in items:
                counters[kind] = counters.get(kind, 0) + 1
                name = self._media_filename(item, kind, counters[kind])
                data = await self._media_bytes(item)
                if data is None:
                    if getattr(item, "url", None):
                        fallback_lines.append(f"Couldn't attach {name} — link: {item.url}")
                    else:
                        log_warning(f"reply media {name} has no retrievable content; dropped")
                    continue
                if len(data) > _MAX_UPLOAD_BYTES:
                    if getattr(item, "url", None):
                        fallback_lines.append(
                            f"{name} is too large to upload — link: {item.url}"
                        )
                    else:
                        fallback_lines.append(
                            f"{name} ({len(data)} bytes) is too large to upload to Discord."
                        )
                    continue
                uploads.append(discord.File(io.BytesIO(data), filename=name))

        sent_any = False
        for start in range(0, len(uploads), _MAX_FILES_PER_MESSAGE):
            batch = uploads[start : start + _MAX_FILES_PER_MESSAGE]
            try:
                await target.send(files=batch)
                sent_any = True
                log_info(f"uploaded {len(batch)} attachment(s) to target id={getattr(target, 'id', '?')}")
            except discord.HTTPException as exc:
                log_warning(f"attachment upload failed: {exc}")
                fallback_lines.append("Some attachments failed to upload.")
        if fallback_lines:
            sent_any = (
                await self._send_discord_messages(thread=target, message="\n".join(fallback_lines))
                or sent_any
            )
        return sent_any

    @staticmethod
    def _media_filename(item, kind: str, index: int) -> str:
        """A display name for the upload: explicit filename > URL basename >
        generated `kind-N.ext` from format/mime."""
        explicit = getattr(item, "filename", None)
        if explicit:
            return explicit
        url = getattr(item, "url", None)
        if url:
            name = Path(urlparse(url).path).name
            if name and "." in name:
                return name
        ext = getattr(item, "format", None)
        if not ext:
            mime = getattr(item, "mime_type", None)
            guessed = mimetypes.guess_extension(mime) if mime else None
            ext = (guessed or ".bin").lstrip(".")
        return f"{kind}-{index}.{ext}"

    async def _media_bytes(self, item) -> bytes | None:
        """Resolve an agno media object to raw bytes; None when unavailable."""
        content = getattr(item, "content", None)
        if isinstance(content, bytes) and content:
            return content
        filepath = getattr(item, "filepath", None)
        if filepath:
            try:
                return await asyncio.to_thread(Path(filepath).read_bytes)
            except OSError as exc:
                log_warning(f"reply media file read failed for {filepath}: {exc}")
                return None
        url = getattr(item, "url", None)
        if url:
            return await self._fetch_bytes(url)
        return None

    @staticmethod
    def _italicize(text: str) -> str:
        """Wrap each line in `_..._` so Discord renders the whole part italic."""
        return "\n".join(f"_{line}_" for line in text.split("\n"))

    async def _send_discord_messages(
        self, thread: discord.channel, message: str, italics: bool = False
    ) -> bool:  # type: ignore
        if not message or not message.strip():
            log_warning(
                f"skipping empty Discord message for target id={getattr(thread, 'id', '?')}"
            )
            return False

        parts = chunk(message, DISCORD_MESSAGE_LIMIT)
        numbered = len(parts) > 1
        for i, part in enumerate(parts, 1):
            body = f"[{i}/{len(parts)}] {part}" if numbered else part
            await thread.send(self._italicize(body) if italics else body)  # type: ignore
        return True

    def serve(self) -> None:
        """Connect to the gateway and block until shutdown."""
        log_info("starting discord client (connecting to gateway)...")
        self.client.run(self.token)
