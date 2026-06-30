import argparse
import asyncio
import logging
import sys

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("metaops")
except Exception:
    __version__ = "3.0.0"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metaops",
        description="MetaOps — enterprise-grade autonomous AI agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  metaops                   start interactive CLI console only (default)
  metaops gateway telegram  run Telegram bot gateway only
  metaops gateway cli       run interactive CLI console gateway only
  metaops --no-cron         skip the background scheduler
  metaops --debug           verbose logging
  metaops --version         print version and exit
        """,
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--telegram-only",
        action="store_true",
        help=argparse.SUPPRESS,  # Hidden legacy flag
    )
    parser.add_argument(
        "--no-cron",
        action="store_true",
        help="skip the background cron scheduler",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable DEBUG logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="sub-commands")

    # gateway command
    gateway_parser = subparsers.add_parser("gateway", help="Launch application gateways")
    gateway_parser.add_argument(
        "gateway_type",
        nargs="?",
        choices=["telegram", "cli"],
        default="cli",
        help="Gateway to start (default: cli)",
    )

    return parser


async def main(args: argparse.Namespace):
    from metaops.core.root import create_runner, session_service
    from metaops.gateway.session_manager import SessionManager
    from metaops.gateway.cli import CLIBridge
    from metaops.gateway.telegram import TelegramBridge
    from metaops.scheduler.cron import MetaOpsCronScheduler
    from metaops.config import MetaOpsConfig

    config = MetaOpsConfig()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if not args.debug:
        # Silence extremely noisy polling logs from third-party libraries
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("telegram").setLevel(logging.WARNING)
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
        logging.getLogger("chromadb").setLevel(logging.WARNING)

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

    # Cron scheduler
    cron_scheduler = None
    if not args.no_cron:
        cron_scheduler = MetaOpsCronScheduler(runner=runner, delivery_callback=deliver_to_platform)
        cron_scheduler.add_job(
            job_id="nightly_audit",
            cron_expression="0 2 * * *",
            prompt="Run a disk usage audit.",
            session_id="cron_nightly_audit",
        )
        cron_scheduler.start()

    # Choose between Telegram and CLI (mutually exclusive)
    run_telegram = args.telegram_only or (
        args.command == "gateway" and getattr(args, "gateway_type", None) == "telegram"
    )
    telegram_bridge = None
    try:
        if run_telegram:
            if not config.telegram_bot_token:
                print("Error: Telegram gateway requires TELEGRAM_BOT_TOKEN to be set in .env", file=sys.stderr)
                sys.exit(1)
            telegram_bridge = TelegramBridge(
                runner=runner,
                session_manager=session_manager,
                token=config.telegram_bot_token,
                session_service=session_service,
            )
            await telegram_bridge.start()
            print("MetaOps Telegram gateway running. Press Ctrl+C to stop.")
            await asyncio.Event().wait()
        else:
            cli_bridge = CLIBridge(runner=runner, session_manager=session_manager)
            await cli_bridge.start()
    finally:
        if cron_scheduler:
            cron_scheduler.shutdown()
        if telegram_bridge:
            await telegram_bridge.stop()


def run():
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(main(args))


if __name__ == "__main__":
    run()
