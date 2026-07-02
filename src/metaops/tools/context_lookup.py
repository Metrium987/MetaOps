"""Context lookup tool for sub-agents.

Allows sub-agents to consult the database for past contexts
when normal session.state transmission fails.
"""

import json
import logging
from google.adk.tools import FunctionTool, ToolContext
from metaops.memory.database import get_db_singleton

logger = logging.getLogger(__name__)


async def lookup_past_context(
    loop_type: str,
    query: str = "",
    limit: int = 3,
    tool_context: ToolContext = None,
) -> dict:
    """Consult the database for past loop contexts of the same type.

    Use this when you need context from previous executions and
    session.state doesn't have the information you need.

    Args:
        loop_type: Type of loop (code, research, reasoning).
        query: Optional search query to find similar past results.
        limit: Maximum results to return (default 3).

    Returns:
        JSON with past contexts including query, result, iterations, timestamp.
    """
    try:
        db = get_db_singleton()
        if query:
            results = await db.search_loop_context(query=query, loop_type=loop_type, limit=limit)
        else:
            results = await db.get_context_for_agent(loop_type=loop_type, query=query, limit=limit)

        return {
            "status": "success",
            "loop_type": loop_type,
            "count": len(results),
            "contexts": results,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def save_my_result(
    loop_type: str,
    query: str,
    result: dict,
    approved: bool = False,
    iterations: int = 1,
    tool_context: ToolContext = None,
) -> dict:
    """Save your execution result to the database for future reference.

    Call this when you complete a task so other agents can learn from it.

    Args:
        loop_type: Type of work (code, research, reasoning).
        query: The original task/query.
        result: Structured JSON result of your work.
        approved: Whether the result was approved.
        iterations: How many iterations it took.
    """
    try:
        import uuid
        db = get_db_singleton()
        session_id = ""
        app_name = ""
        user_id = ""
        if tool_context and tool_context.session:
            session_id = tool_context.session.id if tool_context.session else ""
            app_name = tool_context.session.app_name if tool_context.session else ""
            user_id = tool_context.session.user_id if tool_context.session else ""

        result_text = json.dumps(result) if isinstance(result, dict) else str(result)

        await db.save_loop_context(
            loop_id=str(uuid.uuid4()),
            session_id=session_id,
            loop_type=loop_type,
            query=query,
            result=result_text,
            iterations=iterations,
            approved=approved,
            status="completed",
            app_name=app_name,
            user_id=user_id,
        )
        return {"status": "saved", "loop_type": loop_type}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# Tools for sub-agents
context_lookup_tools = [
    FunctionTool(func=lookup_past_context),
    FunctionTool(func=save_my_result),
]
