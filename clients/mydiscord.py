import re
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
        self, agent: Optional[Agent] = None, team: Optional[Team] = None, client: Optional[discord.Client] = None
    ):
        self.agent = agent
        self.team = team
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

            message_image = None
            message_video = None
            message_audio = None
            message_file = None
            media_url = None
            message_text = message.content
            message_url = message.jump_url
            message_user = message.author.name
            message_user_id = message.author.id

            if message.attachments:
                media = message.attachments[0]
                media_type = media.content_type
                media_url = media.url
                log_info(
                    f"attachment: name={media.filename} type={media_type} "
                    f"size={media.size} bytes url={media_url}"
                )
                if len(message.attachments) > 1:
                    log_warning(
                        f"{len(message.attachments)} attachments received; only the first is processed"
                    )
                if media_type is None:
                    log_warning(f"attachment {media.filename} has no content_type; skipping media")
                elif media_type.startswith("image/"):
                    message_image = media_url
                elif media_type.startswith("video/"):
                    message_video = await media.read()
                elif media_type.startswith("application/"):
                    message_file = await media.read()
                elif media_type.startswith("audio/"):
                    message_audio = media_url
                else:
                    log_warning(f"unhandled attachment content_type: {media_type}")

            channel = message.channel
            channel_kind = type(channel).__name__
            log_info(
                f"message from {message_user} (id={message_user_id}) in {channel_kind} "
                f"(id={getattr(channel, 'id', '?')}): {message_text!r} | media={media_url} | url={message_url}"
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
            log_info(f"routing to session_id={session_id} (target={type(target).__name__})")

            async with target.typing():
                # TODO Unhappy with the duplication here but it keeps MyPy from complaining
                additional_context = dedent(f"""
                    Discord username: {message_user}
                    Discord userid: {message_user_id} 
                    Discord url: {message_url}
                    """)
                if self.agent:
                    log_info(f"dispatching to agent (session={session_id}, user={message_user_id})")
                    self.agent.additional_context = additional_context
                    agent_response: RunOutput = await self.agent.arun(  # type: ignore[misc]
                        input=message_text,
                        user_id=message_user_id,
                        session_id=session_id,
                        images=[Image(url=message_image)] if message_image else None,
                        videos=[Video(content=message_video)] if message_video else None,
                        audio=[Audio(url=message_audio)] if message_audio else None,
                        files=[File(content=message_file)] if message_file else None,
                    )
                    log_info(
                        f"agent response: status={agent_response.status}, "
                        f"content_len={len(agent_response.content or '')}"
                    )
                    if agent_response.status == "ERROR":
                        log_error(agent_response.content)
                        agent_response.content = (
                            "Sorry, there was an error processing your message. Please try again later."
                        )
                    await self._handle_response_in_thread(agent_response, target)
                elif self.team:
                    log_info(f"dispatching to team (session={session_id}, user={message_user_id})")
                    self.team.additional_context = additional_context
                    team_response: TeamRunOutput = await self.team.arun(  # type: ignore[misc]
                        input=message_text,
                        user_id=message_user_id,
                        session_id=session_id,
                        images=[Image(url=message_image)] if message_image else None,
                        videos=[Video(content=message_video)] if message_video else None,
                        audio=[Audio(url=message_audio)] if message_audio else None,
                        files=[File(content=message_file)] if message_file else None,
                    )
                    log_info(
                        f"team response: status={team_response.status}, "
                        f"content_len={len(team_response.content or '')}"
                    )
                    if team_response.status == "ERROR":
                        log_error(team_response.content)
                        team_response.content = (
                            "Sorry, there was an error processing your message. Please try again later."
                        )

                    await self._handle_response_in_thread(team_response, target)
                else:
                    log_warning("no agent or team configured; dropping message")

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

        if response.reasoning_content:
            await self._send_discord_messages(
                thread=thread, message=f"Reasoning: \n{response.reasoning_content}", italics=True
            )

        # Handle structured outputs properly
        content_message = get_text_from_message(response.content) if response.content is not None else ""

        await self._send_discord_messages(thread=thread, message=content_message)

    async def _send_discord_messages(self, thread: discord.channel, message: str, italics: bool = False):  # type: ignore
        if len(message) < 1500:
            if italics:
                formatted_message = "\n".join([f"_{line}_" for line in message.split("\n")])
                await thread.send(formatted_message)  # type: ignore
            else:
                await thread.send(message)  # type: ignore
            return

        message_batches = [message[i : i + 1500] for i in range(0, len(message), 1500)]

        for i, batch in enumerate(message_batches, 1):
            batch_message = f"[{i}/{len(message_batches)}] {batch}"
            if italics:
                formatted_batch = "\n".join([f"_{line}_" for line in batch_message.split("\n")])
                await thread.send(formatted_batch)  # type: ignore
            else:
                await thread.send(batch_message)  # type: ignore

    def serve(self):
        try:
            token = getenv("DISCORD_BOT_TOKEN")
            if not token:
                raise ValueError("DISCORD_BOT_TOKEN NOT SET")
            log_info("starting discord client (connecting to gateway)...")
            return self.client.run(token)
        except Exception as e:
            raise ValueError(f"Failed to run Discord client: {str(e)}")
