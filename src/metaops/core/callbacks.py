import re
import asyncio
import logging
from google.adk.agents.callback_context import CallbackContext
from metaops.memory.vector_service import HybridVectorMemoryService
from metaops.memory.database import MemoryDatabase

logger = logging.getLogger(__name__)

_memory_service: HybridVectorMemoryService = None
_skill_db = MemoryDatabase()


def init_callbacks(memory_service: HybridVectorMemoryService):
    global _memory_service
    _memory_service = memory_service


async def auto_inject_memory_callback(callback_context: CallbackContext):
    """Before-agent: load skill names into state once per session.

    Memory injection is handled natively by the preload_memory tool
    (google.adk.tools.preload_memory) which runs automatically on each
    LLM request and injects past conversations via llm_request.append_instructions().
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


async def skill_harvest_callback(callback_context: CallbackContext):
    """After-agent: parse [SKILL_CREATED] blocks from agent output and persist them."""
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
