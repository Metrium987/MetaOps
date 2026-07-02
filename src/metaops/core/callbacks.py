import re
import time
import uuid
import asyncio
import logging
from typing import Any, Optional
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.tool_context import ToolContext
from google.adk.tools.base_tool import BaseTool
from google.adk.plugins.base_plugin import BasePlugin
from metaops.memory.vector_service import HybridVectorMemoryService
from metaops.memory.database import get_db_singleton, get_db

logger = logging.getLogger(__name__)

_memory_service: HybridVectorMemoryService = None
_skill_db = get_db_singleton()

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


# ══════════════════════════════════════════════════════════════════════════════
# MetaOps Plugin — global callbacks registered once on the Runner
# ══════════════════════════════════════════════════════════════════════════════

class MetaOpsPlugin(BasePlugin):
    """Global plugin that applies logging, memory indexing, skill harvesting,
    and tool security checks to every agent managed by the Runner."""

    def __init__(self, memory_service: HybridVectorMemoryService = None):
        super().__init__(name="metaops_plugin")
        self._memory_service = memory_service
        self._db_initialized = False

    async def _ensure_db_initialized(self):
        """Lazily initialize SQLite tables on first use."""
        if not self._db_initialized:
            from metaops.memory.database import initialize_db
            await initialize_db()
            self._db_initialized = True

    # ── After-agent callbacks ──────────────────────────────────────────────

    async def after_agent_callback(
        self, *, agent: BaseAgent, callback_context: CallbackContext
    ) -> None:
        """Indexes memory, harvests skills, and logs subagents after each agent run."""
        await self._memory_indexing(callback_context)
        await self._skill_harvest(callback_context)
        await self._subagent_logging(callback_context)

    async def _memory_indexing(self, callback_context: CallbackContext):
        if self._memory_service is None:
            return
        try:
            session = callback_context.session
            last_count = callback_context.state.get("_last_indexed_event_count", 0)
            current_count = len(session.events)
            if current_count <= last_count:
                return
            new_events = session.events[last_count:]
            if new_events:
                await self._memory_service.add_events_to_memory(
                    app_name=session.app_name,
                    user_id=session.user_id,
                    session_id=session.id,
                    events=new_events,
                )
                callback_context.state["_last_indexed_event_count"] = current_count
        except NotImplementedError:
            try:
                await self._memory_service.add_session_to_memory(callback_context.session)
            except Exception as exc:
                logger.warning("Failed to index session to memory (fallback): %s", exc)
        except Exception as exc:
            logger.warning("Failed to index session %s to memory: %s", callback_context.session.id, exc)

    async def _skill_harvest(self, callback_context: CallbackContext):
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
            agent_output, re.DOTALL,
        )
        if match:
            name, description, instructions = match.group(1).strip(), match.group(2).strip(), match.group(3).strip()
            task = asyncio.create_task(
                _skill_db.commit_skill(name=name, description=description, instructions=instructions, status="pending_review")
            )
            def log_exc(t):
                try:
                    t.result()
                except Exception as exc:
                    logger.error("Failed to commit harvested skill: %s", exc)
            task.add_done_callback(log_exc)
            logger.info("Skill harvested (pending_review): %s", name)

    async def _subagent_logging(self, callback_context: CallbackContext):
        agent_name = callback_context.agent_name
        if agent_name == "metaops_coordinator":
            return
        session_id = callback_context.session.id if callback_context.session else "unknown"
        query_text = ""
        response_text = ""
        for event in callback_context.session.events:
            if event.author == "user" and event.content and event.content.parts:
                query_text = event.content.parts[0].text or ""
                break
        for event in reversed(callback_context.session.events):
            if event.author == agent_name and event.content and event.content.parts:
                response_text = event.content.parts[0].text or ""
                break

        # Read accumulated token counts from model callbacks
        token_key = f"_agent_tokens:{agent_name}"
        tokens = callback_context.state.pop(token_key, {"prompt": 0, "completion": 0, "total": 0})

        try:
            await self._ensure_db_initialized()
            db = await get_db()
            await db.execute("""
                INSERT INTO subagent_logs (
                    id, session_id, parent_agent, subagent_name, query, response,
                    prompt_tokens, completion_tokens, total_tokens, latency_ms, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(uuid.uuid4()), session_id, "metaops_coordinator", agent_name, query_text, response_text, tokens["prompt"], tokens["completion"], tokens["total"], 0, "success"))
            await db.commit()
        except Exception as e:
            logger.warning("Failed to save subagent log to DB: %s", e)

    # ── Model callbacks ────────────────────────────────────────────────────

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request
    ) -> None:
        agent_name = callback_context.agent_name
        session_id = callback_context.session.id if callback_context.session else "unknown"
        callback_context.state[f"_llm_start_time:{agent_name}:{session_id}"] = time.time()

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response
    ) -> None:
        agent_name = callback_context.agent_name
        session_id = callback_context.session.id if callback_context.session else "unknown"
        start_key = f"_llm_start_time:{agent_name}:{session_id}"
        start_time = callback_context.state.pop(start_key, None)
        latency_ms = int((time.time() - start_time) * 1000) if start_time else None
        model = llm_response.model_version or "unknown"
        prompt_text = ""
        for event in reversed(callback_context.session.events):
            if event.author == "user" and event.content and event.content.parts:
                prompt_text = event.content.parts[0].text or ""
                break
        completion_text = ""
        if llm_response.content and llm_response.content.parts:
            part = llm_response.content.parts[0]
            if part.text:
                completion_text = part.text
            elif part.function_call:
                completion_text = f"FunctionCall: {part.function_call.name}({part.function_call.args})"
        prompt_tokens = completion_tokens = total_tokens = 0
        if llm_response.usage_metadata:
            prompt_tokens = getattr(llm_response.usage_metadata, "prompt_token_count", 0)
            completion_tokens = getattr(llm_response.usage_metadata, "candidates_token_count", 0)
            total_tokens = getattr(llm_response.usage_metadata, "total_token_count", 0)

        # Accumulate tokens per agent for subagent logging
        token_key = f"_agent_tokens:{agent_name}"
        prev = callback_context.state.get(token_key, {"prompt": 0, "completion": 0, "total": 0})
        callback_context.state[token_key] = {
            "prompt": prev["prompt"] + prompt_tokens,
            "completion": prev["completion"] + completion_tokens,
            "total": prev["total"] + total_tokens,
        }

        # Extract provider from model string (e.g. "openai/gpt-4o" -> "openai")
        provider = model.split("/")[0] if "/" in model else "unknown"

        try:
            await self._ensure_db_initialized()
            db = await get_db()
            await db.execute("""
                INSERT INTO portkey_logs (
                    id, session_id, role, provider, model, prompt, completion,
                    prompt_tokens, completion_tokens, total_tokens, cost, latency_ms, status_code, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(uuid.uuid4()), session_id, agent_name, provider, model, prompt_text, completion_text, prompt_tokens, completion_tokens, total_tokens, 0.0, latency_ms, 200, None))
            await db.commit()
        except Exception as e:
            logger.warning("Failed to save Portkey log to DB: %s", e)

    async def on_model_error_callback(
        self, *, callback_context: CallbackContext, llm_request, error: Exception
    ) -> None:
        logger.error("Model error in agent '%s': %s — %s", callback_context.agent_name, type(error).__name__, error)

    # ── Tool callbacks ─────────────────────────────────────────────────────

    async def before_tool_callback(
        self, *, tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
    ) -> Optional[dict]:
        tool_name = tool.name
        user_role = tool_context.state.get("user:role", "guest")
        if tool_name in _SENSITIVE_TOOLS:
            logger.info("TOOL [%s] role=%s args_keys=%s", tool_name, user_role, list(args.keys()))
            if user_role == "guest":
                logger.warning("BLOCKED tool %s — guest role has no shell access", tool_name)
                return {"error": f"Tool '{tool_name}' requires at minimum 'user' role. Current role: guest."}
        return None

    async def after_tool_callback(
        self, *, tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: dict
    ) -> Optional[dict]:
        status = tool_response.get("status", "?") if isinstance(tool_response, dict) else "raw"
        logger.debug("TOOL [%s] -> status=%s", tool.name, status)
        return None

    async def on_tool_error_callback(
        self, *, tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, error: Exception
    ) -> Optional[dict]:
        logger.error("TOOL [%s] raised %s: %s", tool.name, type(error).__name__, error)
        return {"error": f"{type(error).__name__}: {error}", "tool": tool.name, "status": "failed"}


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


