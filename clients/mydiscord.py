import asyncio
import re
from contextlib import asynccontextmanager
from os import getenv
from textwrap import dedent
from typing import Optional, Union

from agno.agent.agent import Agent, RunOutput
from agno.media import Audio, File, Image, Video
from agno.team.team import Team, TeamRunOutput
from agno.utils.log import log_error, log_info, log_warning
from agno.utils.message import get_text_from_message

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
        agent: Optional[Agent] = None,
        team: Optional[Team] = None,
        client: Optional[discord.Client] = None,
        memory=None,
        channel_guidance: str = "",
    ):
        self.agent = agent
        self.team = team
        # Optional deliberate-memory manager (core.memory.MemoryManager). When
        # set, each run is scoped to the user/session, the model's persisted
        # memory is injected as context, and the turns are recorded.
        self.memory = memory
        # Channel-specific output rules (e.g. Discord markdown formatting). Kept
        # out of the base prompt so it stays channel-agnostic; appended to every
        # run's context here, where we know the channel IS Discord.
        self.channel_guidance = channel_guidance
        mode = "team" if team else "agent" if agent else "none"
        log_info(f"DiscordClient init: mode={mode}, custom_client={client is not None}")
        if client is None:
            self.intents = discord.Intents.all()
            self.client = discord.Client(intents=self.intents)
        else:
            self.client = client
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
                additional_context = dedent(f"""
                    Discord username: {message_user}
                    Discord userid: {message_user_id}
                    Discord url: {message_url}
                    """)
                if created_thread:
                    additional_context += (
                        "Note: a new thread was just opened for this user at their "
                        "request and you are now replying inside it. The thread "
                        "already exists — don't try to create another. Greet them "
                        "and carry on here.\n"
                    )
                response = await self._run(
                    input=message_text,
                    user_id=message_user_id,
                    session_id=session_id,
                    additional_context=additional_context,
                    media=media,
                )
                if response is None:
                    log_warning("no agent or team configured; dropping message")
                    return
                await self._handle_response_in_thread(response, target)

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

    async def _run(
        self,
        *,
        input: str,
        user_id,
        session_id: str,
        additional_context: str,
        media: dict,
    ) -> Optional[Union[RunOutput, TeamRunOutput]]:
        """Dispatch to the configured agent or team (one path for both)."""
        runner = self.agent or self.team
        if runner is None:
            return None

        kind = "agent" if self.agent else "team"
        log_info(f"dispatching to {kind} (session={session_id}, user={user_id})")

        # Assemble this run's context, in order: caller-supplied identity/channel
        # info, the model's persisted memory, then any channel output rules.
        parts = [additional_context]
        if self.memory is not None:
            self.memory.set_scope(user_id, session_id)
            # Pass the message as the query so semantic memory (when enabled) can
            # retrieve only the relevant facts/episodes instead of the whole file.
            parts.append(self.memory.build_context(query=input))
            self.memory.record_user_turn(input)
        if self.channel_guidance:
            parts.append(self.channel_guidance)

        runner.additional_context = "\n\n".join(p for p in parts if p and p.strip())
        response = await runner.arun(  # type: ignore[misc]
            input=input,
            user_id=user_id,
            session_id=session_id,
            **media,
        )
        log_info(
            f"{kind} response: status={response.status}, "
            f"content_len={len(response.content or '')}"
        )

        if self.memory is not None and response.content:
            self.memory.record_assistant_turn(get_text_from_message(response.content))
            # Fold any rolled-off turns into an episode (no-op unless enabled).
            await self.memory.maybe_summarize()
        if response.status == "ERROR":
            log_error(response.content)
            response.content = (
                "Sorry, there was an error processing your message. Please try again later."
            )
        return response

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
            if self.memory is None:
                await channel.send("Memory isn't enabled — nothing to flush.")
                return True
            self.memory.set_scope(user_id, session_id)
            dropped = self.memory.flush_session()
            await channel.send(
                f"🧹 Cleared **{dropped}** turn(s) of short-term history for this chat. "
                "Long-term memory and past episodes are kept."
            )
            return True

        if cmd in ("ctx", "context"):
            log_info(f"command !{cmd} from user={user_id} session={session_id}")
            if self.memory is None:
                await channel.send("Memory isn't enabled — no context to report.")
                return True
            self.memory.set_scope(user_id, session_id)
            st = self.memory.context_stats()
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

    async def handle_hitl(
        self, run_response: RunOutput, thread: Union[discord.Thread, discord.TextChannel]
    ) -> RunOutput:
        """Handles optional Human-In-The-Loop interaction."""
        if run_response.is_paused:
            log_info(
                f"run paused for HITL: {len(run_response.tools_requiring_confirmation)} "
                "tool(s) need confirmation"
            )
            for tool in run_response.tools_requiring_confirmation:
                view = RequiresConfirmationView()
                await thread.send(f"Tool requiring confirmation: {tool.tool_name}", view=view)
                await view.wait()
                tool.confirmed = view.value if view.value is not None else False
                log_info(f"tool '{tool.tool_name}' confirmation: {tool.confirmed}")

            if self.agent:
                log_info("continuing agent run after HITL")
                run_response = await self.agent.acontinue_run(  # type: ignore[misc]
                    run_response=run_response,
                )

        return run_response

    async def _handle_response_in_thread(
        self, response: Union[RunOutput, TeamRunOutput], thread: Union[discord.TextChannel, discord.Thread]
    ):
        if isinstance(response, RunOutput):
            response = await self.handle_hitl(response, thread)

        sent_any = False
        if response.reasoning_content:
            sent_any = await self._send_discord_messages(
                thread=thread, message=f"Reasoning: \n{response.reasoning_content}", italics=True
            )

        # Handle structured outputs properly
        content_message = get_text_from_message(response.content) if response.content is not None else ""
        sent_any = await self._send_discord_messages(thread=thread, message=content_message) or sent_any
        if not sent_any:
            log_warning(
                f"response produced no sendable text for target id={getattr(thread, 'id', '?')}; "
                "sending fallback notice"
            )
            await self._send_discord_messages(
                thread=thread,
                message="I finished processing that, but there was no text content to send.",
            )

    async def _send_discord_messages(
        self, thread: discord.channel, message: str, italics: bool = False
    ) -> bool:  # type: ignore
        if not message or not message.strip():
            log_warning(
                f"skipping empty Discord message for target id={getattr(thread, 'id', '?')}"
            )
            return False

        if len(message) < 1500:
            if italics:
                formatted_message = "\n".join([f"_{line}_" for line in message.split("\n")])
                await thread.send(formatted_message)  # type: ignore
            else:
                await thread.send(message)  # type: ignore
            return True

        message_batches = [message[i : i + 1500] for i in range(0, len(message), 1500)]

        for i, batch in enumerate(message_batches, 1):
            batch_message = f"[{i}/{len(message_batches)}] {batch}"
            if italics:
                formatted_batch = "\n".join([f"_{line}_" for line in batch_message.split("\n")])
                await thread.send(formatted_batch)  # type: ignore
            else:
                await thread.send(batch_message)  # type: ignore
        return True

    def serve(self):
        try:
            token = getenv("DISCORD_BOT_TOKEN")
            if not token:
                raise ValueError("DISCORD_BOT_TOKEN NOT SET")
            log_info("starting discord client (connecting to gateway)...")
            return self.client.run(token)
        except Exception as e:
            raise ValueError(f"Failed to run Discord client: {str(e)}")
