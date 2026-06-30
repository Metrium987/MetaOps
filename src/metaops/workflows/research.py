"""Deep research — gather → synthesize pipeline.

Two-stage pipeline:
  Stage 1: Researcher gathers raw material using web tools
  Stage 2: Synthesizer produces a clean, structured report

Exposed as FunctionTool for the coordinator agent.
"""

import logging
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from metaops.config import MetaOpsConfig
from metaops.workflows.agent_runner import run_agent_once
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
    model=config.workstream.to_model(),
    instruction=_RESEARCHER_INSTRUCTION,
    tools=[web_search_tool, web_extract_tool, web_crawl_tool, company_info_tool],
)

synthesizer_agent = Agent(
    name="synthesizer",
    model=config.coordinator.to_model(),
    instruction=_SYNTHESIZER_INSTRUCTION,
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
    # Stage 1: Gather raw material
    logger.info("Research started for: %s", query[:80])
    raw_research = await run_agent_once(researcher_agent, query)
    logger.info("Research gathering complete (%d chars)", len(raw_research))

    # Stage 2: Synthesize into a clean report
    synthesis_prompt = (
        f"## Original Query\n{query}\n\n"
        f"## Raw Research\n{raw_research}"
    )
    report = await run_agent_once(synthesizer_agent, synthesis_prompt)
    logger.info("Research synthesis complete for: %s", query[:80])

    return {"report": report, "query": query}


deep_research_tool = FunctionTool(func=deep_research)
