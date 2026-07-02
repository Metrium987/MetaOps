"""Vibe coding — coder + reviewer loop with automatic revision.

Simple Python loop (no Workflow graph needed):
  1. Coder writes code from the task spec
  2. Reviewer checks for correctness, bugs, security
  3. If rejected, coder revises with reviewer feedback
  4. Repeat up to MAX_REVISIONS times

Exposed as FunctionTool for the coordinator agent.
"""

import logging
import re
from pathlib import Path
from google.adk.agents import Agent
from google.adk.code_executors import UnsafeLocalCodeExecutor
from google.adk.tools import FunctionTool, ToolContext
from metaops.config import get_config
from metaops.workflows.agent_runner import run_agent_once

logger = logging.getLogger(__name__)

config = get_config()


def _get_max_revisions() -> int:
    return config.max_revisions


# ---------------------------------------------------------------------------
# SEARCH/REPLACE diff utilities
# ---------------------------------------------------------------------------

_SEARCH_REPLACE_PATTERN = re.compile(
    r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
    re.DOTALL,
)


def apply_search_replace_diffs(text: str) -> list[dict]:
    """Parse SEARCH/REPLACE diff blocks from agent output.

    Returns list of dicts with 'search' and 'replace' keys.
    """
    diffs = []
    for match in _SEARCH_REPLACE_PATTERN.finditer(text):
        diffs.append({
            "search": match.group(1),
            "replace": match.group(2),
        })
    return diffs


def apply_diffs_to_file(file_path: str, diffs: list[dict]) -> tuple[bool, str]:
    """Apply SEARCH/REPLACE diffs to a file. Returns (success, message)."""
    try:
        path = Path(file_path)
        if not path.exists():
            return False, f"File not found: {file_path}"

        content = path.read_text(encoding="utf-8")
        original = content

        for i, d in enumerate(diffs):
            search = d["search"]
            replace = d["replace"]
            if search in content:
                content = content.replace(search, replace, 1)
                logger.info("Applied diff %d to %s", i + 1, file_path)
            else:
                logger.warning("Diff %d search block not found in %s", i + 1, file_path)

        if content != original:
            path.write_text(content, encoding="utf-8")
            return True, f"Applied {len(diffs)} diff(s) to {file_path}"
        return True, "No changes needed"

    except Exception as e:
        return False, f"Error applying diffs: {e}"


def extract_code_blocks(text: str) -> list[dict]:
    """Extract fenced code blocks from agent output.

    Returns list of dicts with 'language' and 'code' keys.
    """
    pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    blocks = []
    for match in pattern.finditer(text):
        lang = match.group(1) or "unknown"
        code = match.group(2).strip()
        if code:
            blocks.append({"language": lang, "code": code})
    return blocks


def extract_diffs_and_code(text: str) -> dict:
    """Extract both SEARCH/REPLACE diffs and code blocks from agent output.

    Returns dict with 'diffs' and 'code_blocks' keys.
    """
    return {
        "diffs": apply_search_replace_diffs(text),
        "code_blocks": extract_code_blocks(text),
    }


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

_CODER_INSTRUCTION = """You are an expert SWE (Software Engineering) agent. Your goal is to write correct, minimal, clean, and production-ready code to resolve a specific issue.

Follow these strict rules:
1. **Understand and Plan**: Analyze the task and reviewer feedback carefully. Before writing any code, list the exact files and lines of code you need to change.
2. **Surgical Scope**: Modify ONLY the lines of code necessary to solve the issue. Do not rewrite unrelated blocks or change spacing/style outside the target block.
3. **No Placeholders**: Write COMPLETE, runnable code. Never use TODOs, comments like "# rest of code...", or ellipses "...".
4. **Addressing Feedback**: If reviewer feedback is provided, you must address EVERY point. Start your response with a brief section "Addressing Reviewer Feedback:" explaining what was corrected.
5. **Output Format**: Use SEARCH/REPLACE diff format for editing existing files:
   <<<<<<< SEARCH
   <exact lines to find>
   =======
   <replacement lines>
   >>>>>>> REPLACE

   For new files, use standard fenced code blocks:
   ```<language>
   <your code>
   ```

   Followed by a concise explanation of the design choices and how edge cases were handled."""

_REVIEWER_INSTRUCTION = """You are a strict, production-level code reviewer. Your job is to verify that the generated code is correct, secure, and ready to deploy.

Critically analyze the code against the task requirements. You must check:
1. **Correctness & Logic**: Does the code fully implement the requested logic? Are there off-by-one errors, boundary issues, or null/empty exceptions?
2. **Surgical Precision**: Does the code limit modifications to the target scope? (Flag unnecessary code churn or rewrites of stable code).
3. **Security & Reliability**: Check for common vulnerabilities (SQL injection, unsafe eval, path traversal, resource leaks).
4. **Completeness**: Are there placeholders, TODOs, or missing imports?

Provide constructive, detailed feedback for any issues found. If and only if the code has zero issues, approve it.

You MUST end your response with EXACTLY one of:
VERDICT: APPROVED
VERDICT: NEEDS_WORK"""

coder_agent = Agent(
    name="coder_agent",
    model=config.coder.to_model(),
    instruction=_CODER_INSTRUCTION,
    code_executor=UnsafeLocalCodeExecutor(),
)

reviewer_agent = Agent(
    name="reviewer_agent",
    model=config.workstream.to_model(),
    instruction=_REVIEWER_INSTRUCTION,
)


# ---------------------------------------------------------------------------
# FunctionTool wrapper
# ---------------------------------------------------------------------------

async def vibe_code(task: str, tool_context: ToolContext = None) -> dict:
    """Write code with automatic review and correction loop.

    Spawns a coder agent, then a reviewer. If the reviewer rejects,
    the coder revises using the feedback. Repeats up to MAX_REVISIONS.

    Args:
        task: Full description of the coding task including language,
              framework, constraints, and any examples.
        tool_context: Optional ToolContext for role-based access control.

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

    for revision in range(_get_max_revisions()):
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
            revision + 1, _get_max_revisions(), review.strip()[:500],
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
        _get_max_revisions(),
    )
    return {
        "code": coder_output,
        "approved": False,
        "revisions": _get_max_revisions(),
        "last_review_feedback": review,
    }


vibe_coding_tool = FunctionTool(func=vibe_code)
