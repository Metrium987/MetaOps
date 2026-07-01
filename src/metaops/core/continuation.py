"""Shared continuation and thinking-filter utilities for gateways.

Extracted from agent_runner.py to be reusable across telegram, cli, and
other gateway adapters.  Handles:
  - Detecting truncated responses (metaops_truncated flag)
  - Requesting model continuation up to MAX_CONTINUATIONS times
  - Filtering out thinking/reasoning parts from LLM output
"""

import logging
from typing import AsyncIterator

from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig
from google.genai import types

from metaops.core.reasoning_guard import REASONING_BUDGET_EXHAUSTED

logger = logging.getLogger(__name__)

# Mirrors the continuation-retry cap used by reference agent runtimes (e.g.
# Hermes) for responses truncated mid-answer by the max_tokens budget.
MAX_CONTINUATIONS = 3

CONTINUE_PROMPT = (
    "Your previous response was cut off by the output token limit. "
    "Continue exactly where you left off — do not repeat anything already "
    "written, do not restart the explanation or the code block."
)


def filter_thought_parts(parts) -> list[str]:
    """Extract visible text from LLM content parts, skipping thought/reasoning."""
    result = []
    for part in parts or []:
        if getattr(part, "thought", False):
            continue
        if part.text:
            result.append(part.text)
    return result


def is_truncated(event) -> bool:
    """Check if an ADK event flags a truncated response."""
    if event.custom_metadata and event.custom_metadata.get("metaops_truncated"):
        return True
    return False


def has_budget_exhausted(error_code: str | None) -> bool:
    """Check if the error code indicates reasoning budget exhaustion."""
    return error_code == REASONING_BUDGET_EXHAUSTED


async def run_turn_with_continuation(
    runner: Runner,
    user_id: str,
    session_id: str,
    message_text: str,
    run_config: RunConfig | None = None,
) -> tuple[str, str | None]:
    """Run a full turn with automatic continuation on truncation.

    Encapsulates the common pattern shared by telegram, cli, and drain_pending:
    run the initial turn, detect truncation, request continuations, filter
    thought parts, and return the assembled text.

    Returns:
        (text, error_code) — the assembled visible text and the last error
        code (or None).  If the budget was exhausted with no visible output,
        text is empty and error_code is REASONING_BUDGET_EXHAUSTED.
    """
    parts: list[str] = []
    last_error_code: str | None = None

    async def _run_one(message: str) -> bool:
        nonlocal last_error_code
        truncated = False
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(parts=[types.Part(text=message)]),
            run_config=run_config,
        ):
            if event.error_code:
                last_error_code = event.error_code
            if is_truncated(event):
                truncated = True
            if event.content:
                parts.extend(filter_thought_parts(event.content.parts))
        return truncated

    truncated = await _run_one(message_text)
    continuations = 0
    while truncated and not has_budget_exhausted(last_error_code) and continuations < MAX_CONTINUATIONS:
        continuations += 1
        logger.info("Output truncated — continuation (%d/%d)", continuations, MAX_CONTINUATIONS)
        last_error_code = None
        truncated = await _run_one(CONTINUE_PROMPT)

    return "\n".join(parts), last_error_code
