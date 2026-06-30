import asyncio
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from google.adk.events import Event
from google.adk.runners import Runner
from google.genai import types
from metaops.gateway.base import BaseGateway
from metaops.gateway.session_manager import SessionManager

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
                
                content = types.Content(role='user', parts=[types.Part(text=user_input)])
                async for event in self.runner.run_async(user_id=self.user_id, session_id=session_id, new_message=content):
                    if event.is_final_response() and event.content and event.content.parts:
                        text = "".join([
                            part.text for part in event.content.parts 
                            if part.text and not getattr(part, "thought", False)
                        ])
                        if text: print(f"\n\033[92mMetaOps:\033[0m {text}\n", flush=True)
            except KeyboardInterrupt: continue
            except EOFError: break

    async def stop(self): pass

    async def send_event(self, event: Event): pass

