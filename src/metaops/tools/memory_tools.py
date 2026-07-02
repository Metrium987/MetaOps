import os
import logging
from typing import Optional, List, Dict, Any
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types
from metaops.memory.vector_service import HybridVectorMemoryService
from metaops.memory.database import get_db_singleton

logger = logging.getLogger(__name__)

_memory_service: HybridVectorMemoryService = None
_skill_db = get_db_singleton()


def init_memory_tools(memory_service: HybridVectorMemoryService):
    global _memory_service
    _memory_service = memory_service


async def save_procedural_skill(
    name: str,
    description: str,
    instructions: str,
    tool_context: ToolContext,
    resources: Optional[List[Dict[str, str]]] = None,
) -> dict:
    """Save a skill with explicit L1/L2/L3 structure.

    Writes to both SQLite (source of truth) and ChromaDB (L1 index).
    New skills are created with status 'pending_review' and must be
    approved before they become executable.
    """
    await _skill_db.initialize()

    # L2 + L3 → SQLite (source of truth)
    await _skill_db.commit_skill(
        name=name,
        description=description,
        instructions=instructions,
        resources=resources,
        status="pending_review",
    )

    # L1 → ChromaDB procedural_memory (semantic index only)
    if _memory_service:
        app_name = tool_context._invocation_context.app_name
        user_id = tool_context.user_id
        # Upsert: remove old entry if any, then add
        try:
            existing = _memory_service.procedural.get(where={"skill_name": name})
            if existing and existing["ids"]:
                _memory_service.procedural.delete(ids=existing["ids"])
        except Exception:
            pass

        _memory_service.procedural.add(
            documents=[description],
            metadatas=[{
                "skill_name": name,
                "app_name": app_name,
                "user_id": user_id,
            }],
            ids=[f"skill_{name}"],
        )

    # Update session state
    current_skills = tool_context.state.get("user:learned_skills", [])
    if name not in current_skills:
        current_skills.append(name)
    tool_context.state["user:learned_skills"] = current_skills

    logger.info("Skill saved (pending_review): %s", name)
    return {"status": "pending_review", "message": f"Skill '{name}' saved. Awaiting human approval."}


async def approve_skill(name: str, tool_context: ToolContext) -> dict:
    """Approve a pending skill — makes it executable."""
    await _skill_db.initialize()
    approved = await _skill_db.approve_skill(name)
    if approved:
        logger.info("Skill approved: %s", name)
        return {"status": "success", "message": f"Skill '{name}' approved and now executable."}
    return {"status": "error", "message": f"Skill '{name}' not found or not pending review."}


async def reject_skill(name: str, tool_context: ToolContext) -> dict:
    """Reject a pending skill."""
    await _skill_db.initialize()
    rejected = await _skill_db.reject_skill(name)
    if rejected:
        logger.info("Skill rejected: %s", name)
        return {"status": "success", "message": f"Skill '{name}' rejected."}
    return {"status": "error", "message": f"Skill '{name}' not found or not pending review."}


async def discover_skill(query: str, tool_context: ToolContext) -> dict:
    """Semantic search over skill descriptions (L1) to find relevant skills.

    Returns matching skill names and descriptions. The caller can then
    use execute_skill(name) to run the matched skill.
    """
    if not _memory_service:
        return {"status": "error", "message": "Memory service not initialized."}

    app_name = tool_context._invocation_context.app_name
    user_id = tool_context.user_id

    try:
        query_embeddings = _memory_service.embed_fn([query])
        res = _memory_service.procedural.query(
            query_embeddings=query_embeddings,
            n_results=5,
            where={"$and": [{"app_name": app_name}, {"user_id": user_id}]},
        )
    except Exception as e:
        return {"status": "error", "message": f"Search failed: {e}"}

    skills = []
    if res and res["documents"] and res["documents"][0]:
        for i, doc in enumerate(res["documents"][0]):
            meta = res["metadatas"][0][i] if res["metadatas"] else {}
            skills.append({
                "name": meta.get("skill_name", "unknown"),
                "description": doc,
            })

    return {"status": "success", "skills": skills}


async def recall_past_context(query: str, tool_context: ToolContext) -> dict:
    """Semantic search over past sessions via ADK native MemoryService."""
    memory_response = await tool_context.search_memory(query)

    context_snippets = []
    for memory in memory_response.memories:
        if memory.content and memory.content.parts:
            text = memory.content.parts[0].text
            if text:
                context_snippets.append(text)

    return {"status": "success", "snippets": context_snippets}


skill_saver_tool = FunctionTool(func=save_procedural_skill)
skill_approve_tool = FunctionTool(func=approve_skill)
skill_reject_tool = FunctionTool(func=reject_skill)
skill_discover_tool = FunctionTool(func=discover_skill)
memory_search_tool = FunctionTool(func=recall_past_context)
