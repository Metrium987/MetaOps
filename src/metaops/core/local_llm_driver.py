import logging
import uuid

from google.adk.labs.openai import OpenAILlm

logger = logging.getLogger("metaops.core.local_llm_driver")


class LocalOpenAILlm(OpenAILlm):
    """OpenAILlm hardened for local/self-hosted backends (Ollama, LM Studio).

    Local OpenAI-compatible servers don't always populate tool_call.id or
    tool_call.function.name the way OpenAI does — seen with small/quantized
    models and truncated streamed tool calls. ADK matches a function_call to
    its later function_response by id, so a missing one silently breaks the
    tool-call turn. Patch responses after they come back from the unmodified
    upstream driver rather than re-implementing its parsing.
    """

    async def generate_content_async(self, llm_request, stream: bool = False):
        declared_names = _declared_tool_names(llm_request)
        async for response in super().generate_content_async(llm_request, stream=stream):
            _harden_function_calls(response, declared_names)
            yield response


def _declared_tool_names(llm_request) -> list[str]:
    config = llm_request.config
    if not config or not config.tools or not config.tools[0].function_declarations:
        return []
    return [fd.name for fd in config.tools[0].function_declarations if fd.name]


def _harden_function_calls(response, declared_names: list[str]) -> None:
    if not response.content or not response.content.parts:
        return
    for part in response.content.parts:
        fc = part.function_call
        if fc is None:
            continue
        if not fc.id:
            fc.id = f"call_{uuid.uuid4().hex[:24]}"
            logger.warning("Tool call missing id from backend, generated %s", fc.id)
        if not fc.name:
            if len(declared_names) == 1:
                fc.name = declared_names[0]
                logger.warning(
                    "Tool call missing name from backend, defaulted to only declared tool %s",
                    fc.name,
                )
            else:
                logger.warning(
                    "Tool call missing name from backend and %d tools declared, cannot recover",
                    len(declared_names),
                )
