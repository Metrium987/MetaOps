"""Shared lightweight agent runner for workflow tools.

Provides run_agent_once() — a minimal helper that creates a cached
Runner per agent, sends a single prompt, and collects the text output.
Used by vibe_coding, dev_cycle, and research instead of duplicating
the same boilerplate.
"""

import uuid
import logging
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.genai import types

from metaops.core.continuation import (
    MAX_CONTINUATIONS,
    CONTINUE_PROMPT,
    filter_thought_parts,
    is_truncated,
    has_budget_exhausted,
)

logger = logging.getLogger(__name__)

# Cache runners per agent to avoid recreating them on every call.
# Each call still gets a fresh session (stateless).
_runner_cache: dict[str, Runner] = {}


def _get_runner(agent: Agent) -> Runner:
    """Return a cached Runner for the given agent, creating one if needed."""
    if agent.name not in _runner_cache:
        _runner_cache[agent.name] = Runner(
            agent=agent,
            app_name=f"metaops_{agent.name}",
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
        )
    return _runner_cache[agent.name]


async def run_agent_once(agent: Agent, prompt: str) -> str:
    """Run an agent with a single prompt and return its text output.

    Creates an ephemeral session (no persistence) so each call is
    stateless.  Filters out thought/reasoning parts automatically.

    If a response is truncated mid-answer by the max_tokens budget (flagged
    by ReasoningGuardedOpenAILlm via custom_metadata), automatically asks the
    model to continue in the same session, up to MAX_CONTINUATIONS times, and
    concatenates the parts. If a response instead exhausts the entire budget
    on internal reasoning with zero visible output (REASONING_BUDGET_EXHAUSTED),
    retrying is pointless — that's reported immediately as a clear error.

    Args:
        agent: The ADK Agent to run.
        prompt: The user message to send.

    Returns:
        Concatenated text output from the agent (empty string if no output).
    """
    runner = _get_runner(agent)
    session = await runner.session_service.create_session(
        app_name=f"metaops_{agent.name}",
        user_id=agent.name,
        session_id=str(uuid.uuid4()),
    )

    parts: list[str] = []
    last_error_code = None
    last_error_message = None

    async def _run_turn(message_text: str) -> bool:
        nonlocal last_error_code, last_error_message
        turn_truncated = False
        async for event in runner.run_async(
            user_id=agent.name,
            session_id=session.id,
            new_message=types.Content(parts=[types.Part(text=message_text)]),
        ):
            if event.error_code or event.error_message:
                last_error_code = event.error_code
                last_error_message = event.error_message
                logger.warning(
                    "Agent '%s' event reported an error: code=%s message=%s",
                    agent.name, last_error_code, last_error_message,
                )
            if is_truncated(event):
                turn_truncated = True
            if event.content:
                parts.extend(filter_thought_parts(event.content.parts))
        return turn_truncated

    truncated = await _run_turn(prompt)

    continuations = 0
    while truncated and not has_budget_exhausted(last_error_code) and continuations < MAX_CONTINUATIONS:
        continuations += 1
        logger.info(
            "Agent '%s' output truncated — requesting continuation (%d/%d)",
            agent.name, continuations, MAX_CONTINUATIONS,
        )
        last_error_code = None
        last_error_message = None
        truncated = await _run_turn(CONTINUE_PROMPT)

    output = "\n".join(parts)

    if has_budget_exhausted(last_error_code) and not output:
        raise RuntimeError(f"Agent '{agent.name}': {last_error_message}")

    if not output:
        raise RuntimeError(
            f"Agent '{agent.name}' returned no text output "
            f"(error_code={last_error_code}, error_message={last_error_message})"
        )
    return output
