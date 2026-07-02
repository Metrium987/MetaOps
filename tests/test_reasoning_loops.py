"""Quick test for reasoning loops and DB integration."""
import asyncio
import uuid
import sys
sys.path.insert(0, "src")

async def test_loops():
    from metaops.workflows.reasoning_agents import reasoning_loop, code_loop, research_loop, REASONING_TOOLS

    print(f"REASONING_TOOLS: {len(REASONING_TOOLS)} tools")
    for t in REASONING_TOOLS:
        print(f"  - {t.agent.name}")

    print(f"\nReasoning loop: {[a.name for a in reasoning_loop.sub_agents]}")
    print(f"Code loop: {[a.name for a in code_loop.sub_agents]}")
    print(f"Research loop: {[a.name for a in research_loop.sub_agents]}")

    # Test DB
    from metaops.memory.database import get_db_singleton
    db = get_db_singleton()
    await db.initialize()
    print("\nDB initialized OK")

    # Save test context
    await db.save_loop_context(
        loop_id=str(uuid.uuid4()),
        session_id="test_session",
        loop_type="reasoning",
        query="What is 2+2?",
        result='{"status": "APPROVED", "recommendation": "4", "confidence": 10}',
        iterations=1,
        approved=True,
        status="completed",
    )
    print("Saved test context OK")

    # Retrieve
    results = await db.get_recent_loop_context("reasoning", limit=5)
    print(f"Retrieved {len(results)} contexts")
    if results:
        print(f"  Latest: query={results[0]['query']}, approved={results[0]['approved']}")

    # Test search
    search_results = await db.search_loop_context("math", limit=3)
    print(f"Search 'math': {len(search_results)} results")

    # Test stats
    stats = await db.get_loop_stats()
    print(f"Stats: {stats}")

    # Test context_for_agent
    ctx = await db.get_context_for_agent("reasoning", "math problem", limit=3)
    print(f"Context for agent: {len(ctx)} results")

    print("\nAll tests passed!")

if __name__ == "__main__":
    asyncio.run(test_loops())
