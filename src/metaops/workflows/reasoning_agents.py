"""Reasoning sub-agents extracted from OptiLLM patterns.

Four reasoning strategies adapted as ADK agents:
  1. Best-of-N Selector  -- generate N options, judge picks the best
  2. CoT Reflection       -- think, reflect, output (single-pass structured reasoning)
  3. Self-Consistency     -- multiple samples, majority vote aggregation
  4. MoA Critique         -- generate diverse candidates, critique, synthesize

Three autonomous LoopAgent tools:
  5. Code Loop    -- code -> test -> review -> repeat until approved
  6. Research Loop -- research -> critique -> repeat until quality
  7. Reasoning Loop -- think -> judge -> repeat until solid

Context flow (per ADK docs):
  - LoopAgent passes the same InvocationContext to every sub-agent
  - output_key saves agent responses to session.state
  - {state_key} in instructions resolves from session.state
  - EscalationChecker reads session.state to decide loop exit
"""

import logging
import uuid
from collections.abc import AsyncGenerator
from google.adk.agents import Agent, BaseAgent, LoopAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.code_executors import UnsafeLocalCodeExecutor
from google.adk.events import Event, EventActions
from google.adk.tools import AgentTool
from metaops.config import get_config
from metaops.memory.database import get_db_singleton
from metaops.tools.context_lookup import context_lookup_tools
from metaops.tools.web_search import (
    web_search_tool,
    web_extract_tool,
    web_crawl_tool,
    company_info_tool,
)

logger = logging.getLogger(__name__)
config = get_config()


# ══════════════════════════════════════════════════════════════════════════════
# EscalationChecker -- shared termination agent for LoopAgents
# Reads session.state["approved"] to decide loop exit
# ══════════════════════════════════════════════════════════════════════════════

class EscalationChecker(BaseAgent):
    """Checks session.state for a completion flag and escalates to exit the loop.

    Parses JSON output from sub-agents and saves structured context to DB.
    """

    def __init__(self, name: str = "escalation_checker", flag_key: str = "approved", loop_type: str = "unknown"):
        super().__init__(name=name)
        self._flag_key = flag_key
        self._loop_type = loop_type

    def _parse_json_status(self, text: str) -> dict:
        """Extract JSON from agent output, handling markdown code blocks."""
        import re
        import json
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": "UNKNOWN"}

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        import json

        # Check approval from session state
        is_done = ctx.session.state.get(self._flag_key, False)

        # Also check the latest evaluation for JSON status
        eval_key = {
            "code": "review_result",
            "research": "research_evaluation",
            "reasoning": "reasoning_evaluation",
        }.get(self._loop_type, "result")
        eval_text = ctx.session.state.get(eval_key, "")
        if eval_text and not is_done:
            parsed = self._parse_json_status(str(eval_text))
            if parsed.get("status") == "APPROVED":
                is_done = True
                ctx.session.state[self._flag_key] = True

        if is_done:
            logger.info("[%s] Condition met, exiting loop.", self.name)
            # Save structured JSON context to database
            try:
                db = get_db_singleton()
                query = ctx.session.state.get("query", "")
                result_key = {
                    "code": "code_result",
                    "research": "research_result",
                    "reasoning": "reasoning_result",
                }.get(self._loop_type, "result")
                result_raw = ctx.session.state.get(result_key, "")

                # Parse result as JSON if possible
                result_parsed = self._parse_json_status(str(result_raw))
                result_json = json.dumps(result_parsed, ensure_ascii=False)

                eval_parsed = self._parse_json_status(str(eval_text))
                eval_json = json.dumps(eval_parsed, ensure_ascii=False)

                await db.save_loop_context(
                    loop_id=str(uuid.uuid4()),
                    session_id=ctx.session.id if ctx.session else "",
                    loop_type=self._loop_type,
                    query=query,
                    result=result_json,
                    iterations=ctx.session.state.get("iteration_count", 0),
                    approved=True,
                    status="completed",
                    app_name=ctx.session.app_name if ctx.session else "",
                    user_id=ctx.session.user_id if ctx.session else "",
                )
            except Exception as exc:
                logger.warning("Failed to save loop context: %s", exc)
            yield Event(author=self.name, actions=EventActions(escalate=True))
        else:
            ctx.session.state["iteration_count"] = ctx.session.state.get("iteration_count", 0) + 1
            yield Event(author=self.name)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Best-of-N Selector
