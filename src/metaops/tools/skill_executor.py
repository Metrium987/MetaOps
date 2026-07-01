import shlex
import logging
from google.adk.tools import FunctionTool, ToolContext
from metaops.backends.local import LocalTerminalBackend
from metaops.memory.database import MemoryDatabase
from metaops.tools._shell_guard import check_command_allowed

logger = logging.getLogger(__name__)

_db = MemoryDatabase()
_backend = LocalTerminalBackend()

async def execute_skill(skill_name: str, arguments: str = "", tool_context: ToolContext = None) -> dict:
    """Execute a previously learned and approved skill.

    Refuses skills with status 'pending_review' or 'rejected'.
    """
    await _db.initialize()
    skill = await _db.get_skill(skill_name)

    if not skill:
        return {"status": "error", "message": f"Skill '{skill_name}' not found."}

    if skill["status"] == "pending_review":
        return {
            "status": "error",
            "message": f"Skill '{skill_name}' is pending review. Approve it first with approve_skill(name='{skill_name}').",
        }

    if skill["status"] == "rejected":
        return {"status": "error", "message": f"Skill '{skill_name}' has been rejected."}

    procedure = skill["instructions"]

    # `arguments` is caller-supplied free text — shell-quote each token before
    # splicing it into the command string, otherwise shell metacharacters in
    # it (`;`, `|`, `$(...)`, backticks...) would be interpreted as part of
    # the shell command rather than literal argument text (shell injection).
    try:
        arg_tokens = shlex.split(arguments) if arguments else []
    except ValueError:
        return {"status": "error", "message": "Could not parse skill arguments."}
    safe_arguments = " ".join(shlex.quote(tok) for tok in arg_tokens)
    command = f"{procedure} {safe_arguments}".strip()

    user_role = "guest"
    if tool_context and tool_context.state:
        user_role = tool_context.state.get("user:role", "guest")

    error = check_command_allowed(command, user_role)
    if error:
        return {"status": "error", "message": error}

    output_buffer = []
    async for chunk in _backend.execute_stream(command):
        output_buffer.append(chunk)
    raw_output = "".join(output_buffer)
    # Check for explicit error markers rather than substring matching
    # (avoids false positives from "error handling" text in output).
    lower = raw_output.lower()
    status = "error" if any(marker in lower for marker in ["traceback", "exception:", "fatal error"]) else "success"
    return {"status": status, "summary": raw_output[-500:]}

skill_executor_tool = FunctionTool(func=execute_skill)

