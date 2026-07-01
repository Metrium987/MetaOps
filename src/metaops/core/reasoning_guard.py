"""Hardens OpenAI-compatible drivers against silent reasoning-budget exhaustion.

Confirmed on 2026-06-30 against two free/training-tier providers (KiloCode's
stepfun/step-3.7-flash:free and OpenCode Zen's deepseek-v4-flash-free): given
the coder_agent system prompt, both models sometimes spend their entire
`max_tokens` completion budget on internal chain-of-thought and never emit
visible content (`usage.completion_tokens == max_tokens`, `message.content`
empty). The OpenAI SDK response carries this signal, but ADK's OpenAILlm
driver discards it — `_response_to_llm_response` only reads `message.content`
and `message.tool_calls`. This wrapper inspects the same usage_metadata signal
after the upstream driver parses it and reports it through ADK's own
error/metadata channels (LlmResponse.error_code, LlmResponse.custom_metadata)
instead of silently returning empty content.
"""

import logging

from google.adk.labs.openai import OpenAILlm

logger = logging.getLogger("metaops.core.reasoning_guard")

# Tolerance for "hit the budget" — providers occasionally report a couple of
# tokens under the requested max_tokens even when genuinely truncated.
_BUDGET_FRACTION = 0.98

REASONING_BUDGET_EXHAUSTED = "REASONING_BUDGET_EXHAUSTED"


class ReasoningGuardedOpenAILlm(OpenAILlm):
    """OpenAILlm that flags responses truncated by the max_tokens budget.

    Two distinct shapes, both detected via usage_metadata vs max_tokens:
      - Exhausted: the full budget was spent with zero visible output (the
        model never got past internal reasoning). Reported as an error on
        the response so callers see a clear, specific message instead of an
        opaque empty completion.
      - Truncated: some visible content was produced but the budget ran out
        mid-answer. Flagged via custom_metadata so a caller (agent_runner)
        can request a continuation instead of treating it as final.
    """

    async def generate_content_async(self, llm_request, stream: bool = False):
        async for response in super().generate_content_async(llm_request, stream=stream):
            _flag_budget_truncation(response, self.max_tokens)
            yield response


def _has_visible_output(response) -> bool:
    if not response.content or not response.content.parts:
        return False
    for part in response.content.parts:
        if (part.text and part.text.strip()) or part.function_call:
            return True
    return False


def _flag_budget_truncation(response, max_tokens: int) -> None:
    usage = response.usage_metadata
    if not usage or not usage.candidates_token_count or not max_tokens:
        return
    if usage.candidates_token_count < max_tokens * _BUDGET_FRACTION:
        return  # well under budget — a normal, complete response

    if _has_visible_output(response):
        logger.warning(
            "Response hit the max_tokens budget (%d/%d) with visible output "
            "already present — likely truncated mid-answer.",
            usage.candidates_token_count, max_tokens,
        )
        response.custom_metadata = {
            **(response.custom_metadata or {}),
            "metaops_truncated": True,
        }
    else:
        logger.warning(
            "Response consumed the entire max_tokens budget (%d) with no "
            "visible output — likely exhausted on internal reasoning.",
            max_tokens,
        )
        response.error_code = response.error_code or REASONING_BUDGET_EXHAUSTED
        response.error_message = response.error_message or (
            f"Model consumed its full max_tokens budget ({max_tokens}) on "
            "internal reasoning and produced no visible response. Raise "
            "max_tokens further, lower the model's reasoning effort, or "
            "switch to a non-reasoning model."
        )
