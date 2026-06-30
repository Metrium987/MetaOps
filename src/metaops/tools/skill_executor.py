import shlex
from google.adk.tools import FunctionTool, ToolContext
from metaops.backends.local import LocalTerminalBackend
from metaops.memory.database import MemoryDatabase
from metaops.tools._shell_guard import check_command_allowed

_db = MemoryDatabase()
_backend = LocalTerminalBackend()

async def execute_skill(skill_name: str, arguments: str = "", tool_context: ToolContext = None) -> dict:
    """Executes a previously learned multi-step skill from the database."""
    procedure = await _db.get_skill_procedure(skill_name)
    if not procedure:
        return {"status": "error", "message": f"Skill '{skill_name}' not found."}

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
    status = "error" if "error" in raw_output.lower() else "success"
    return {"status": status, "summary": raw_output[-500:]}

skill_executor_tool = FunctionTool(func=execute_skill)

