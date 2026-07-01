import argparse
import asyncio
import logging
import sys

logger = logging.getLogger(__name__)

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("metaops")
except Exception:
    __version__ = "3.0.0"


def _is_port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


def start_portkey_if_needed():
    import shutil
    import subprocess
    import os
    
    portkey_url = os.getenv("PORTKEY_GATEWAY_URL")
    if not portkey_url:
        return None
        
    port = 8787
    if ":" in portkey_url:
        try:
            parts = portkey_url.split(":")
            if len(parts) >= 3:
                port = int(parts[2].split("/")[0])
            elif len(parts) == 2:
                port = int(parts[1].split("/")[0])
        except Exception:
            pass

    if _is_port_in_use(port):
        logger.info("Portkey gateway already running on port %d", port)
        return None
        
    npx = shutil.which("npx")
    if not npx:
        logger.warning("npx not found — cannot start Portkey gateway automatically")
        return None
        
    logger.info("Starting Portkey gateway on port %d in the background...", port)
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["shell"] = True
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            
        proc = subprocess.Popen(
            [npx, "@portkey-ai/gateway", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            **kwargs
        )
        return proc
    except Exception as e:
        logger.error("Failed to start Portkey gateway: %s", e)
        return None


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
  metaops db reset          reset sessions database (fixes corrupted tool_calls)
  metaops db reset all      reset all databases
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

    # db command
    db_parser = subparsers.add_parser("db", help="Database management")
    db_sub = db_parser.add_subparsers(dest="db_action", help="Database actions")
    db_reset = db_sub.add_parser("reset", help="Reset database(s)")
    db_reset.add_argument(
        "target",
        nargs="?",
        choices=["sessions", "skills", "vector", "artifacts", "all"],
        default="sessions",
        help="Which database to reset (default: sessions)",
    )
    db_reset.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )

    return parser


async def _get_or_create_session(session_service, *, app_name, user_id, session_id, state):
    """Like create_session, but idempotent across process restarts.

    ADK's SqliteSessionService raises AlreadyExistsError for a session_id
    that's already on disk (unlike the old hand-rolled INSERT OR IGNORE
    service), and these two callers use fixed, well-known session ids that
    are (re)created on every startup.
    """
    from google.adk.errors.already_exists_error import AlreadyExistsError

    try:
        await session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id, state=state,
        )
    except AlreadyExistsError:
        pass


async def main(args: argparse.Namespace):
    from metaops.core.root import create_runner, session_service
    from metaops.gateway.session_manager import SessionManager
    from metaops.gateway.cli import CLIBridge
    from metaops.gateway.telegram import TelegramBridge
    from metaops.scheduler.cron import MetaOpsCronScheduler
    from metaops.config import get_config
    from metaops.gateway.registry import GatewayRegistry
    from metaops.gateway.delivery import DeliveryService

    config = get_config()
    portkey_proc = start_portkey_if_needed()

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
    registry = GatewayRegistry()
    delivery_service = DeliveryService(registry, telegram_token=config.telegram_bot_token)

    await _get_or_create_session(
        session_service,
        app_name="metaops_enterprise",
        user_id="local_cli_user",
        session_id="metaops_session_local_cli_user",
        state={"user:role": config.default_cli_role, "user:name": "local_user"},
    )
    await _get_or_create_session(
        session_service,
        app_name="metaops_enterprise",
        user_id="system_cron",
        session_id="cron_nightly_audit",
        state={"user:role": config.default_cron_role, "user:name": "cron"},
    )

    async def deliver_to_platform(session_id: str, message: str):
        # Route cron outputs dynamically using DeliveryService
        await delivery_service.deliver(config.cron_delivery_target, message)

    # Cron scheduler
    cron_scheduler = None
    if not args.no_cron:
        cron_scheduler = MetaOpsCronScheduler(runner=runner, delivery_callback=deliver_to_platform)
        cron_scheduler.add_job(
            job_id="nightly_audit",
            cron_expression="0 2 * * *",
            prompt="Run a full security and code quality audit of this project.",
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
                default_role=config.default_telegram_role,
                allowed_user_ids=config.telegram_allowed_user_ids,
            )
            registry.register("telegram", telegram_bridge)
            registry.set_active("telegram", True)
            await telegram_bridge.start()
            print("MetaOps Telegram gateway running. Press Ctrl+C to stop.")
            await asyncio.Event().wait()
        else:
            # Pass runner=None for lazy init — the CLI creates it on first
            # message so the prompt appears instantly instead of waiting for
            # ChromaDB + MCP servers + model loading at startup.
            cli_bridge = CLIBridge(runner=None, session_manager=session_manager)
            registry.register("cli", cli_bridge)
            registry.set_active("cli", True)
            await cli_bridge.start()
            # Runner may have been created lazily — grab it for cleanup
            runner = cli_bridge._runner or runner
    finally:
        registry.set_active("telegram", False)
        registry.set_active("cli", False)
        if cron_scheduler:
            cron_scheduler.shutdown()
        if telegram_bridge:
            await telegram_bridge.stop()
        await _close_mcp_toolsets(runner)
        if portkey_proc:
            logger.info("Stopping Portkey gateway...")
            portkey_proc.terminate()
            import subprocess
            try:
                portkey_proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                portkey_proc.kill()


