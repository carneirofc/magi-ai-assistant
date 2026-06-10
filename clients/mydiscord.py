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
import re
from contextlib import asynccontextmanager
from textwrap import dedent

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
    ):
        self.conversation = conversation
        self.client = client
        self.token = token
        log_info("DiscordClient init")
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

            media = await self._extract_media(message)
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
                    message_user=message_user,
                    message_user_id=message_user_id,
                    message_url=message_url,
                )
                extra_context = self._build_additional_context(run_context)
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

    async def _extract_media(self, message) -> dict:
        """Map the first attachment to agno media kwargs for `*.arun(**media)`.

        Images/audio are passed by URL (agno fetches + encodes for the model);
        video/files are read as bytes since providers want raw content. Only the
        first attachment is processed — Discord allows many, the model takes one.
        """
        media: dict = {"images": None, "videos": None, "audio": None, "files": None}
        if not message.attachments:
            return media

        if len(message.attachments) > 1:
            log_warning(
                f"{len(message.attachments)} attachments received; only the first is processed"
            )

        att = message.attachments[0]
        ctype = att.content_type
        log_info(
            f"attachment: name={att.filename} type={ctype} size={att.size} bytes url={att.url}"
        )
        if not ctype:
            log_warning(f"attachment {att.filename} has no content_type; skipping media")
            return media

        # "image/png; codecs=..." -> "png"
        subtype = ctype.split("/", 1)[1].split(";", 1)[0].strip() or None
        if ctype.startswith("image/"):
            media["images"] = [Image(url=att.url, format=subtype, mime_type=ctype)]
        elif ctype.startswith("audio/"):
            media["audio"] = [Audio(url=att.url, format=subtype, mime_type=ctype)]
        elif ctype.startswith("video/"):
            media["videos"] = [Video(content=await att.read())]
        elif ctype.startswith("application/"):
            media["files"] = [File(content=await att.read())]
        else:
            log_warning(f"unhandled attachment content_type: {ctype}")
        return media

    def _build_run_context(
        self,
        *,
        message,
        target,
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
        """Render a channel-neutral reply onto the Discord target."""
        sent_any = False
        if reply.reasoning:
            sent_any = await self._send_discord_messages(
                thread=target, message=f"Reasoning: \n{reply.reasoning}", italics=True
            )
        sent_any = await self._send_discord_messages(thread=target, message=reply.text) or sent_any
        if not sent_any:
            log_warning(
                f"response produced no sendable text for target id={getattr(target, 'id', '?')}; "
                "sending fallback notice"
            )
            await self._send_discord_messages(
                thread=target,
                message="I finished processing that, but there was no text content to send.",
            )

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
