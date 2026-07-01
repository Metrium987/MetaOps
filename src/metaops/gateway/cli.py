import asyncio
import sys
import time
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig
from metaops.gateway.base import BaseGateway
from metaops.gateway.session_manager import SessionManager
from metaops.core.continuation import (
    run_turn_with_continuation,
    has_budget_exhausted,
)
from metaops.core.session_checkpoint import SessionCheckpoint
from metaops.config import get_config


def _make_run_config() -> RunConfig:
    return RunConfig(max_llm_calls=get_config().gateway_max_llm_calls)


# Spinner frames for loading indicator
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_SPINNER_INTERVAL = 0.08


class CLIBridge(BaseGateway):
    def __init__(self, runner_or_none, session_manager: SessionManager):
        """Accept either a ready Runner or None for lazy initialization."""
        self._runner = runner_or_none
        self._runner_lock = asyncio.Lock()
        self.session_manager = session_manager
        self._is_interactive = True
        try:
            self.session = PromptSession(history=InMemoryHistory())
        except Exception:
            self._is_interactive = False
        self.user_id = "local_cli_user"

    async def _get_runner(self) -> Runner:
        """Return the runner, initializing lazily on first call."""
        if self._runner is not None:
            return self._runner
        async with self._runner_lock:
            if self._runner is not None:
                return self._runner
            return await self._init_runner()

    async def _init_runner(self) -> Runner:
        """Initialize the runner with a progress spinner."""
        spinner_task = None
        try:
            if self._is_interactive:
                spinner_task = asyncio.create_task(self._run_spinner("Loading agent"))
                t0 = time.monotonic()
                from metaops.core.root import create_runner
                runner = create_runner()
                elapsed = time.monotonic() - t0
                if spinner_task:
                    spinner_task.cancel()
                    try:
                        await spinner_task
                    except asyncio.CancelledError:
                        pass
                sys.stdout.write(f"\r\033[K")
                sys.stdout.flush()
                print(f"\033[92mAgent loaded\033[0m in {elapsed:.1f}s")
            else:
                from metaops.core.root import create_runner
                runner = create_runner()
            self._runner = runner
            return runner
        except Exception as exc:
            if spinner_task:
                spinner_task.cancel()
                try:
                    await spinner_task
                except asyncio.CancelledError:
                    pass
            sys.stdout.write(f"\r\033[K")
            sys.stdout.flush()
            print(f"\033[91mFailed to load agent: {exc}\033[0m")
            raise

    async def _run_spinner(self, message: str):
        """Run an animated spinner until cancelled."""
        idx = 0
        try:
            while True:
                frame = _SPINNER_FRAMES[idx % len(_SPINNER_FRAMES)]
                sys.stdout.write(f"\r{frame} {message}...")
                sys.stdout.flush()
                idx += 1
                await asyncio.sleep(_SPINNER_INTERVAL)
        except asyncio.CancelledError:
            sys.stdout.write(f"\r\033[K")
            sys.stdout.flush()

    async def start(self):
        print("MetaOps CLI. Type '/exit' to quit.")
        session_id = self.session_manager.get_session_id("cli", self.user_id)
        checkpoint = SessionCheckpoint(f"cli:{self.user_id}")
        try:
            while True:
                try:
                    if self._is_interactive:
                        user_input = await self.session.prompt_async("MetaOps> ", multiline=False)
                    else:
                        loop = asyncio.get_running_loop()
                        user_input = await loop.run_in_executor(None, input, "MetaOps> ")

                    if user_input.strip().lower() in ["/exit", "/quit"]:
                        print("Goodbye.")
                        return
                    if not user_input.strip():
                        continue

                    # Lazy init on first message
                    runner = await self._get_runner()

                    # Run LLM in background so prompt_toolkit stays responsive
                    result = [None, None]
                    done_event = asyncio.Event()

                    async def _process():
                        result[0], result[1] = await run_turn_with_continuation(
                            runner=runner,
                            user_id=self.user_id,
                            session_id=session_id,
                            message_text=user_input,
                            run_config=_make_run_config(),
                        )
                        done_event.set()

                    process_task = asyncio.create_task(_process())
                    spinner_task = asyncio.create_task(self._run_spinner("Thinking"))

                    await done_event.wait()

                    spinner_task.cancel()
                    try:
                        await spinner_task
                    except asyncio.CancelledError:
                        pass
                    sys.stdout.write(f"\r\033[K")
                    sys.stdout.flush()

                    text, error_code = result
                    checkpoint.save({"last_user_input": user_input, "last_response": (text or "")[:2000]})
                    if text:
                        print(f"\n\033[92mMetaOps:\033[0m {text}\n", flush=True)
                    elif has_budget_exhausted(error_code):
                        print("\n\033[91mResponse consumed by internal reasoning. Please retry.\033[0m\n", flush=True)

                except KeyboardInterrupt:
                    print("\nGoodbye.")
                    return
                except EOFError:
                    print("\nGoodbye.")
                    return
        except Exception as exc:
            print(f"\n[MetaOps] CLI error: {exc}")

    async def stop(self):
        pass

    async def send_event(self, event: Event):
        pass