async def _close_mcp_toolsets(runner) -> None:
    """Shut down MCP server sessions/subprocesses started by create_runner().

    Without this, the stdio MCP servers (subprocesses) loaded once at
    startup are never explicitly stopped and rely entirely on the OS
    reaping them when the parent process exits.
    """
    if runner is None:
        return
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

    for tool in runner.agent.tools:
        if isinstance(tool, McpToolset):
            try:
                await tool.close()
            except Exception as exc:
                logger.warning("Failed to close MCP toolset cleanly: %s", exc)


def _db_reset(args: argparse.Namespace):
    """Reset database(s). Synchronous — runs outside asyncio."""
    from metaops.config import get_config
    import os
    import shutil
    from pathlib import Path

    config = get_config()
    target = args.target or "sessions"

    # Resolve artifacts path relative to project root
    from pathlib import Path as _Path
    _proj = _Path(__file__).resolve().parent.parent.parent
    _artifacts_path = os.getenv("METAOPS_ARTIFACTS_DIR", str(_proj / ".data" / "artifacts"))
    _artifacts_p = _Path(_artifacts_path)
    if not _artifacts_p.is_absolute():
        _artifacts_p = _proj / _artifacts_p

    targets = {
        "sessions": ("SQLite sessions", config.sessions_db),
        "skills": ("SQLite skills", config.skills_db),
        "vector": ("ChromaDB vector", config.vector_db),
        "artifacts": ("File artifacts", str(_artifacts_p)),
    }

    if target == "all":
        to_reset = list(targets.keys())
    else:
        to_reset = [target]

    # Confirmation
    if not args.yes:
        labels = [f"  - {targets[t][0]} ({targets[t][1]})" for t in to_reset]
        print(f"Resetting:\n" + "\n".join(labels))
        answer = input("Continue? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    for t in to_reset:
        label, path = targets[t]
        p = Path(path)
        if t in ("vector", "artifacts"):
            # Directory: remove entire tree
            if p.exists():
                shutil.rmtree(p)
                print(f"  [OK] {label}: removed {path}")
            else:
                print(f"  [--] {label}: not found, skipping")
        else:
            # SQLite file
            if p.exists():
                os.remove(p)
                print(f"  [OK] {label}: removed {path}")
            else:
                print(f"  [--] {label}: not found, skipping")

    print("Done. Databases will be recreated on next startup.")


def run():
    parser = _build_parser()
    args = parser.parse_args()

    # Handle db command synchronously (no asyncio needed)
    if args.command == "db" and getattr(args, "db_action", None) == "reset":
        _db_reset(args)
        return

    try:
        asyncio.run(main(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[MetaOps] Service stopped.")


if __name__ == "__main__":
    run()
