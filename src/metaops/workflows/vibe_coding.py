"""Vibe coding — coder + reviewer loop with automatic revision.

Simple Python loop (no Workflow graph needed):
  1. Coder writes code from the task spec
  2. Reviewer checks for correctness, bugs, security
  3. If rejected, coder revises with reviewer feedback
  4. Repeat up to MAX_REVISIONS times

Exposed as FunctionTool for the coordinator agent.
"""

import logging
from google.adk.agents import Agent
from google.adk.tools import FunctionTool
from metaops.config import MetaOpsConfig
from metaops.workflows.agent_runner import run_agent_once

logger = logging.getLogger(__name__)

config = MetaOpsConfig()

MAX_REVISIONS = 3

# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

_CODER_INSTRUCTION = """You are an expert software engineer. Write correct, clean, production-ready code.

Rules:
- Read the FULL prompt carefully before writing anything.
- If reviewer feedback is included, fix EVERY issue listed. Start with:
  "Addressing reviewer feedback:" then acknowledge each fix briefly.
- Output format: wrap code in a fenced block with the language tag:
  ```<language>
  <your code>
  ```
  Then add a 1-paragraph explanation of what the code does.
- Write COMPLETE, runnable code. No placeholders, no TODOs, no "..."."""

_REVIEWER_INSTRUCTION = """You are a strict but fair code reviewer.

Review the code against the original task requirements. Check:
1. Correctness — does it do what was asked?
2. Bugs — logic errors, off-by-one, unhandled edge cases
3. Security — injections, unsafe evals, exposed secrets
4. Completeness — is anything missing from the requirements?

If the code is correct and complete → approve it.
If there are real issues → reject with a specific numbered list of problems.

You MUST end your response with EXACTLY one of:
VERDICT: APPROVED
VERDICT: NEEDS_WORK"""

coder_agent = Agent(
    name="coder_agent",
    model=config.workstream.to_model(),
    instruction=_CODER_INSTRUCTION,
)

reviewer_agent = Agent(
    name="reviewer_agent",
    model=config.workstream.to_model(),
    instruction=_REVIEWER_INSTRUCTION,
)


# ---------------------------------------------------------------------------
# FunctionTool wrapper
# ---------------------------------------------------------------------------

async def vibe_code(task: str) -> dict:
    """Write code with automatic review and correction loop.

    Spawns a coder agent, then a reviewer. If the reviewer rejects,
    the coder revises using the feedback. Repeats up to MAX_REVISIONS.

    Args:
        task: Full description of the coding task including language,
              framework, constraints, and any examples.

    Returns:
        dict with keys:
            code     — the final code block (approved or best attempt)
            approved — True only if the reviewer explicitly approved
            revisions — number of coder→reviewer cycles that ran
            last_review_feedback — reviewer's last rejection reasons
                (only present when approved is False)
    """
    # Step 1: Initial code generation
    coder_output = await run_agent_once(coder_agent, task)
    logger.info("Coder produced initial code (%d chars)", len(coder_output))

    for revision in range(MAX_REVISIONS):
        # Step 2: Review
        review_prompt = (
            f"## Original Task\n{task}\n\n"
            f"## Code to Review\n{coder_output}"
        )
        review = await run_agent_once(reviewer_agent, review_prompt)

        if "VERDICT: APPROVED" in review:
            logger.info("Reviewer approved at revision %d", revision)
            return {
                "code": coder_output,
                "approved": True,
                "revisions": revision,
            }

        # Step 3: Revise based on feedback
        logger.info(
            "Reviewer rejected — revision %d/%d — feedback: %s",
            revision + 1, MAX_REVISIONS, review.strip()[:500],
        )
        revision_prompt = (
            f"## Original Task\n{task}\n\n"
            f"## Your Previous Code\n{coder_output}\n\n"
            f"## Reviewer Feedback (fix ALL issues)\n{review}"
        )
        coder_output = await run_agent_once(coder_agent, revision_prompt)

    # Max revisions reached without approval
    logger.warning(
        "Vibe coding reached max revisions (%d) without approval. "
        "Returning last attempt.",
        MAX_REVISIONS,
    )
    return {
        "code": coder_output,
        "approved": False,
        "revisions": MAX_REVISIONS,
        "last_review_feedback": review,
    }


vibe_coding_tool = FunctionTool(func=vibe_code)
