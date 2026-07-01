import asyncio
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig
from google.genai import types
from metaops.gateway.base import BaseGateway
from metaops.gateway.session_manager import SessionManager
from metaops.core.continuation import (
    MAX_CONTINUATIONS,
    CONTINUE_PROMPT,
    filter_thought_parts,
    is_truncated,
    has_budget_exhausted,
)

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
        while True:
            try:
                if self._is_interactive:
                    user_input = await self.session.prompt_async("MetaOps> ", multiline=False)
                else:
                    loop = asyncio.get_running_loop()
                    user_input = await loop.run_in_executor(None, input, "MetaOps> ")

                if user_input.strip().lower() in ["/exit", "/quit"]: break
                if not user_input.strip(): continue

                parts: list[str] = []
                last_error_code = None

                async def _run_turn(message_text: str) -> bool:
                    """Runs one turn. Returns True if truncated mid-answer."""
                    nonlocal last_error_code
                    turn_truncated = False
                    async for event in self.runner.run_async(
                        user_id=self.user_id,
                        session_id=session_id,
                        new_message=types.Content(parts=[types.Part(text=message_text)]),
                        run_config=_DEFAULT_RUN_CONFIG,
                    ):
                        if event.error_code:
                            last_error_code = event.error_code
                        if is_truncated(event):
                            turn_truncated = True
                        if event.content:
                            parts.extend(filter_thought_parts(event.content.parts))
                    return turn_truncated

                truncated = await _run_turn(user_input)

                continuations = 0
                while truncated and not has_budget_exhausted(last_error_code) and continuations < MAX_CONTINUATIONS:
                    continuations += 1
                    print(f"\n\033[93m[Response truncated — continuing...]\033[0m", flush=True)
                    last_error_code = None
                    truncated = await _run_turn(CONTINUE_PROMPT)

                text = "\n".join(parts)
                if text:
                    print(f"\n\033[92mMetaOps:\033[0m {text}\n", flush=True)
                elif has_budget_exhausted(last_error_code):
                    print("\n\033[91mResponse consumed by internal reasoning. Please retry.\033[0m\n", flush=True)

            except KeyboardInterrupt: continue
            except EOFError: break

    async def stop(self): pass

    async def send_event(self, event: Event): pass

