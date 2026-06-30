import asyncio
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from google.adk.events import Event
from google.adk.runners import Runner
from google.genai import types
from metaops.gateway.base import PlatformBridge
from metaops.gateway.session_manager import SessionManager

class CLIBridge(PlatformBridge):
    def __init__(self, runner: Runner, session_manager: SessionManager):
        self.runner = runner
        self.session_manager = session_manager
        self.session = PromptSession(history=InMemoryHistory())
        self.user_id = "local_cli_user"

    async def start(self):
        print("MetaOps CLI Gateway Initialized. Type '/exit' to quit.")
        session_id = self.session_manager.get_session_id("cli", self.user_id)
        while True:
            try:
                user_input = await self.session.prompt_async("MetaOps> ", multiline=False)
                if user_input.strip().lower() in ["/exit", "/quit"]: break
                if not user_input.strip(): continue
                
                content = types.Content(role='user', parts=[types.Part(text=user_input)])
                async for event in self.runner.run_async(user_id=self.user_id, session_id=session_id, new_message=content):
                    if event.is_final_response() and event.content and event.content.parts:
                        text = event.content.parts[0].text
                        if text: print(f"\n\033[92mMetaOps:\033[0m {text}\n", flush=True)
            except KeyboardInterrupt: continue
            except EOFError: break

    async def send_event(self, event: Event): pass
