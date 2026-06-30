"""Full dev cycle — plan → code (with review) → optional tests.

Three-stage pipeline:
  Stage 1: Architect agent produces a step-by-step implementation plan
  Stage 2: vibe_code implements the plan with automatic review loop
  Stage 3: (Optional) Run tests and report results

Exposed as FunctionTool for the coordinator agent.
"""

import os
import shlex
import logging
from google.adk.agents import Agent
from google.adk.tools import FunctionTool, ToolContext
from metaops.config import MetaOpsConfig
from metaops.workflows.agent_runner import run_agent_once
from metaops.workflows.vibe_coding import vibe_code

logger = logging.getLogger(__name__)
config = MetaOpsConfig()

_PLANNER_INSTRUCTION = """You are a software architect. Turn a feature request into a concrete, actionable implementation plan.

## Implementation Plan
1. [File path] — what to create or change (be specific about class/function names)
2. ...

## Constraints
- Language, framework, patterns to follow
- Files NOT to touch
- External APIs or services involved

## Test Strategy
- Commands to verify correctness
- Edge cases that must be handled

Be specific. Every step must be actionable by a developer who hasn't seen the codebase.
Do not write any code — plans only."""

_planner_agent = Agent(
    name="planner",
    model=config.coordinator.to_litellm(),
    instruction=_PLANNER_INSTRUCTION,
)


async def _run_shell(command: str) -> str:
    """Execute a shell command and return its output (last 4000 chars)."""
    from metaops.backends.local import LocalTerminalBackend
    backend = LocalTerminalBackend()
    chunks: list[str] = []
    async for chunk in backend.execute_stream(command):
        chunks.append(chunk)
    return "".join(chunks)[-4000:]


async def full_dev_cycle(
    task: str,
    run_tests: bool = False,
    test_command: str = "python -m pytest -q",
    tool_context: ToolContext = None,
) -> dict:
    """Plan, implement (with review loop), and optionally test a coding task.

    Stage 1 — Plan: architect produces a step-by-step implementation plan.
    Stage 2 — Code: vibe-coder implements the plan with automatic review
               and correction loop (up to 3 revisions).
    Stage 3 — Test (optional): runs the provided test command and reports results.

    Args:
        task: Full description of what to build. Include language, framework,
              file paths if known, and any constraints.
        run_tests: Set to True to run tests after implementation.
        test_command: Shell command to run tests. Default: python -m pytest -q
        tool_context: ToolContext for inspecting role-based access.

    Returns:
        dict with keys:
            plan       — implementation plan produced by the architect
            code       — final code (approved or best attempt)
            approved   — True if reviewer approved the code
            revisions  — number of coder→reviewer cycles that ran
            test_output — test command output (only present if run_tests=True)
    """
    logger.info("Full dev cycle started: %s", task[:80])

    # Enforce security validation on test command at the start
    if run_tests:
        user_role = "guest"
        if tool_context and tool_context.state:
            user_role = tool_context.state.get("user:role", "guest")

        if user_role != "admin":
            try:
                tokens = shlex.split(test_command)
                forbidden = {"rm", "sudo", "mkfs", "format", "dd"}
                for tok in tokens:
                    base_tok = os.path.basename(tok.replace("\\", "/")).lower()
                    if any(base_tok.startswith(f) for f in forbidden):
                        return {
                            "plan": "Blocked",
                            "code": "",
                            "approved": False,
                            "revisions": 0,
                            "test_output": "Error: Insufficient permissions to execute sensitive commands.",
                            "tests_passed": False,
                        }
            except ValueError:
                return {
                    "plan": "Blocked",
                    "code": "",
                    "approved": False,
                    "revisions": 0,
                    "test_output": "Error: Test command parsing failed.",
                    "tests_passed": False,
                }

    # Stage 1: Plan
    plan = await run_agent_once(_planner_agent, task)
    logger.info("Planning complete (%d chars)", len(plan))

    # Stage 2: Implement with review loop
    combined_task = f"{task}\n\n---\nImplementation Plan:\n{plan}"
    code_result = await vibe_code(task=combined_task)
    logger.info(
        "Coding complete — approved=%s revisions=%d",
        code_result["approved"],
        code_result["revisions"],
    )

    result = {
        "plan": plan,
        "code": code_result["code"],
        "approved": code_result["approved"],
        "revisions": code_result["revisions"],
    }

    # Stage 3: Tests (optional)
    if run_tests:
        logger.info("Running tests: %s", test_command)
        test_output = await _run_shell(test_command)
        result["test_output"] = test_output
        passed = "passed" in test_output.lower() or "ok" in test_output.lower()
        failed = "failed" in test_output.lower() or "error" in test_output.lower()
        result["tests_passed"] = passed and not failed
        logger.info("Tests %s", "passed" if result["tests_passed"] else "failed/inconclusive")

    return result


full_dev_cycle_tool = FunctionTool(func=full_dev_cycle)
