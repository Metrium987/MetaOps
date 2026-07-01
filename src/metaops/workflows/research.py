"""Deep research — gather → refine → synthesize pipeline.

Three-stage pipeline:
  Stage 1: Researcher gathers raw material using web tools
  Stage 2: Bounded refinement loop — an evaluator grades the research;
           on "fail" a refiner runs the suggested follow-up searches and
           merges the new findings back in, up to MAX_REFINEMENT_ITERATIONS
  Stage 3: Synthesizer produces a clean, structured report

Exposed as FunctionTool for the coordinator agent.
"""

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Literal

from google.adk.agents import Agent, BaseAgent, LoopAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types
from pydantic import BaseModel, Field

from metaops.config import get_config
from metaops.workflows.agent_runner import run_agent_once
from metaops.tools.web_search import (
    web_search_tool,
    web_extract_tool,
    web_crawl_tool,
    company_info_tool,
)

logger = logging.getLogger(__name__)
config = get_config()

MAX_REFINEMENT_ITERATIONS = 3


class ResearchFeedback(BaseModel):
    """Structured verdict from the research evaluator."""

    grade: Literal["pass", "fail"] = Field(
        description="'pass' if the research is sufficient, 'fail' if it needs another refinement pass."
    )
    follow_up_queries: list[str] = Field(
        default_factory=list,
        description="Specific follow-up search queries to fill research gaps. Empty if grade is 'pass'.",
    )


_RESEARCHER_INSTRUCTION = """You are a research specialist. Gather comprehensive, accurate information on the given topic.

Process:
1. Run 2-3 distinct web_search queries covering different angles of the topic
2. Use web_extract on the 3-5 most relevant pages to pull full content
3. Use web_crawl on official documentation or authoritative sources when depth is needed
4. Use company_info if the query involves a specific company or product

Gather ALL raw material: data, citations, code examples, expert opinions, release notes.
Do not filter or summarize yet — collect everything.
End with: RESEARCH COMPLETE: [N] sources gathered"""

_EVALUATOR_INSTRUCTION = """You are a meticulous quality assurance analyst evaluating the research findings stored in the 'raw_research' state key.

CRITICAL RULES:
1. Assume the research topic itself is correct — do not question or fact-check the premise.
2. Judge only the QUALITY of the research: comprehensiveness of coverage, source variety, depth, and clarity.
3. If there are significant gaps in depth or coverage, grade "fail" and list 3-5 specific, targeted follow-up search queries to close them.
4. If the research thoroughly covers the topic, grade "pass" with an empty follow_up_queries list.

Respond with a single raw JSON object matching the ResearchFeedback schema."""

_REFINER_INSTRUCTION = """You are a specialist researcher running a refinement pass.
The previous research was graded "fail" — read the 'research_evaluation' state key to see why and which follow-up queries were requested.

1. Run every query listed in follow_up_queries using web_search, then web_extract the most relevant results.
2. Combine the new findings with the existing material already in 'raw_research'.

Your output MUST be the new, complete, improved set of research findings — do not drop anything gathered in the previous pass."""

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
    output_key="raw_research",
)

research_evaluator = Agent(
    name="research_evaluator",
    model=config.workstream.to_model(),
    instruction=_EVALUATOR_INSTRUCTION,
    output_schema=ResearchFeedback,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    output_key="research_evaluation",
)


class EscalationChecker(BaseAgent):
    """Stops the refinement loop once research_evaluator grades the research 'pass'."""

    def __init__(self, name: str = "escalation_checker"):
        super().__init__(name=name)

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        evaluation = ctx.session.state.get("research_evaluation")
        if evaluation and evaluation.get("grade") == "pass":
            logger.info("[%s] Research passed evaluation, stopping loop.", self.name)
            yield Event(author=self.name, actions=EventActions(escalate=True))
        else:
            logger.info("[%s] Research still failing, loop continues.", self.name)
            yield Event(author=self.name)


researcher_refiner = Agent(
    name="researcher_refiner",
    model=config.workstream.to_model(),
    instruction=_REFINER_INSTRUCTION,
    tools=[web_search_tool, web_extract_tool],
    output_key="raw_research",
)

refinement_loop = LoopAgent(
    name="research_refinement_loop",
    sub_agents=[research_evaluator, EscalationChecker(), researcher_refiner],
    max_iterations=MAX_REFINEMENT_ITERATIONS,
)

research_pipeline = SequentialAgent(
    name="research_pipeline",
    sub_agents=[researcher_agent, refinement_loop],
)

synthesizer_agent = Agent(
    name="synthesizer",
    model=config.coordinator.to_model(),
    instruction=_SYNTHESIZER_INSTRUCTION,
)


async def _run_research_pipeline(query: str) -> str:
    """Runs researcher -> bounded evaluate/refine loop, returns the final raw_research text.

    Unlike run_agent_once(), this needs access to session state (the loop's
    sub-agents communicate via output_key, not via returned text), so it
    drives its own ephemeral Runner instead of going through run_agent_once().
    """
    app_name = "metaops_research_pipeline"
    user_id = "research_pipeline"
    runner = Runner(
        agent=research_pipeline,
        app_name=app_name,
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )
    session = await runner.session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=str(uuid.uuid4()),
    )
    async for _event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=types.Content(parts=[types.Part(text=query)]),
    ):
        pass

    final_session = await runner.session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session.id
    )
    raw_research = final_session.state.get("raw_research", "") if final_session else ""
    if not raw_research:
        raise RuntimeError("Research pipeline produced no output in 'raw_research' state.")
    return raw_research


async def deep_research(query: str) -> dict:
    """Search the web comprehensively and synthesize a structured report.

    Pipeline: researcher gathers raw material from multiple sources using
    search, extraction, and crawling; a bounded evaluate/refine loop grades
    the research and runs follow-up searches on gaps (up to
    MAX_REFINEMENT_ITERATIONS); synthesizer then produces a clean report.

    Args:
        query: The research question. Include constraints, versions, or domain
               context for better results.

    Returns:
        dict with keys:
            report — structured markdown report with findings and sources
            query  — echo of the original query
    """
    # Stage 1+2: Gather raw material, then evaluate/refine until it passes
    # quality bar or the iteration budget runs out.
    logger.info("Research started for: %s", query[:80])
    raw_research = await _run_research_pipeline(query)
    logger.info("Research gathering complete (%d chars)", len(raw_research))

    # Stage 3: Synthesize into a clean report
    synthesis_prompt = (
        f"## Original Query\n{query}\n\n"
        f"## Raw Research\n{raw_research}"
    )
    report = await run_agent_once(synthesizer_agent, synthesis_prompt)
    logger.info("Research synthesis complete for: %s", query[:80])

    return {"report": report, "query": query}


deep_research_tool = FunctionTool(func=deep_research)
