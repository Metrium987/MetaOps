import asyncio
import logging
from typing import Optional
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google.adk.runners import Runner
from google.adk.events import Event
from google.adk.agents.run_config import RunConfig
from metaops.gateway.base import BaseGateway
from google.adk.sessions import BaseSessionService
from metaops.gateway.session_manager import SessionManager
from metaops.core.continuation import (
    run_turn_with_continuation,
    has_budget_exhausted,
)
from metaops.core.session_checkpoint import SessionCheckpoint
from metaops.config import get_config

logger = logging.getLogger(__name__)


def _make_run_config() -> RunConfig:
    return RunConfig(max_llm_calls=get_config().gateway_max_llm_calls)


class TelegramBridge(BaseGateway):
    def __init__(
        self,
        runner: Runner,
        session_manager: SessionManager,
        token: str,
        session_service: BaseSessionService = None,
        default_role: str = "admin",
        allowed_user_ids: Optional[set[str]] = None,
    ):
        self.runner = runner
        self.session_manager = session_manager
        self.token = token
        self._session_service = session_service
        self.default_role = default_role
        self.allowed_user_ids = allowed_user_ids
        if self.allowed_user_ids is None:
            logger.warning(
                "No METAOPS_TELEGRAM_ALLOWED_USERS configured — this bot will "
                "respond to any Telegram user who messages it, with role=%s.",
                default_role,
            )
        self._initialized_sessions: set = set()
        self._pending: dict[str, asyncio.Queue] = {}
        self.application = Application.builder().token(token).build()
        self._config = get_config()

    async def start(self):
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("clear", self.cmd_clear))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        logger.info("Telegram gateway polling.")

    async def stop(self):
        if self.application.updater and self.application.updater.running:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("MetaOps ready. Send a message to begin.")

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        session_id = self.session_manager.get_session_id("telegram", user_id)
        self._initialized_sessions.discard(session_id)
        self.session_manager.clear_session("telegram", user_id)
        if self._session_service:
            try:
                await self._session_service.delete_session(
                    app_name="metaops_enterprise", user_id=user_id, session_id=session_id
                )
            except Exception as e:
                logger.error("Failed to delete session %s from database: %s", session_id, e)
        await update.message.reply_text("Session cleared. Send a message to start a new one.")

    async def _ensure_session(self, user_id: str, session_id: str, user_name: str):
        if self._session_service and session_id not in self._initialized_sessions:
            existing = await self._session_service.get_session(
                app_name="metaops_enterprise", user_id=user_id, session_id=session_id
            )
            if not existing:
                await self._session_service.create_session(
                    app_name="metaops_enterprise",
                    user_id=user_id,
                    session_id=session_id,
                    state={
                        "user:role": self.default_role,
                        "user:name": user_name,
                        "user:telegram_id": user_id,
                    },
                )
            self._initialized_sessions.add(session_id)

    def _get_pending_queue(self, session_id: str) -> asyncio.Queue:
        if session_id not in self._pending:
            self._pending[session_id] = asyncio.Queue(maxsize=self._config.max_pending_messages)
        return self._pending[session_id]

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)
        chat_id = update.effective_chat.id
        user_input = update.message.text

        if self.allowed_user_ids is not None and user_id not in self.allowed_user_ids:
            logger.warning("Rejected message from non-allowlisted Telegram user_id=%s", user_id)
            await context.bot.send_message(chat_id=chat_id, text="Access denied.")
            return

        session_id = self.session_manager.get_session_id("telegram", user_id)

        if self.session_manager.is_busy(session_id):
            queue = self._get_pending_queue(session_id)
            try:
                queue.put_nowait((user_input, chat_id))
                count = queue.qsize()
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Queued ({count}/{self._config.max_pending_messages}). Your message will be processed after the current turn."
                )
            except asyncio.QueueFull:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Message queue full. Please wait for the current turn to complete."
                )
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        await self._ensure_session(user_id, session_id, user.first_name or user_id)
        self.session_manager.set_busy(session_id, True)

        # Spawn background task so the handler returns immediately and
        # Telegram polling + other users are not blocked during the LLM call.
        asyncio.create_task(
            self._process_message(user_id, session_id, chat_id, user_input)
        )

    async def _process_message(self, user_id: str, session_id: str, chat_id: int, user_input: str):
        """Background task: run the agent and send the response."""
        checkpoint = SessionCheckpoint(f"telegram:{user_id}")
        try:
            text, error_code = await run_turn_with_continuation(
                runner=self.runner,
                user_id=user_id,
                session_id=session_id,
                message_text=user_input,
                run_config=_make_run_config(),
            )
            checkpoint.save({"last_user_input": user_input, "last_response": text[:2000]})
            if text:
                for i in range(0, len(text), 4000):
                    await self.application.bot.send_message(chat_id=chat_id, text=text[i : i + 4000])
            elif has_budget_exhausted(error_code):
                await self.application.bot.send_message(chat_id=chat_id, text="Response was consumed by internal reasoning. Please retry with a simpler request.")
        except Exception as exc:
            logger.error("Error for user %s: %s", user_id, exc)
            await self.application.bot.send_message(chat_id=chat_id, text=f"System Error: {exc}")
        finally:
            self.session_manager.set_busy(session_id, False)
            await self._drain_pending(session_id, user_id)

    async def _drain_pending(self, session_id: str, user_id: str):
        """Process queued messages after the current turn completes."""
        queue = self._pending.get(session_id)
        if not queue or queue.empty():
            return
        while not queue.empty():
            try:
                msg_text, chat_id = queue.get_nowait()
                logger.info("Draining queued message for session %s", session_id)
                self.session_manager.set_busy(session_id, True)
                try:
                    text, error_code = await run_turn_with_continuation(
                        runner=self.runner,
                        user_id=user_id,
                        session_id=session_id,
                        message_text=msg_text,
                        run_config=_make_run_config(),
                    )
                    if text:
                        for i in range(0, len(text), 4000):
                            await self.application.bot.send_message(chat_id=chat_id, text=text[i : i + 4000])
                    elif has_budget_exhausted(error_code):
                        await self.application.bot.send_message(chat_id=chat_id, text="Response was consumed by internal reasoning. Please retry.")
                finally:
                    self.session_manager.set_busy(session_id, False)
            except asyncio.QueueEmpty:
                break
            except Exception as exc:
                logger.error("Error processing queued message: %s", exc)

    async def send_direct_message(self, chat_id: str, text: str):
        if self.application and self.application.bot:
            for i in range(0, len(text), 4000):
                await self.application.bot.send_message(
                    chat_id=chat_id, text=text[i : i + 4000]
                )

    async def send_event(self, event: Event):
        pass
