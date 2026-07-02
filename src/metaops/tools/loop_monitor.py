"""Loop monitoring tool for the coordinator.

Allows the main LLM to query the database to see:
- Status of running/completed autonomous loops
- Recent loop executions by type
- Search past loop results by query
- Aggregate statistics

This enables the coordinator to know where sub-agents are in their execution.
"""

import logging
from google.adk.tools import FunctionTool, ToolContext
from metaops.memory.database import get_db_singleton

logger = logging.getLogger(__name__)


async def get_loop_status(tool_context: ToolContext = None) -> dict:
    """Get status of recent autonomous loop executions.

    Returns a summary of all recent loops (code, research, reasoning)
    with their status, iterations, and whether they were approved.
    """
    try:
        db = get_db_singleton()
        loops = await db.get_loop_status(limit=10)
        running = await db.get_running_loops()

        return {
            "running": running,
            "recent": loops,
            "running_count": len(running),
            "total_recent": len(loops),
        }
    except Exception as e:
        return {"error": str(e), "running": [], "recent": []}


async def search_past_loops(
    query: str,
    loop_type: str = "",
    limit: int = 5,
    tool_context: ToolContext = None,
) -> dict:
    """Search past loop executions by query similarity.

    Useful for finding how similar tasks were solved before.

    Args:
        query: Search query to find similar past loops.
        loop_type: Filter by type (code, research, reasoning). Empty = all types.
        limit: Maximum results to return.
    """
    try:
        db = get_db_singleton()
        results = await db.search_loop_context(
            query=query,
            loop_type=loop_type if loop_type else None,
            limit=limit,
        )
        return {"results": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e), "results": []}


async def get_loop_stats(tool_context: ToolContext = None) -> dict:
    """Get aggregate statistics for all autonomous loops.

    Shows total runs, approval rate, average iterations, and token usage
    per loop type (code, research, reasoning).
    """
    try:
        db = get_db_singleton()
        stats = await db.get_loop_stats()
        return {"stats": stats}
    except Exception as e:
        return {"error": str(e), "stats": {}}


async def get_loop_detail(
    loop_id: str,
    tool_context: ToolContext = None,
) -> dict:
    """Get detailed information about a specific loop execution.

    Args:
        loop_id: The ID of the loop to inspect.
    """
    try:
        db = get_db_singleton()
        db_conn = await db._get_db()
        cursor = await db_conn.execute(
            "SELECT * FROM loop_context WHERE id = ?", (loop_id,)
        )
        row = await cursor.fetchone()
        if row:
            return {"loop": dict(row)}
        return {"error": f"Loop {loop_id} not found"}
    except Exception as e:
        return {"error": str(e)}


# Export tools for the coordinator
loop_monitor_tools = [
    FunctionTool(func=get_loop_status),
    FunctionTool(func=search_past_loops),
    FunctionTool(func=get_loop_stats),
    FunctionTool(func=get_loop_detail),
]
