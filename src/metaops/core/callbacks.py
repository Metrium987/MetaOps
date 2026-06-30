import re
import asyncio
import logging
from typing import Any, Optional
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.tool_context import ToolContext
from google.adk.tools.base_tool import BaseTool
from metaops.memory.vector_service import HybridVectorMemoryService
from metaops.memory.database import MemoryDatabase

logger = logging.getLogger(__name__)

_memory_service: HybridVectorMemoryService = None
_skill_db = MemoryDatabase()

# Tools that require extra scrutiny before execution
_SENSITIVE_TOOLS = frozenset({
    "execute_secure_command",
    "execute_workstream_command",
    "run_audit",
    "bash",
})


def init_callbacks(memory_service: HybridVectorMemoryService):
    global _memory_service
    _memory_service = memory_service


# ── Before-agent ──────────────────────────────────────────────────────────────

async def auto_inject_memory_callback(callback_context: CallbackContext):
    """Load skill names into state once per session.

    Memory injection is handled natively by preload_memory which runs
    automatically on each LLM request.
    """
    if callback_context.state.get("skills_loaded"):
        return
    try:
        await _skill_db.initialize()
        skill_names = await _skill_db.list_skill_names()
        if skill_names:
            callback_context.state["available_skills"] = ", ".join(skill_names)
        callback_context.state["skills_loaded"] = True
    except Exception as exc:
        logger.warning("Could not load skills: %s", exc)


# ── After-agent ───────────────────────────────────────────────────────────────

async def skill_harvest_callback(callback_context: CallbackContext):
    """Parse [SKILL_CREATED] blocks from agent output and persist them."""
    agent_output = ""
    for event in reversed(callback_context.session.events):
        if (event.author == "metaops_coordinator"
                and event.content and event.content.parts):
            agent_output = event.content.parts[0].text or ""
            break

    if not agent_output:
        return

    match = re.search(
        r"\[SKILL_CREATED\]\s*Name:\s*(.*?)\nTrigger:\s*(.*?)\nProcedure:\s*(.*?)\s*\[/SKILL_CREATED\]",
        agent_output,
        re.DOTALL,
    )
    if match:
        name      = match.group(1).strip()
        trigger   = match.group(2).strip()
        procedure = match.group(3).strip()
        asyncio.create_task(_skill_db.commit_skill(name, trigger, procedure))
        logger.info("Skill harvested and queued for persistence: %s", name)


# ── Before-tool ───────────────────────────────────────────────────────────────

async def before_tool_callback(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> Optional[dict]:
    """Log tool calls; block sensitive tools for low-privilege sessions."""
    tool_name = tool.name
    user_role = tool_context.state.get("user:role", "guest")

    if tool_name in _SENSITIVE_TOOLS:
        logger.info("TOOL [%s] role=%s args_keys=%s", tool_name, user_role, list(args.keys()))
        if user_role == "guest":
            logger.warning("BLOCKED tool %s — guest role has no shell access", tool_name)
            return {"error": f"Tool '{tool_name}' requires at minimum 'user' role. Current role: guest."}

    return None  # None = proceed normally


# ── After-tool ────────────────────────────────────────────────────────────────

async def after_tool_callback(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, result: dict
) -> Optional[dict]:
    """Log tool results at debug level."""
    tool_name = tool.name
    status = result.get("status", "?") if isinstance(result, dict) else "raw"
    logger.debug("TOOL [%s] -> status=%s", tool_name, status)
    return None  # None = keep original result


# ── Model error ───────────────────────────────────────────────────────────────

async def on_model_error_callback(callback_context: CallbackContext, llm_request, error: Exception):
    """Log model-level errors. Return None to let ADK propagate the error."""
    logger.error(
        "Model error in agent '%s': %s — %s",
        callback_context.agent_name,
        type(error).__name__,
        error,
    )
    return None


# ── Tool error ────────────────────────────────────────────────────────────────

async def on_tool_error_callback(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, error: Exception
) -> Optional[dict]:
    """Convert tool exceptions into structured error dicts instead of crashing."""
    logger.error("TOOL [%s] raised %s: %s", tool.name, type(error).__name__, error)
    return {
        "error": f"{type(error).__name__}: {error}",
        "tool": tool.name,
        "status": "failed",
    }
