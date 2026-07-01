import asyncio
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

# Safety limit: prevent infinite LLM loops per user turn
_DEFAULT_RUN_CONFIG = RunConfig(max_llm_calls=50)

class CLIBridge(BaseGateway):
    def __init__(self, runner: Runner, session_manager: SessionManager):
        self.runner = runner
        self.session_manager = session_manager
        self._is_interactive = True
        try:
            self.session = PromptSession(history=InMemoryHistory())
        except Exception:
            self._is_interactive = False
        self.user_id = "local_cli_user"

    async def start(self):
        print("MetaOps CLI Gateway Initialized. Type '/exit' to quit.")
        session_id = self.session_manager.get_session_id("cli", self.user_id)
        checkpoint = SessionCheckpoint(f"cli:{self.user_id}")
        while True:
            try:
                if self._is_interactive:
                    user_input = await self.session.prompt_async("MetaOps> ", multiline=False)
                else:
                    loop = asyncio.get_running_loop()
                    user_input = await loop.run_in_executor(None, input, "MetaOps> ")

                if user_input.strip().lower() in ["/exit", "/quit"]: break
                if not user_input.strip(): continue

                text, error_code = await run_turn_with_continuation(
                    runner=self.runner,
                    user_id=self.user_id,
                    session_id=session_id,
                    message_text=user_input,
                    run_config=_DEFAULT_RUN_CONFIG,
                )
                checkpoint.save({"last_user_input": user_input, "last_response": text[:2000]})
                if text:
                    print(f"\n\033[92mMetaOps:\033[0m {text}\n", flush=True)
                elif has_budget_exhausted(error_code):
                    print("\n\033[91mResponse consumed by internal reasoning. Please retry.\033[0m\n", flush=True)

            except KeyboardInterrupt: continue
            except EOFError: break

    async def stop(self): pass

    async def send_event(self, event: Event): pass