# ══════════════════════════════════════════════════════════════════════════════

_BON_JUDGE_INSTRUCTION = """You are a response quality judge. You will receive a user query and multiple candidate responses.

Your task:
1. Rate each candidate on a scale of 0 to 10 based on: relevance, accuracy, completeness, clarity.
2. Identify the strongest and weakest aspects of each candidate.
3. Select the BEST candidate as your final answer.
4. Provide a brief justification for your selection.

Format your response as:
## Ratings
Candidate 1: X/10 -- [brief note]
Candidate 2: X/10 -- [brief note]
...

## Best Selection
[Candidate N] -- [justification]

## Final Answer
[The full text of the best candidate, copied verbatim]"""

bon_judge_agent = Agent(
    name="bon_judge",
    description="Judges multiple candidate responses and selects the best one.",
    model=config.workstream.to_model(),
    instruction=_BON_JUDGE_INSTRUCTION,
    tools=[web_search_tool, web_extract_tool],
    code_executor=UnsafeLocalCodeExecutor(),
)

bon_tool = AgentTool(agent=bon_judge_agent)


# ══════════════════════════════════════════════════════════════════════════════
# 2. CoT Reflection
# ══════════════════════════════════════════════════════════════════════════════

_COT_REFLECTION_INSTRUCTION = """You are a careful reasoning agent. For every query, follow this structured process:

1. THINK: Break down the problem step by step.
2. REFLECT: Review your thinking. Check for errors, gaps, or better approaches.
3. OUTPUT: Provide your final, concise answer.

Format:
<thinking>[Step-by-step reasoning]</thinking>
<reflection>[Self-correction]</reflection>
<output>[Final answer]</output>"""

cot_reflection_agent = Agent(
    name="cot_reflection",
    description="Deep structured reasoning with self-reflection.",
    model=config.thinker.to_model(),
    instruction=_COT_REFLECTION_INSTRUCTION,
    tools=[web_search_tool, web_extract_tool],
    code_executor=UnsafeLocalCodeExecutor(),
)

cot_reflection_tool = AgentTool(agent=cot_reflection_agent)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Self-Consistency Voter
# ══════════════════════════════════════════════════════════════════════════════

_SELF_CONSISTENCY_INSTRUCTION = """You are a majority-vote aggregator. You will receive multiple independent answers to the same question.

1. Group semantically equivalent answers.
2. Count how many answers fall into each group.
3. Return the answer from the largest group."""

self_consistency_agent = Agent(
    name="self_consistency",
    description="Aggregates multiple independent answers via majority vote.",
    model=config.workstream.to_model(),
    instruction=_SELF_CONSISTENCY_INSTRUCTION,
    tools=[web_search_tool],
)

self_consistency_tool = AgentTool(agent=self_consistency_agent)


# ══════════════════════════════════════════════════════════════════════════════
# 4. MoA Critique-and-Synthesize
# ══════════════════════════════════════════════════════════════════════════════

_MOA_CRITIC_INSTRUCTION = """You are a critical analyst with web search. You will receive a user query and three candidate responses.

1. Search the web to verify key claims.
2. Analyze each candidate: strengths, weaknesses, accuracy, completeness.
3. Cite web sources when verifying claims."""

_MOA_SYNTHESIZER_INSTRUCTION = """You are a response synthesizer with web search. You will receive a query, three candidates, and a critique.

1. Search the web to fill gaps or verify disputed claims.
2. Identify the strongest elements from each candidate.
3. Synthesize a final response combining the best aspects.
4. Cite your sources. Output ONLY the final answer."""

moa_critic_agent = Agent(
    name="moa_critic",
    description="Critiques multiple candidate responses with web verification.",
    model=config.workstream.to_model(),
    instruction=_MOA_CRITIC_INSTRUCTION,
    tools=[web_search_tool, web_extract_tool, company_info_tool],
)

