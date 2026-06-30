import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google.adk.runners import Runner
from google.adk.events import Event
from google.genai import types
from metaops.gateway.base import BaseGateway
from metaops.gateway.session_manager import SessionManager
from metaops.memory.session_service import SQLiteSessionService

logger = logging.getLogger(__name__)


class TelegramBridge(BaseGateway):
    def __init__(
        self,
        runner: Runner,
        session_manager: SessionManager,
        token: str,
        session_service: SQLiteSessionService = None,
        default_role: str = "admin",
    ):
        self.runner = runner
        self.session_manager = session_manager
        self.token = token
        self._session_service = session_service
        self.default_role = default_role
        self._initialized_sessions: set = set()
        self.application = Application.builder().token(token).build()

    async def start(self):
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("clear", self.cmd_clear))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True)
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
        self.session_manager._user_to_session.pop(user_id, None)
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

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = str(user.id)
        chat_id = update.effective_chat.id
        user_input = update.message.text
        session_id = self.session_manager.get_session_id("telegram", user_id)

        if self.session_manager.is_busy(session_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Je suis en train de traiter votre message précédent. Veuillez patienter..."
            )
            return

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        await self._ensure_session(user_id, session_id, user.first_name or user_id)

        content = types.Content(role="user", parts=[types.Part(text=user_input)])
        try:
            self.session_manager.set_busy(session_id, True)
            async for event in self.runner.run_async(
                user_id=user_id, session_id=session_id, new_message=content
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    text = event.content.parts[0].text
                    if text:
                        for i in range(0, len(text), 4000):
                            await context.bot.send_message(
                                chat_id=chat_id, text=text[i : i + 4000]
                            )
        except Exception as exc:
            logger.error("Error for user %s: %s", user_id, exc)
            await context.bot.send_message(chat_id=chat_id, text=f"System Error: {exc}")
        finally:
            self.session_manager.set_busy(session_id, False)

    async def send_direct_message(self, chat_id: str, text: str):
        if self.application and self.application.bot:
            for i in range(0, len(text), 4000):
                await self.application.bot.send_message(
                    chat_id=chat_id, text=text[i : i + 4000]
                )

    async def send_event(self, event: Event):
        pass
