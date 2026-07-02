import asyncio
import logging
import os
import re
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
        self.bot_id = None
        self.bot_username = None
        # Active session tracking (hermes pattern)
        self._active_sessions: dict[str, asyncio.Event] = {}
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._background_tasks: set[asyncio.Task] = set()

    async def start(self):
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("clear", self.cmd_clear))
        self.application.add_handler(CommandHandler("help", self.cmd_help))
        self.application.add_handler(CommandHandler("new", self.cmd_new))
        self.application.add_handler(CommandHandler("stop", self.cmd_stop))
        self.application.add_handler(CommandHandler("status", self.cmd_status))
        self.application.add_handler(CommandHandler("resume", self.cmd_resume))
        
        # MessageHandler matches TEXT (both normal message and channel post)
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        # Also register a handler to catch commands sent in channel posts
        self.application.add_handler(
            MessageHandler(filters.COMMAND & filters.ChatType.CHANNEL, self.handle_channel_command)
        )

        await self.application.initialize()
        await self.application.start()
        
        # Fetch bot user info to resolve bot username and ID for mentions
        bot_info = await self.application.bot.get_me()
        self.bot_id = bot_info.id
        self.bot_username = bot_info.username
        logger.info("Telegram bot @%s connected (ID: %s)", self.bot_username, self.bot_id)

        if self._config.telegram_mode == "webhook":
            await self.application.updater.start_webhook(
                listen=self._config.telegram_webhook_listen_host,
                port=self._config.telegram_webhook_listen_port,
                url_path=self._config.telegram_webhook_path.lstrip("/"),
                webhook_url=self._config.telegram_webhook_url,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
                secret_token=self._config.telegram_webhook_secret_token or None,
                max_connections=self._config.telegram_webhook_max_connections,
            )
            logger.info("Telegram gateway webhook running on %s:%s%s", 
                        self._config.telegram_webhook_listen_host,
                        self._config.telegram_webhook_listen_port,
                        self._config.telegram_webhook_path)
        else:
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
        message = update.effective_message
        if not message:
            return
        chat = update.effective_chat
        reply_id = message.message_id if self._config.telegram_reply_to_message else None
        await context.bot.send_message(
            chat_id=chat.id,
            text="MetaOps ready. Send a message to begin.",
            reply_to_message_id=reply_id
        )

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        if not message:
            return
        chat = update.effective_chat
        user = update.effective_user
        
        if user:
            user_id = str(user.id)
            user_name = user.first_name or user_id
        else:
            user_id = f"channel_{chat.id}"
            user_name = chat.title or user_id

        # Authorization check
        is_allowed = False
        if self.allowed_user_ids is None:
            is_allowed = True
        else:
            if user_id in self.allowed_user_ids or str(chat.id) in self.allowed_user_ids:
                is_allowed = True
            elif user and str(user.id) in self.allowed_user_ids:
                is_allowed = True

        if not is_allowed:
            if chat.type not in ("group", "supergroup", "channel"):
                await context.bot.send_message(chat_id=chat.id, text="Access denied.")
            return

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
        
        reply_id = message.message_id if self._config.telegram_reply_to_message else None
        await context.bot.send_message(
            chat_id=chat.id,
            text="Session cleared. Send a message to start a new one.",
            reply_to_message_id=reply_id
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "🤖 *MetaOps Bot Commands*\n\n"
            "🔸 /start — Initialize the bot and check connectivity.\n"
            "🔸 /new — Start a new clean session (clears history/session database).\n"
            "🔸 /status — Show current session info (session ID, active model, token limits).\n"
            "🔸 /stop — Kill any running background execution tasks for your session.\n"
            "🔸 /clear — Clear the current session history.\n"
            "🔸 /resume <name> — Switch to/resume a named session."
        )
        reply_id = update.effective_message.message_id if self._config.telegram_reply_to_message else None
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="Markdown",
            reply_to_message_id=reply_id
        )

    async def cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        user_id = str(user.id)
        session_id = self.session_manager.get_session_id("telegram", user_id)
        
        # Authorization check
        chat = update.effective_chat
        is_allowed = False
        if self.allowed_user_ids is None:
            is_allowed = True
        else:
            if user_id in self.allowed_user_ids or str(chat.id) in self.allowed_user_ids:
                is_allowed = True
            elif str(user.id) in self.allowed_user_ids:
                is_allowed = True

        if not is_allowed:
            if chat.type not in ("group", "supergroup", "channel"):
                await context.bot.send_message(chat_id=chat.id, text="Access denied.")
            return

        self._initialized_sessions.discard(session_id)
        self.session_manager.clear_session("telegram", user_id)
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
                logger.error("Failed to delete session %s: %s", session_id, e)
        
        # Generate new session ID and ensure it is created
        new_session_id = self.session_manager.get_session_id("telegram", user_id)
        await self._ensure_session(user_id, new_session_id, user.first_name or user_id)
        
        reply_id = update.effective_message.message_id if self._config.telegram_reply_to_message else None
        await context.bot.send_message(
            chat_id=chat.id,
            text="✨ Fresh session started! Send a message to begin.",
            reply_to_message_id=reply_id
        )

    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        user_id = str(user.id)
        session_id = self.session_manager.get_session_id("telegram", user_id)
        
        task = self._session_tasks.pop(session_id, None)
        interrupted = False
        if task and not task.done():
            task.cancel()
            interrupted = True
            
        interrupt_event = self._active_sessions.pop(session_id, None)
        if interrupt_event:
            interrupt_event.set()
            interrupted = True
            
        self.session_manager.set_busy(session_id, False)
        
        # Clear pending queue
        queue = self._pending.pop(session_id, None)
        
        reply_id = update.effective_message.message_id if self._config.telegram_reply_to_message else None
        if interrupted or queue:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🛑 Active task and pending messages stopped.",
                reply_to_message_id=reply_id
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="No active task running.",
                reply_to_message_id=reply_id
            )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        user_id = str(user.id)
        session_id = self.session_manager.get_session_id("telegram", user_id)
        
        status_msg = (
            f"📋 *MetaOps Session Status*\n\n"
            f"👤 *User*: {user.first_name} (ID: `{user_id}`)\n"
            f"🔑 *Role*: `{self.default_role}`\n"
            f"🆔 *Session ID*: `{session_id}`\n\n"
            f"⚙️ *Configuration*:\n"
            f"• Coordinator: `{self._config.coordinator.provider}` / `{self._config.coordinator.model}`\n"
            f"• Workstream: `{self._config.workstream.provider}` / `{self._config.workstream.model}`\n"
            f"• Auditor: `{self._config.auditor.provider}` / `{self._config.auditor.model}`\n"
            f"• Portkey Gateway: `{'Active' if os.getenv('PORTKEY_GATEWAY_URL') else 'Disabled'}`\n"
        )
        
        reply_id = update.effective_message.message_id if self._config.telegram_reply_to_message else None
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=status_msg,
            parse_mode="Markdown",
            reply_to_message_id=reply_id
        )

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        user_id = str(user.id)
        
        args = context.args
        reply_id = update.effective_message.message_id if self._config.telegram_reply_to_message else None
        if not args:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Please specify the session name. Example: `/resume session_name`",
                parse_mode="Markdown",
                reply_to_message_id=reply_id
            )
            return
            
        target_session = args[0]
        key = f"telegram:{user_id}"
        self.session_manager._user_to_session[key] = target_session
        
        await self._ensure_session(user_id, target_session, user.first_name or user_id)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🔄 Switched/resumed session to: `{target_session}`",
            parse_mode="Markdown",
            reply_to_message_id=reply_id
        )

    async def handle_channel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        if not message or not message.text:
            return
        cmd = message.text.split()[0].lower()
        if "@" in cmd:
            cmd = cmd.split("@")[0]
        
        if cmd == "/start":
            await self.cmd_start(update, context)
        elif cmd == "/clear":
            await self.cmd_clear(update, context)
        elif cmd == "/help":
            await self.cmd_help(update, context)
        elif cmd == "/new":
            await self.cmd_new(update, context)
        elif cmd == "/stop":
            await self.cmd_stop(update, context)
        elif cmd == "/status":
            await self.cmd_status(update, context)
        elif cmd == "/resume":
            await self.cmd_resume(update, context)

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
        message = update.effective_message
        if not message or not message.text:
            return

        chat = update.effective_chat
        chat_id = chat.id
        user = update.effective_user

        # Identify user/session
        if user:
            user_id = str(user.id)
            user_name = user.first_name or user_id
        else:
            user_id = f"channel_{chat_id}"
            user_name = chat.title or user_id

        # Check authorization
        is_allowed = False
        if self.allowed_user_ids is None:
            is_allowed = True
        else:
            if user_id in self.allowed_user_ids or str(chat_id) in self.allowed_user_ids:
                is_allowed = True
            elif user and str(user.id) in self.allowed_user_ids:
                is_allowed = True

        if not is_allowed:
            if chat.type in ("group", "supergroup", "channel"):
                logger.warning("Ignored unauthorized message in %s chat_id=%s", chat.type, chat_id)
            else:
                logger.warning("Rejected message from non-allowlisted Telegram user_id=%s", user_id)
                await context.bot.send_message(chat_id=chat_id, text="Access denied.")
            return

        # Check group policy (if in group/supergroup/channel)
        if chat.type in ("group", "supergroup", "channel"):
            policy = self._config.telegram_group_policy
            if policy == "mention":
                is_mentioned = False
                bot_username = self.bot_username
                if bot_username:
                    if f"@{bot_username.lower()}" in message.text.lower():
                        is_mentioned = True
                
                # Check if it's a reply to our own message
                if not is_mentioned and message.reply_to_message:
                    reply_to = message.reply_to_message
                    if reply_to.from_user and reply_to.from_user.id == self.bot_id:
                        is_mentioned = True
                
                if not is_mentioned:
                    return

        session_id = self.session_manager.get_session_id("telegram", user_id)

        # Self-heal stale locks before checking
        self._heal_stale_lock(session_id)

        user_input = message.text
        bot_username = self.bot_username
        if bot_username and chat.type in ("group", "supergroup", "channel"):
            mention_str = f"@{bot_username}"
            pattern = re.compile(re.escape(mention_str), re.IGNORECASE)
            user_input = pattern.sub("", user_input).strip()

        if session_id in self._active_sessions:
            queue = self._get_pending_queue(session_id)
            try:
                queue.put_nowait((user_input, chat_id, message.message_id))
                count = queue.qsize()
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Queued ({count}/{self._config.max_pending_messages}). Processing after current turn.",
                    reply_to_message_id=message.message_id if self._config.telegram_reply_to_message else None
                )
            except asyncio.QueueFull:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Message queue full. Please wait.",
                    reply_to_message_id=message.message_id if self._config.telegram_reply_to_message else None
                )
            return

        await self._ensure_session(user_id, session_id, user_name)

        # Mark active BEFORE spawning task (closes race window)
        interrupt_event = asyncio.Event()
        self._active_sessions[session_id] = interrupt_event
        self.session_manager.set_busy(session_id, True)

        # React with emoji if configured
        react_emoji = self._config.telegram_react_emoji
        if react_emoji:
            try:
                from telegram import ReactionTypeEmoji
                await context.bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    reaction=[ReactionTypeEmoji(emoji=react_emoji)],
                )
            except Exception as e:
                logger.debug("Failed to set reaction: %s", e)

        task = asyncio.create_task(
            self._process_message(user_id, session_id, chat_id, user_input, interrupt_event, message.message_id)
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
                                user_input: str, interrupt_event: asyncio.Event, reply_to_message_id: Optional[int] = None):
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
            if text and interrupt_event.is_set() and session_id in self._pending:
                logger.info("Suppressing stale response for interrupted session %s", session_id)
                text = None

            checkpoint.save({"last_user_input": user_input, "last_response": (text or "")[:2000]})
            if text:
                reply_id = reply_to_message_id if self._config.telegram_reply_to_message else None
                for i in range(0, len(text), 4000):
                    await self.application.bot.send_message(
                        chat_id=chat_id, 
                        text=text[i : i + 4000],
                        reply_to_message_id=reply_id
                    )
            elif has_budget_exhausted(error_code):
                await self.application.bot.send_message(
                    chat_id=chat_id, 
                    text="Response was consumed by internal reasoning. Please retry.",
                    reply_to_message_id=reply_to_message_id if self._config.telegram_reply_to_message else None
                )
        except asyncio.CancelledError:
            logger.debug("Task cancelled for session %s", session_id)
        except Exception as exc:
            logger.error("Error for user %s: %s", user_id, exc)
            try:
                await self.application.bot.send_message(
                    chat_id=chat_id, 
                    text=f"System Error: {exc}",
                    reply_to_message_id=reply_to_message_id if self._config.telegram_reply_to_message else None
                )
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
                msg_text, chat_id, msg_id = queue.get_nowait()
                logger.info("Draining queued message for session %s", session_id)
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
                        reply_id = msg_id if self._config.telegram_reply_to_message else None
                        for i in range(0, len(text), 4000):
                            await self.application.bot.send_message(
                                chat_id=chat_id, 
                                text=text[i : i + 4000],
                                reply_to_message_id=reply_id
                            )
                    elif has_budget_exhausted(error_code):
                        await self.application.bot.send_message(
                            chat_id=chat_id, 
                            text="Response was consumed by internal reasoning. Please retry.",
                            reply_to_message_id=msg_id if self._config.telegram_reply_to_message else None
                        )
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
