import uuid
import logging
from google.adk.agents import Agent
from google.adk.workflow import Workflow
from google.adk.tools import FunctionTool
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.genai import types
from metaops.config import MetaOpsConfig
from metaops.tools.web_search import (
    web_search_tool,
    web_extract_tool,
    web_crawl_tool,
    company_info_tool,
)

logger = logging.getLogger(__name__)
config = MetaOpsConfig()

_RESEARCHER_INSTRUCTION = """You are a research specialist. Gather comprehensive, accurate information on the given topic.

Process:
1. Run 2-3 distinct web_search queries covering different angles of the topic
2. Use web_extract on the 3-5 most relevant pages to pull full content
3. Use web_crawl on official documentation or authoritative sources when depth is needed
4. Use company_info if the query involves a specific company or product

Gather ALL raw material: data, citations, code examples, expert opinions, release notes.
Do not filter or summarize yet — collect everything.
End with: RESEARCH COMPLETE: [N] sources gathered"""

_SYNTHESIZER_INSTRUCTION = """Read all the research gathered above and produce a clean, structured report.

## Summary
[2-3 sentence answer to the original query]

## Key Findings
[Bullet points, most important first, with citations where relevant]

## Sources
[List of URLs/sites referenced]

## Recommendation
[What to use, do, or decide — skip if purely informational]

Be concise. Cite sources inline. Skip sections that have no content."""

researcher_agent = Agent(
    name="researcher",
    model=config.workstream.to_litellm(),
    instruction=_RESEARCHER_INSTRUCTION,
    tools=[web_search_tool, web_extract_tool, web_crawl_tool, company_info_tool],
)

synthesizer_agent = Agent(
    name="synthesizer",
    model=config.coordinator.to_litellm(),
    instruction=_SYNTHESIZER_INSTRUCTION,
)

research_workflow = Workflow(
    name="research",
    edges=[("START", researcher_agent, synthesizer_agent)],
)


async def deep_research(query: str) -> dict:
    """Search the web comprehensively and synthesize a structured report.

    Two-stage pipeline: researcher gathers raw material from multiple sources
    using search, extraction, and crawling; synthesizer produces a clean report.

    Args:
        query: The research question. Include constraints, versions, or domain
               context for better results.

    Returns:
        dict with keys:
            report — structured markdown report with findings and sources
            query  — echo of the original query
    """
    runner = Runner(
        node=research_workflow,
        app_name="metaops_research",
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )
    session = await runner.session_service.create_session(
        app_name="metaops_research",
        user_id="researcher",
        session_id=str(uuid.uuid4()),
    )
    parts: list[str] = []
    async for event in runner.run_async(
        user_id="researcher",
        session_id=session.id,
        new_message=types.Content(parts=[types.Part(text=query)]),
    ):
        if event.content:
            for part in event.content.parts or []:
                if part.text:
                    parts.append(part.text)

    report = "\n".join(parts)
    logger.info("Research complete for query: %s", query[:80])
    return {"report": report, "query": query}


deep_research_tool = FunctionTool(func=deep_research)
