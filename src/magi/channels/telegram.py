"""Telegram channel — a long-polling bot over the shared `ConversationService`.

Needs the optional `telegram` extra (python-telegram-bot, lazy-imported in
`serve_async`). Long polling means no public URL or webhook. The adapter owns
only transport: it scopes the user through `scoped_user_id("telegram", …)`,
turns photos/documents into agno media, and renders the channel-neutral reply
back (text chunked to Telegram's 4096-char limit, then media).

Access is **deny by default**: only ids in `config.telegram_allowed_users` are
served. Anyone else gets their numeric id back so the operator can allowlist
them (`magi config set telegram_allowed_users "[123]"`).
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from agno.media import File, Image
from agno.utils.log import log_info, log_warning

from magi.channels.gateway import scoped_user_id
from magi.core.config import config
from magi.core.conversation import ConversationReply, ConversationService
from magi.core.prompts import load_prompt

PLATFORM = "telegram"
MESSAGE_LIMIT = 4096
_HELP = (
    "Commands:\n"
    "/new — start fresh (clears this chat's short-term history)\n"
    "/whoami — show your Telegram user id"
)


def chunk(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """`text` split into in-order parts of at most `limit` chars."""
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def session_id_for(chat_id: int) -> str:
    return f"tg-{chat_id}"


type SendText = Callable[[str], Awaitable[object]]
type SendMedia = Callable[[bytes | str], Awaitable[object]]


@dataclass
class Outbound:
    """How to answer one chat — injected so the logic is testable without PTB."""

    text: SendText
    photo: SendMedia
    document: SendMedia


@dataclass
class Inbound:
    user_id: int
    chat_id: int
    text: str
    images: list[Image] = field(default_factory=list)
    files: list[File] = field(default_factory=list)


class TelegramAdapter:
    """This channel as a `gateway.PlatformAdapter` (ADR 0005)."""

    platform: str = PLATFORM

    def __init__(
        self,
        conversation: ConversationService,
        token: str,
        allowed_users: Sequence[int],
    ) -> None:
        self.conversation = conversation
        self.token = token
        self.allowed_users = frozenset(allowed_users)

    def is_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_users

    async def respond(self, msg: Inbound, out: Outbound) -> None:
        """Handle one inbound message end to end (commands, access, the turn)."""
        command = msg.text.strip().split(maxsplit=1)[0].lower() if msg.text.strip() else ""
        command = command.split("@", 1)[0]  # /new@my_bot in groups
        if command == "/whoami":
            await out.text(f"Your Telegram user id is {msg.user_id}.")
            return
        if not self.is_allowed(msg.user_id):
            log_warning(f"telegram: refused user id {msg.user_id} (not in telegram_allowed_users)")
            await out.text(
                f"Sorry, I'm not open to you yet. Ask the operator to add your id "
                f"{msg.user_id} to telegram_allowed_users."
            )
            return
        user = scoped_user_id(self.platform, msg.user_id)
        session = session_id_for(msg.chat_id)
        if command in ("/start", "/help"):
            await out.text(_HELP)
            return
        if command in ("/new", "/reset", "/flush"):
            dropped = self.conversation.flush(user, session)
            await out.text(f"Fresh start — cleared {dropped} turn(s) from this chat.")
            return
        if not msg.text.strip() and not msg.images and not msg.files:
            return
        media: dict[str, object] = {}
        if msg.images:
            media["images"] = msg.images
        if msg.files:
            media["files"] = msg.files
        reply = await self.conversation.handle(
            user_id=user,
            session_id=session,
            text=msg.text or "(the user sent an attachment)",
            media=media,
        )
        await self.send_reply(reply, out)

    async def send_reply(self, reply: ConversationReply, out: Outbound) -> None:
        sent = False
        for part in chunk(reply.text.strip()):
            await out.text(part)
            sent = True
        for image in reply.images:
            payload = _media_payload(image)
            if payload is not None:
                await out.photo(payload)
                sent = True
        for file in reply.files:
            payload = _media_payload(file)
            if payload is not None:
                await out.document(payload)
                sent = True
        if not sent:
            await out.text("I finished that, but there was nothing to send back.")

    async def serve_async(self) -> None:
        """Long-poll Telegram until cancelled (the PTB manual lifecycle, so it
        shares the event loop with the other gateway adapters)."""
        import asyncio

        from telegram import Update
        from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

        async def on_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
            message = update.effective_message
            user = update.effective_user
            if message is None or user is None:
                return
            inbound = Inbound(
                user_id=user.id,
                chat_id=message.chat_id,
                text=message.text or message.caption or "",
            )
            if message.photo:
                largest = message.photo[-1]
                data = await (await largest.get_file()).download_as_bytearray()
                inbound.images.append(Image(content=bytes(data)))
            if message.document is not None:
                doc = message.document
                data = await (await doc.get_file()).download_as_bytearray()
                inbound.files.append(
                    File(content=bytes(data), mime_type=doc.mime_type, filename=doc.file_name)
                )
            await message.chat.send_action("typing")
            await self.respond(
                inbound,
                Outbound(
                    text=message.reply_text,
                    photo=message.reply_photo,
                    document=message.reply_document,
                ),
            )

        app = ApplicationBuilder().token(self.token).build()
        app.add_handler(MessageHandler(filters.ALL, on_message))
        log_info(f"telegram: polling (allowed users: {sorted(self.allowed_users) or 'none'})")
        async with app:
            await app.start()
            updater = app.updater
            if updater is None:
                raise RuntimeError("telegram: application built without an updater")
            await updater.start_polling()
            try:
                await asyncio.Event().wait()  # until the gateway cancels us
            finally:
                await updater.stop()
                await app.stop()


def _media_payload(item: Image | File) -> bytes | str | None:
    if isinstance(item.content, bytes):
        return item.content
    return item.url


def build_telegram_adapter(conversation: ConversationService) -> TelegramAdapter:
    """The Telegram bot over a (shared) conversation, with Telegram's output rules."""
    if not config.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set (add it to $MAGI_HOME/.env)")
    if not config.telegram_allowed_users:
        log_warning("telegram: telegram_allowed_users is empty — every user will be refused")
    return TelegramAdapter(
        conversation.with_guidance(load_prompt("channels/telegram.md")),
        token=config.telegram_bot_token,
        allowed_users=config.telegram_allowed_users,
    )