moa_synthesizer_agent = Agent(
    name="moa_synthesizer",
    description="Synthesizes the best elements from multiple candidates.",
    model=config.coordinator.to_model(),
    instruction=_MOA_SYNTHESIZER_INSTRUCTION,
    tools=[web_search_tool, web_extract_tool, web_crawl_tool, company_info_tool],
)

moa_pipeline = SequentialAgent(
    name="moa_critique_synthesize",
    sub_agents=[moa_critic_agent, moa_synthesizer_agent],
)

moa_tool = AgentTool(agent=moa_pipeline)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Autonomous Code Loop
# Context flow: coder output_key="code_result" -> reviewer reads {code_result}
#              -> checker reads state["approved"]
# ══════════════════════════════════════════════════════════════════════════════

_AUTONOMOUS_CODER_INSTRUCTION = """You are an expert coder. Write correct, minimal, production-ready code.

Rules:
1. Write COMPLETE code -- no TODOs, no placeholders.
2. Test your code by executing it.
3. Fix any errors found.
4. When done, output a JSON block:
```json
{"status": "APPROVED"|"NEEDS_WORK", "code": "...", "tests_passed": true|false, "summary": "..."}
```"""

_AUTONOMOUS_REVIEWER_INSTRUCTION = """You are a strict code reviewer. Review this code:

{code_result}

Check: logic errors, security issues, missing imports, incomplete implementations.

Output a JSON block:
```json
{"status": "APPROVED"|"NEEDS_WORK", "issues": ["..."], "score": 0-10, "summary": "..."}
```"""

autonomous_coder = Agent(
    name="autonomous_coder",
    description="Writes and tests code autonomously in a loop.",
    model=config.coder.to_model(),
    instruction=_AUTONOMOUS_CODER_INSTRUCTION,
    code_executor=UnsafeLocalCodeExecutor(),
    tools=context_lookup_tools,
    output_key="code_result",
)

autonomous_reviewer = Agent(
    name="autonomous_reviewer",
    description="Reviews code for correctness and security.",
    model=config.workstream.to_model(),
    instruction=_AUTONOMOUS_REVIEWER_INSTRUCTION,
    tools=context_lookup_tools,
    output_key="review_result",
)


class _CodeApprovalChecker(BaseAgent):
    """Checks if reviewer approved the code by reading session.state."""
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        review = ctx.session.state.get("review_result", "")
        if "STATUS: APPROVED" in review:
            ctx.session.state["approved"] = True
            yield Event(author=self.name, actions=EventActions(escalate=True))
        else:
            yield Event(author=self.name)


code_loop = LoopAgent(
    name="autonomous_code_loop",
    max_iterations=config.max_revisions,
    sub_agents=[autonomous_coder, autonomous_reviewer, EscalationChecker(name="code_checker", loop_type="code")],
)

code_loop_tool = AgentTool(agent=code_loop)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Autonomous Research Loop
# Context flow: researcher output_key="research_result" -> critic reads {research_result}
#              -> checker reads state["approved"]
# ══════════════════════════════════════════════════════════════════════════════

_AUTONOMOUS_RESEARCHER_INSTRUCTION = """You are a web research specialist. Search thoroughly, extract key information, compile findings.

1. Run multiple search queries covering different angles.
2. Extract content from the most relevant pages.
3. Compile findings with source URLs.
4. When done, output a JSON block:
```json
{"status": "RESEARCH_COMPLETE", "findings": ["..."], "sources": ["url1", ...], "summary": "..."}
```"""

_AUTONOMOUS_RESEARCH_CRITIC_INSTRUCTION = """You are a research quality evaluator. Assess this research:

{research_result}

Check: source variety, depth, accuracy, coverage.

Output a JSON block:
```json
{"status": "APPROVED"|"NEEDS_MORE", "score": 0-10, "gaps": ["..."], "follow_up_queries": ["..."], "summary": "..."}
```"""

autonomous_researcher = Agent(
    name="autonomous_researcher",
    description="Conducts thorough web research autonomously.",
    model=config.research.to_model(),
    instruction=_AUTONOMOUS_RESEARCHER_INSTRUCTION,
    tools=[web_search_tool, web_extract_tool, web_crawl_tool, company_info_tool, *context_lookup_tools],
    output_key="research_result",
)

