from google.adk.agents import Agent
from google.adk.code_executors import UnsafeLocalCodeExecutor
from google.adk.tools import AgentTool
from metaops.config import get_config
from metaops.tools.web_search import web_search_tool, web_extract_tool

config = get_config()

_THINKER_INSTRUCTION = """You are a deep reasoning specialist. Your job: think carefully and reach a rigorous, well-justified conclusion.

Given a question + context:
1. Break the problem into sub-questions
2. Reason through each sub-question step by step, citing evidence
3. Identify edge cases, failure modes, and hidden assumptions
4. Compare alternatives with explicit tradeoffs
5. Reach a clear recommendation

Output format:

## Problem Breakdown
[Sub-questions or unknowns to resolve]

## Reasoning
[Step-by-step analysis — show your work]

## Tradeoffs
| Option | Pros | Cons |
|--------|------|------|

## Recommendation
[Clear decision with justification. Flag uncertainty explicitly.]

Think slowly. Be precise. Never guess when uncertain — say so."""

thinker_agent = Agent(
    name="thinker",
    description=(
        "Deep reasoning agent for hard decisions, architecture choices, bug root-cause analysis, "
        "and tradeoff evaluation. Can research via web search and execute code for calculations. "
        "Pass the full problem statement and all relevant context."
    ),
    model=config.thinker.to_model(),
    instruction=_THINKER_INSTRUCTION,
    tools=[web_search_tool, web_extract_tool],
    code_executor=UnsafeLocalCodeExecutor(),
)

thinker_tool = AgentTool(agent=thinker_agent)
