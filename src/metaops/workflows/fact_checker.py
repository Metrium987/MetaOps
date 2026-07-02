"""Fact-checking pipeline: critic (claim extraction + web verification) → reviser (minimal edits).

Modeled on llm-auditor but adapted for Metaops:
- Uses web_search_tool (Tavily) instead of google_search
- Builds references from raw Tavily results instead of Gemini grounding_metadata
- Exposed as an AgentTool for on-demand invocation (not automatic per turn)
"""

from google.adk.agents import Agent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse
from google.adk.tools import AgentTool
from google.genai import types
from metaops.config import get_config
from metaops.tools.web_search import web_search_tool, web_extract_tool

config = get_config()

# ── Prompts ───────────────────────────────────────────────────────────────────

CRITIC_PROMPT = """You are a professional fact-checker. Your task: verify the factual accuracy of an answer using web search.

# Process

## Step 1: Extract CLAIMS
Read the answer and extract every distinct factual CLAIM — statements about the world, statistics, dates, names, events.

## Step 2: Verify each CLAIM
For each claim:
1. Search the web for evidence (use the web_search tool).
2. Assign a verdict:
   - **Accurate** — correct and supported by reliable sources
   - **Inaccurate** — contains errors or contradictions with sources
   - **Disputed** — reliable sources disagree
   - **Unsupported** — no reliable source found to confirm or deny
   - **Not Applicable** — subjective opinion, not requiring verification
3. Provide a brief justification citing your evidence.

## Step 3: Overall verdict
Assess the answer as a whole: Accurate / Mostly Accurate / Mixed / Inaccurate.

# Output format

For each claim, output:

**Claim N:** [the claim]
- **Verdict:** [Accurate|Inaccurate|Disputed|Unsupported|Not Applicable]
- **Justification:** [brief reasoning with source references]
- **Source:** [URL if available from search]

**Overall verdict:** [Accurate|Mostly Accurate|Mixed|Inaccurate]
**Overall justification:** [summary]

---

Original question and answer to verify:
"""

REVISER_PROMPT = """You are a professional editor. You receive a question-answer pair and a fact-checker's verification findings.

Your task: minimally revise the answer to fix inaccuracies while preserving structure, style, and length.

# Editing rules

- **Accurate claims:** Keep as-is.
- **Inaccurate claims:** Fix following the fact-checker's justification and sources.
- **Disputed claims:** Present both sides to make the answer balanced.
- **Unsupported claims:** Omit if not central, or soften to express uncertainty.
- **Not Applicable claims:** Keep as-is.

# Constraints

- Do NOT introduce new claims or statements.
- Edit minimally — maintain original structure and tone.
- If the answer is fully accurate, output it unchanged.
- After the revised answer, output exactly: ---END-OF-EDIT---

# Example

Question: What is the shape of the sun?
Answer: The sun is cube-shaped and very hot.

Findings:
- Claim 1: "The sun is cube-shaped" — Verdict: Inaccurate (NASA confirms it's a sphere)
- Claim 2: "The sun is very hot" — Verdict: Accurate

Your output:
The sun is sphere-shaped and very hot.
---END-OF-EDIT---

---

Here are the question-answer pair and the fact-checker's findings:
"""

# ── Callbacks ─────────────────────────────────────────────────────────────────

_END_OF_EDIT_MARK = "---END-OF-EDIT---"


def _remove_end_of_edit_mark(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse:
    """Strip the ---END-OF-EDIT--- marker and anything after it."""
    del callback_context
    if not llm_response.content or not llm_response.content.parts:
        return llm_response
    for idx, part in enumerate(llm_response.content.parts):
        if part.text and _END_OF_EDIT_MARK in part.text:
            del llm_response.content.parts[idx + 1:]
            part.text = part.text.split(_END_OF_EDIT_MARK, 1)[0]
    return llm_response


def _render_references_from_search(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse:
    """Append a References section built from raw Tavily search results.

    Scans the session history for the most recent web_search tool response
    and extracts title+URL pairs — replaces the Gemini-specific
    grounding_metadata approach from llm-auditor.
    """
    if not llm_response.content or not llm_response.content.parts:
        return llm_response

    # Find raw search results from the most recent web_search tool call
    references = []
    try:
        events = callback_context.session.events
        for event in reversed(events):
            if not (event.content and event.content.parts):
                continue
            for part in event.content.parts:
                if not part.text:
                    continue
                # Tool responses come as JSON strings with a 'results' list
                if '"results"' in part.text and '"url"' in part.text:
                    import json
                    try:
                        data = json.loads(part.text)
                        for r in data.get("results", []):
                            title = r.get("title", "")
                            url = r.get("url", "")
                            if title and url:
                                references.append(f"* [{title}]({url})")
                    except (json.JSONDecodeError, TypeError):
                        pass
    except Exception:
        pass

    if references:
        ref_text = "\n\n**References:**\n\n" + "\n".join(references)
        llm_response.content.parts.append(types.Part(text=ref_text))

    return llm_response


# ── Agents ────────────────────────────────────────────────────────────────────

critic_agent = Agent(
    name="critic",
    description="Extracts factual claims from an answer and verifies each via web search. Returns verdicts per claim with sources.",
    model=config.coordinator.to_model(),
    instruction=CRITIC_PROMPT,
    tools=[web_search_tool, web_extract_tool],
    after_model_callback=_render_references_from_search,
)

reviser_agent = Agent(
    name="reviser",
    description="Takes fact-checker verdicts and produces a minimally corrected version of the original answer.",
    model=config.coordinator.to_model(),
    instruction=REVISER_PROMPT,
    after_model_callback=_remove_end_of_edit_mark,
)

# ── Pipeline ──────────────────────────────────────────────────────────────────

fact_checker_agent = SequentialAgent(
    name="fact_checker",
    description=(
        "Verifies factual accuracy of a question-answer pair using web search, "
        "then produces a corrected version if needed. Invoke explicitly when a "
        "response contains factual claims that need verification."
    ),
    sub_agents=[critic_agent, reviser_agent],
)

fact_check_tool = AgentTool(agent=fact_checker_agent)
