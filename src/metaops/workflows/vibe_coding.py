import uuid
import logging
from google.adk.agents import Agent
from google.adk.workflow import Workflow
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.adk.tools import FunctionTool
from google.genai import types
from metaops.config import MetaOpsConfig

logger = logging.getLogger(__name__)

config = MetaOpsConfig()

# Maximum reviewer → coder cycles before giving up and returning the best attempt
MAX_REVISIONS = 3


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

_CODER_INSTRUCTION = """You are an expert software engineer. Your job is to write correct, clean code.

IMPORTANT — read the full conversation before writing:
- If this is the first message, write the code from scratch based on the task.
- If a reviewer has already provided feedback (look for "VERDICT: NEEDS_WORK" above),
  you MUST fix every issue in the numbered list. Start your response with:
  "Addressing reviewer feedback:" followed by a brief acknowledgment of each fix.

Output format:
```<language>
<your code here>
```
Then: a one-paragraph explanation of what the code does and any key decisions made."""

_REVIEWER_INSTRUCTION = """You are a strict but fair code reviewer.

Review the latest code in the conversation. Check for:
1. Correctness — does it match what was asked?
2. Bugs — logic errors, off-by-one, unhandled exceptions, edge cases
3. Security — injections, unsafe evals, exposed secrets, path traversal
4. Quality — unclear naming, unnecessary complexity, missing error handling

If the code is correct and safe, approve it.
If there are real issues, reject it with a specific numbered list.

You MUST end your response with EXACTLY one of these two lines (no variations):
VERDICT: APPROVED
VERDICT: NEEDS_WORK"""

coder_agent = Agent(
    name="coder_agent",
    model=config.workstream.to_litellm(),
    instruction=_CODER_INSTRUCTION,
)

reviewer_agent = Agent(
    name="reviewer_agent",
    model=config.workstream.to_litellm(),
    instruction=_REVIEWER_INSTRUCTION,
)


# ---------------------------------------------------------------------------
# Router — reads the reviewer verdict, loops back or ends
# ---------------------------------------------------------------------------

def review_router(node_input: str, ctx):
    """Route back to coder_agent on rejection, or end the workflow on approval.

    Tracks iteration count in ctx.state to enforce MAX_REVISIONS.
    When the limit is reached the last attempt is returned regardless of verdict.
    """
    text = str(node_input)
    iterations = ctx.state.get("coding:iterations", 0)

    approved = "VERDICT: APPROVED" in text
    at_limit = iterations >= MAX_REVISIONS

    if approved or at_limit:
        if at_limit and not approved:
            logger.warning(
                "Vibe coding reached max revisions (%d) without approval. "
                "Returning last attempt.",
                MAX_REVISIONS,
            )
        ctx.state["coding:iterations"] = 0
        return  # no yield → terminal, output returned to user

    ctx.state["coding:iterations"] = iterations + 1
    logger.info(
        "Reviewer rejected — revision %d/%d", iterations + 1, MAX_REVISIONS
    )
    yield Event(route="coder_agent")


# ---------------------------------------------------------------------------
# Workflow graph
#
#   START → coder_agent → reviewer_agent → review_router
#                ↑                               ↓ (NEEDS_WORK, under limit)
#                └───────────────────────────────┘
#
# The shared conversation context means the coder sees the reviewer's
# numbered issues on every subsequent pass — no extra state injection needed.
# ---------------------------------------------------------------------------

vibe_coding_workflow = Workflow(
    name="vibe_coding",
    edges=[
        ("START", coder_agent, reviewer_agent, review_router),
        (review_router, {"coder_agent": coder_agent}),
    ],
)


# ---------------------------------------------------------------------------
# FunctionTool wrapper — called by the coordinator like any other tool
# ---------------------------------------------------------------------------

async def vibe_code(task: str) -> dict:
    """Write code with automatic review and correction loop.

    Spawns a coder agent, then a reviewer. If the reviewer rejects the code,
    the coder revises it. Repeats up to MAX_REVISIONS times.

    Args:
        task: Full description of the coding task, including language, context,
              constraints, and any examples.

    Returns:
        dict with keys:
            code     — the final (approved or best) code block
            approved — True if the reviewer approved, False if max retries hit
            revisions — number of reviewer → coder cycles that ran
    """
    runner = Runner(
        node=vibe_coding_workflow,
        app_name="metaops_vibe_coding",
        session_service=InMemorySessionService(),
        artifact_service=InMemoryArtifactService(),
    )

    session = await runner.session_service.create_session(
        app_name="metaops_vibe_coding",
        user_id="vibe_coder",
        session_id=str(uuid.uuid4()),
    )

    output_parts: list[str] = []
    async for event in runner.run_async(
        user_id="vibe_coder",
        session_id=session.id,
        new_message=types.Content(parts=[types.Part(text=task)]),
    ):
        if event.content:
            for part in event.content.parts or []:
                if part.text:
                    output_parts.append(part.text)

    final_output = "\n".join(output_parts)
    approved = "VERDICT: APPROVED" in final_output or (
        # If the last event is from coder_agent (max retries hit), it's not approved
        "VERDICT:" not in final_output
    )
    revisions = session.state.get("coding:iterations", 0)

    return {
        "code": final_output,
        "approved": approved,
        "revisions": revisions,
    }


vibe_coding_tool = FunctionTool(func=vibe_code)
