"""Shared lightweight agent runner for workflow tools.

Provides run_agent_once() — a minimal helper that creates a cached
Runner per agent, sends a single prompt, and collects the text output.
Used by vibe_coding, dev_cycle, and research instead of duplicating
the same boilerplate.
"""

import uuid
import asyncio
import logging
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.genai import types

from metaops.core.continuation import (
    CONTINUE_PROMPT,
    filter_thought_parts,
    is_truncated,
    has_budget_exhausted,
    _get_max_continuations,
)

logger = logging.getLogger(__name__)

from metaops.config import get_config

# Cache runners per agent to avoid recreating them on every call.
# Each call still gets a fresh session (stateless).
_runner_cache: dict[str, Runner] = {}


def _get_runner(agent: Agent) -> Runner:
    """Return a cached Runner for the given agent, creating one if needed."""
    if agent.name not in _runner_cache:
        from metaops.core.callbacks import MetaOpsPlugin

        _runner_cache[agent.name] = Runner(
            agent=agent,
            app_name=f"metaops_{agent.name}",
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            plugins=[MetaOpsPlugin()],
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

    If the primary model fails (due to timeout, connection issue, or other LLM errors),
    it dynamically falls back to the models configured in METAOPS_FALLBACK_PROVIDERS.

    Args:
        agent: The ADK Agent to run.
        prompt: The user message to send.

    Returns:
        Concatenated text output from the agent (empty string if no output).
    """
    original_model = agent.model
    config = get_config()
    fallbacks = config.get_fallback_configs()
    models_to_try = [None] + fallbacks

    last_exc = None

    for idx, fallback in enumerate(models_to_try):
        if fallback is not None:
            logger.warning(
                "⚠️ Agent '%s' primary model failed. Attempting fallback %d/%d: model '%s' (provider: %s)...",
                agent.name, idx, len(fallbacks), fallback.model, fallback.provider
            )
            # Temporarily switch the agent's model
            agent.model = fallback.to_model()
            # Clear cache so we compile a fresh Runner with the fallback model
            if agent.name in _runner_cache:
                del _runner_cache[agent.name]

        try:
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
            max_cont = _get_max_continuations()
            while truncated and not has_budget_exhausted(last_error_code) and continuations < max_cont:
                continuations += 1
                logger.info(
                    "Agent '%s' output truncated — requesting continuation (%d/%d)",
                    agent.name, continuations, max_cont,
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

            # Execution succeeded! Restore the original model config before returning.
            if fallback is not None:
                agent.model = original_model
                if agent.name in _runner_cache:
                    del _runner_cache[agent.name]

            return output

        except Exception as e:
            last_exc = e
            logger.error("Error executing agent '%s' on model %s: %s", agent.name, getattr(agent.model, 'model', agent.model), e)
            continue

    # All attempts failed: restore original model config and raise last exception
    agent.model = original_model
    if agent.name in _runner_cache:
        del _runner_cache[agent.name]
    raise last_exc
