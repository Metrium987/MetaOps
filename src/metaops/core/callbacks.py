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
    "full_dev_cycle",
    "execute_skill",
    "ingest_file_dependency",
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
        skill_names = await _skill_db.list_skill_names(status="approved")
        if skill_names:
            callback_context.state["available_skills"] = ", ".join(skill_names)
        callback_context.state["skills_loaded"] = True
    except Exception as exc:
        logger.warning("Could not load skills: %s", exc)


# ── After-agent ───────────────────────────────────────────────────────────────

async def memory_indexing_callback(callback_context: CallbackContext):
    """Index ONLY new session events into ChromaDB (incremental).

    The previous implementation called add_session_to_memory which re-indexed
    ALL events every turn, creating duplicate entries in ChromaDB.
    Instead, we track the last indexed event count and only index new ones.
    """
    if _memory_service is None:
        logger.debug("Memory service not initialized — skipping indexing")
        return
    try:
        session = callback_context.session
        last_count = callback_context.state.get("_last_indexed_event_count", 0)
        current_count = len(session.events)
        if current_count <= last_count:
            return  # No new events to index

        # Index only the new events
        new_events = session.events[last_count:]
        if new_events:
            await _memory_service.add_events_to_memory(
                app_name=session.app_name,
                user_id=session.user_id,
                session_id=session.id,
                events=new_events,
            )
            callback_context.state["_last_indexed_event_count"] = current_count
            logger.info("Indexed %d new events from session %s", len(new_events), session.id)
    except NotImplementedError:
        # Fallback: add_session_to_memory if add_events_to_memory not supported
        try:
            await _memory_service.add_session_to_memory(callback_context.session)
            logger.info("Indexed session %s via fallback add_session_to_memory", callback_context.session.id)
        except Exception as exc:
            logger.warning("Failed to index session to memory (fallback): %s", exc)
    except Exception as exc:
        logger.warning("Failed to index session %s to memory: %s", callback_context.session.id, exc)


async def skill_harvest_callback(callback_context: CallbackContext):
    """Persist skills from agent output.

    Legacy path: still parses [SKILL_CREATED] blocks for backward compat,
    but writes through the unified commit_skill (L1/L2/L3, pending_review).
    New skills should be created via save_procedural_skill tool instead.
    """
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
        name        = match.group(1).strip()
        description = match.group(2).strip()
        instructions = match.group(3).strip()
        task = asyncio.create_task(
            _skill_db.commit_skill(
                name=name,
                description=description,
                instructions=instructions,
                status="pending_review",
            )
        )
        def log_task_exception(t):
            try:
                t.result()
            except Exception as exc:
                logger.error("Failed to commit harvested skill: %s", exc)
        task.add_done_callback(log_task_exception)
        logger.info("Skill harvested (pending_review): %s", name)


async def combined_after_agent_callback(callback_context: CallbackContext):
    """Combined after-agent callback: indexes memory + harvests skills.

    ADK only supports a single after_agent_callback, so this merges both.
    """
    await memory_indexing_callback(callback_context)
    await skill_harvest_callback(callback_context)


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
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: dict
) -> Optional[dict]:
    """Log tool results at debug level."""
    tool_name = tool.name
    status = tool_response.get("status", "?") if isinstance(tool_response, dict) else "raw"
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
