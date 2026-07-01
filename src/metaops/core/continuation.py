"""Shared continuation and thinking-filter utilities for gateways.

Extracted from agent_runner.py to be reusable across telegram, cli, and
other gateway adapters.  Handles:
  - Detecting truncated responses (metaops_truncated flag)
  - Requesting model continuation up to MAX_CONTINUATIONS times
  - Filtering out thinking/reasoning parts from LLM output
"""

import logging
from typing import Iterator

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
