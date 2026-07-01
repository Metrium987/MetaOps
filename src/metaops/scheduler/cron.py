from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig
from google.genai import types
from typing import Callable, Awaitable
import logging

from metaops.config import get_config

logger = logging.getLogger(__name__)


def _make_cron_run_config() -> RunConfig:
    return RunConfig(max_llm_calls=get_config().cron_max_llm_calls)


class MetaOpsCronScheduler:
    def __init__(self, runner: Runner, delivery_callback: Callable[[str, str], Awaitable[None]]):
        self.scheduler = AsyncIOScheduler()
        self.runner = runner
        self.delivery_callback = delivery_callback

    def add_job(self, job_id: str, cron_expression: str, prompt: str, session_id: str):
        trigger = CronTrigger.from_crontab(cron_expression)
        self.scheduler.add_job(self._execute_unattended, trigger=trigger, args=[prompt, session_id], id=job_id, replace_existing=True)

    async def _execute_unattended(self, prompt: str, session_id: str):
        content = types.Content(role='user', parts=[types.Part(text=prompt)])
        try:
            final_output = []
            async for event in self.runner.run_async(user_id="system_cron", session_id=session_id, new_message=content, run_config=_make_cron_run_config()):
                if event.is_final_response() and event.content and event.content.parts:
                    text = "".join([
                        part.text for part in event.content.parts
                        if part.text and not getattr(part, "thought", False)
                    ])
                    if text: final_output.append(text)
            if final_output: await self.delivery_callback(session_id, "\n".join(final_output))
        except Exception as e:
            await self.delivery_callback(session_id, f"Error: {str(e)}")

    def start(self): self.scheduler.start()
    def shutdown(self): self.scheduler.shutdown()
