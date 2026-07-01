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

_TYPING_REFRESH_SECONDS = 2.0


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
        # Active session tracking (hermes pattern)
        self._active_sessions: dict[str, asyncio.Event] = {}
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()

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
        await self._cancel_background_tasks()
        if self.application.updater and self.application.updater.running:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()

    async def _cancel_background_tasks(self):
        """Cancel all background processing tasks on shutdown."""
        if not self._background_tasks:
            return
        tasks = [t for t in self._background_tasks if not t.done()]
        if tasks:
            logger.info("Cancelling %d background task(s)", len(tasks))
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._active_sessions.clear()
        self._session_tasks.clear()

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("MetaOps ready. Send a message to begin.")

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        session_id = self.session_manager.get_session_id("telegram", user_id)
        self._initialized_sessions.discard(session_id)
        self.session_manager.clear_session("telegram", user_id)
        # Cancel any active task for this session
        task = self._session_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
        self._active_sessions.pop(session_id, None)
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

    def _heal_stale_lock(self, session_id: str):
        """Clear stale session lock if the owner task already exited."""
        task = self._session_tasks.get(session_id)
        if task and task.done():
            logger.debug("Healing stale lock for session %s", session_id)
            self._active_sessions.pop(session_id, None)
            self._session_tasks.pop(session_id, None)
            self.session_manager.set_busy(session_id, False)

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

        # Self-heal stale locks before checking
        self._heal_stale_lock(session_id)

        if session_id in self._active_sessions:
            queue = self._get_pending_queue(session_id)
            try:
                queue.put_nowait((user_input, chat_id))
                count = queue.qsize()
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Queued ({count}/{self._config.max_pending_messages}). Processing after current turn."
                )
            except asyncio.QueueFull:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Message queue full. Please wait."
                )
            return

        await self._ensure_session(user_id, session_id, user.first_name or user_id)

        # Mark active BEFORE spawning task (hermes pattern — closes race window)
        interrupt_event = asyncio.Event()
        self._active_sessions[session_id] = interrupt_event
        self.session_manager.set_busy(session_id, True)

        task = asyncio.create_task(
            self._process_message(user_id, session_id, chat_id, user_input, interrupt_event)
        )
        self._session_tasks[session_id] = task
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _keep_typing(self, chat_id: int, stop_event: asyncio.Event):
        """Send typing indicator every 2 seconds until stop_event is set."""
        try:
            while not stop_event.is_set():
                try:
                    await self.application.bot.send_chat_action(chat_id=chat_id, action="typing")
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=_TYPING_REFRESH_SECONDS)
                    break  # event was set
                except asyncio.TimeoutError:
                    continue  # refresh typing
        except asyncio.CancelledError:
            pass

    async def _process_message(self, user_id: str, session_id: str, chat_id: int,
                                user_input: str, interrupt_event: asyncio.Event):
        """Background task: run agent, stream typing, send response."""
        typing_task = asyncio.create_task(self._keep_typing(chat_id, interrupt_event))
        checkpoint = SessionCheckpoint(f"telegram:{user_id}")
        try:
            text, error_code = await run_turn_with_continuation(
                runner=self.runner,
                user_id=user_id,
                session_id=session_id,
                message_text=user_input,
                run_config=_make_run_config(),
            )
            # Suppress stale response if session was interrupted
            if text and interrupt_event.is_set() and session_id in self._pending:
                logger.info("Suppressing stale response for interrupted session %s", session_id)
                text = None

            checkpoint.save({"last_user_input": user_input, "last_response": (text or "")[:2000]})
            if text:
                for i in range(0, len(text), 4000):
                    await self.application.bot.send_message(chat_id=chat_id, text=text[i : i + 4000])
            elif has_budget_exhausted(error_code):
                await self.application.bot.send_message(chat_id=chat_id, text="Response was consumed by internal reasoning. Please retry.")
        except asyncio.CancelledError:
            logger.debug("Task cancelled for session %s", session_id)
        except Exception as exc:
            logger.error("Error for user %s: %s", user_id, exc)
            try:
                await self.application.bot.send_message(chat_id=chat_id, text=f"System Error: {exc}")
            except Exception:
                pass
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            self._active_sessions.pop(session_id, None)
            self._session_tasks.pop(session_id, None)
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
                # Re-mark active for the drain turn
                interrupt_event = asyncio.Event()
                self._active_sessions[session_id] = interrupt_event
                self.session_manager.set_busy(session_id, True)
                typing_task = asyncio.create_task(self._keep_typing(chat_id, interrupt_event))
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
                    typing_task.cancel()
                    try:
                        await typing_task
                    except asyncio.CancelledError:
                        pass
                    self._active_sessions.pop(session_id, None)
                    self.session_manager.set_busy(session_id, False)
            except asyncio.QueueEmpty:
                break
            except asyncio.CancelledError:
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
