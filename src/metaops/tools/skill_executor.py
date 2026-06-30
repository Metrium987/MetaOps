import shlex
import os
from google.adk.tools import FunctionTool, ToolContext
from metaops.backends.local import LocalTerminalBackend
from metaops.memory.database import MemoryDatabase

_db = MemoryDatabase()
_backend = LocalTerminalBackend()

async def execute_skill(skill_name: str, arguments: str = "", tool_context: ToolContext = None) -> dict:
    """Executes a previously learned multi-step skill from the database."""
    procedure = await _db.get_skill_procedure(skill_name)
    if not procedure:
        return {"status": "error", "message": f"Skill '{skill_name}' not found."}
    
    command = f"{procedure} {arguments}".strip()
    
    user_role = "guest"
    if tool_context and tool_context.state:
        user_role = tool_context.state.get("user:role", "guest")

    if user_role != "admin":
        try:
            tokens = shlex.split(command)
            forbidden = {"rm", "sudo", "mkfs", "format", "dd"}
            for tok in tokens:
                base_tok = os.path.basename(tok.replace("\\", "/")).lower()
                if any(base_tok.startswith(f) for f in forbidden):
                    return {"status": "error", "message": "Insufficient permissions to execute sensitive commands in skill execution."}
        except ValueError:
            return {"status": "error", "message": "Skill command parsing failed. Rejected for security reasons."}

    output_buffer = []
    async for chunk in _backend.execute_stream(command):
        output_buffer.append(chunk)
    raw_output = "".join(output_buffer)
    status = "error" if "error" in raw_output.lower() else "success"
    return {"status": status, "summary": raw_output[-500:]}

skill_executor_tool = FunctionTool(func=execute_skill)

