"""Shared lightweight agent runner for workflow tools.

Provides run_agent_once() — a minimal helper that creates a throwaway
Runner + InMemorySession, sends a single prompt, and collects the text
output.  Used by vibe_coding, dev_cycle, and research instead of
duplicating the same boilerplate.
"""

import uuid
import logging
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.genai import types

logger = logging.getLogger(__name__)


async def run_agent_once(agent: Agent, prompt: str) -> str:
    """Run an agent with a single prompt and return its text output.

    Creates an ephemeral session (no persistence) so each call is
    stateless.  Filters out thought/reasoning parts automatically.

    Args:
        agent: The ADK Agent to run.
        prompt: The user message to send.

    Returns:
        Concatenated text output from the agent (empty string if no output).
    """
    runner = Runner(
        agent=agent,
        app_name=f"metaops_{agent.name}",
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )
    session = await runner.session_service.create_session(
        app_name=f"metaops_{agent.name}",
        user_id=agent.name,
        session_id=str(uuid.uuid4()),
    )

    parts: list[str] = []
    async for event in runner.run_async(
        user_id=agent.name,
        session_id=session.id,
        new_message=types.Content(parts=[types.Part(text=prompt)]),
    ):
        if event.content:
            for part in event.content.parts or []:
                # Skip thinking/reasoning parts
                if getattr(part, "thought", False):
                    continue
                if part.text:
                    parts.append(part.text)

    return "\n".join(parts)