autonomous_research_critic = Agent(
    name="autonomous_research_critic",
    description="Evaluates research quality and identifies gaps.",
    model=config.workstream.to_model(),
    instruction=_AUTONOMOUS_RESEARCH_CRITIC_INSTRUCTION,
    tools=[web_search_tool, *context_lookup_tools],
    output_key="research_evaluation",
)


class _ResearchApprovalChecker(BaseAgent):
    """Checks if research quality is sufficient by reading session.state."""
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        evaluation = ctx.session.state.get("research_evaluation", "")
        if "STATUS: APPROVED" in evaluation:
            ctx.session.state["approved"] = True
            yield Event(author=self.name, actions=EventActions(escalate=True))
        else:
            yield Event(author=self.name)


research_loop = LoopAgent(
    name="autonomous_research_loop",
    max_iterations=config.max_refinement_iterations,
    sub_agents=[autonomous_researcher, autonomous_research_critic, EscalationChecker(name="research_checker", loop_type="research")],
)

research_loop_tool = AgentTool(agent=research_loop)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Autonomous Reasoning Loop
# Context flow: thinker output_key="reasoning_result" -> judge reads {reasoning_result}
#              -> checker reads state["approved"]
# ══════════════════════════════════════════════════════════════════════════════

_AUTONOMOUS_THINKER_INSTRUCTION = """You are a deep reasoning specialist.

1. Break down the problem into sub-questions.
2. Reason through each step, citing evidence.
3. Identify edge cases and hidden assumptions.
4. Reach a clear recommendation.

Output a JSON block:
```json
{"status": "REASONING_COMPLETE", "recommendation": "...", "tradeoffs": {"option1": "pros/cons", ...}, "confidence": 0-10, "summary": "..."}
```"""

_AUTONOMOUS_REASONING_JUDGE_INSTRUCTION = """You are a reasoning quality judge. Assess this reasoning:

{reasoning_result}

Check: logical consistency, evidence backing, edge cases, clarity.

Output a JSON block:
```json
{"status": "APPROVED"|"NEEDS_WORK", "score": 0-10, "weaknesses": ["..."], "suggestions": ["..."], "summary": "..."}
```"""

autonomous_thinker = Agent(
    name="autonomous_thinker",
    description="Performs deep structured reasoning autonomously.",
    model=config.thinker.to_model(),
    instruction=_AUTONOMOUS_THINKER_INSTRUCTION,
    tools=[web_search_tool, web_extract_tool, *context_lookup_tools],
    code_executor=UnsafeLocalCodeExecutor(),
    output_key="reasoning_result",
)

autonomous_reasoning_judge = Agent(
    name="autonomous_reasoning_judge",
    description="Evaluates reasoning quality and completeness.",
    model=config.workstream.to_model(),
    instruction=_AUTONOMOUS_REASONING_JUDGE_INSTRUCTION,
    tools=[web_search_tool, *context_lookup_tools],
    output_key="reasoning_evaluation",
)


class _ReasoningApprovalChecker(BaseAgent):
    """Checks if reasoning quality is sufficient by reading session.state."""
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        evaluation = ctx.session.state.get("reasoning_evaluation", "")
        if "STATUS: APPROVED" in evaluation:
            ctx.session.state["approved"] = True
            yield Event(author=self.name, actions=EventActions(escalate=True))
        else:
            yield Event(author=self.name)


reasoning_loop = LoopAgent(
    name="autonomous_reasoning_loop",
    max_iterations=config.max_continuations,
    sub_agents=[autonomous_thinker, autonomous_reasoning_judge, EscalationChecker(name="reasoning_checker", loop_type="reasoning")],
)

reasoning_loop_tool = AgentTool(agent=reasoning_loop)


# ══════════════════════════════════════════════════════════════════════════════
# Export all reasoning tools
# ══════════════════════════════════════════════════════════════════════════════

REASONING_TOOLS = [
    bon_tool,
    cot_reflection_tool,
    self_consistency_tool,
    moa_tool,
    code_loop_tool,
    research_loop_tool,
    reasoning_loop_tool,
]
