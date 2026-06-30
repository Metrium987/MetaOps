import asyncio
import logging
from metaops.core.root import create_runner, session_service
from metaops.gateway.session_manager import SessionManager
from metaops.gateway.cli import CLIBridge
from metaops.gateway.telegram import TelegramBridge
from metaops.scheduler.cron import MetaOpsCronScheduler
from metaops.config import MetaOpsConfig

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def main():
    config = MetaOpsConfig()
    runner = create_runner()
    session_manager = SessionManager()

    await session_service.create_session(
        app_name="metaops_enterprise",
        user_id="local_cli_user",
        session_id="metaops_session_local_cli_user",
        state={"user:role": "admin", "user:name": "local_user"},
    )
    await session_service.create_session(
        app_name="metaops_enterprise",
        user_id="system_cron",
        session_id="cron_nightly_audit",
        state={"user:role": "admin", "user:name": "cron"},
    )

    async def deliver_to_platform(session_id: str, message: str):
        print(f"\n\033[94m[SYSTEM DELIVERY -> {session_id}]\033[0m\n{message}\n")

    cron_scheduler = MetaOpsCronScheduler(runner=runner, delivery_callback=deliver_to_platform)
    cron_scheduler.add_job(job_id="nightly_audit", cron_expression="0 2 * * *", prompt="Run a disk usage audit.", session_id="cron_nightly_audit")
    cron_scheduler.start()

    cli_bridge = CLIBridge(runner=runner, session_manager=session_manager)

    telegram_bridge = None
    if config.telegram_bot_token:
        telegram_bridge = TelegramBridge(runner=runner, session_manager=session_manager, token=config.telegram_bot_token, session_service=session_service)
        await telegram_bridge.start()

    try:
        await cli_bridge.start()
    finally:
        cron_scheduler.shutdown()
        if telegram_bridge:
            await telegram_bridge.stop()

def run():
    asyncio.run(main())

if __name__ == "__main__":
    run()