async def before_model_callback(callback_context: CallbackContext, llm_request):
    """Record LLM call start time to calculate latency in after_model_callback."""
    agent_name = callback_context.agent_name
    session_id = callback_context.session.id if callback_context.session else "unknown"
    start_key = f"_llm_start_time:{agent_name}:{session_id}"
    callback_context.state[start_key] = time.time()
    logger.debug("Starting LLM request for agent %s in session %s", agent_name, session_id)


async def after_model_callback(callback_context: CallbackContext, llm_response) -> Optional[Any]:
    """Capture LLM response and log it to portkey_logs."""
    agent_name = callback_context.agent_name
    session_id = callback_context.session.id if callback_context.session else "unknown"
    
    start_key = f"_llm_start_time:{agent_name}:{session_id}"
    start_time = callback_context.state.pop(start_key, None)
    latency_ms = int((time.time() - start_time) * 1000) if start_time else None
    
    log_id = str(uuid.uuid4())
    model = llm_response.model_version or "unknown"
    
    prompt_text = ""
    for event in reversed(callback_context.session.events):
        if event.author == "user" and event.content and event.content.parts:
            prompt_text = event.content.parts[0].text or ""
            break
            
    completion_text = ""
    if llm_response.content and llm_response.content.parts:
        part = llm_response.content.parts[0]
        if part.text:
            completion_text = part.text
        elif part.function_call:
            completion_text = f"FunctionCall: {part.function_call.name}({part.function_call.args})"
            
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    if llm_response.usage_metadata:
        prompt_tokens = getattr(llm_response.usage_metadata, "prompt_token_count", 0)
        completion_tokens = getattr(llm_response.usage_metadata, "candidates_token_count", 0)
        total_tokens = getattr(llm_response.usage_metadata, "total_token_count", 0)

    try:
        db = await get_db()
        await db.execute("""
            INSERT INTO portkey_logs (
                id, session_id, role, provider, model, prompt, completion,
                prompt_tokens, completion_tokens, total_tokens, cost, latency_ms, status_code, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            log_id,
            session_id,
            agent_name,
            "openai_compatible",
            model,
            prompt_text,
            completion_text,
            prompt_tokens,
            completion_tokens,
            total_tokens,
            0.0,
            latency_ms,
            200,
            None
        ))
        await db.commit()
    except Exception as e:
        logger.warning("Failed to save Portkey log to DB: %s", e)
        
    return None


async def subagent_logging_callback(callback_context: CallbackContext):
    """Log subagent execution details to subagent_logs."""
    agent_name = callback_context.agent_name
    if agent_name == "metaops_coordinator":
        return
        
    session_id = callback_context.session.id if callback_context.session else "unknown"
    
    query_text = ""
    response_text = ""
    for event in callback_context.session.events:
        if event.author == "user" and event.content and event.content.parts:
            query_text = event.content.parts[0].text or ""
            break
            
    for event in reversed(callback_context.session.events):
        if event.author == agent_name and event.content and event.content.parts:
            response_text = event.content.parts[0].text or ""
            break
            
    log_id = str(uuid.uuid4())

    try:
        db = await get_db()
        await db.execute("""
            INSERT INTO subagent_logs (
                id, session_id, parent_agent, subagent_name, query, response,
                prompt_tokens, completion_tokens, total_tokens, latency_ms, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            log_id,
            session_id,
            "metaops_coordinator",
            agent_name,
            query_text,
            response_text,
            0, 0, 0,
            0,
            "success"
        ))
        await db.commit()
    except Exception as e:
        logger.warning("Failed to save subagent log to DB: %s", e)


async def combined_after_agent_callback(callback_context: CallbackContext):
    """Combined after-agent callback: indexes memory + harvests skills + logs subagents.

    ADK only supports a single after_agent_callback, so this merges both.
    """
    await memory_indexing_callback(callback_context)
    await skill_harvest_callback(callback_context)
    await subagent_logging_callback(callback_context)


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
