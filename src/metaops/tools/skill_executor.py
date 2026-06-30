from google.adk.tools import FunctionTool
from metaops.backends.local import LocalTerminalBackend
from metaops.memory.database import MemoryDatabase

_db = MemoryDatabase()
_backend = LocalTerminalBackend()

async def execute_skill(skill_name: str, arguments: str = "") -> dict:
    """Executes a previously learned multi-step skill from the database."""
    procedure = await _db.get_skill_procedure(skill_name)
    if not procedure:
        return {"status": "error", "message": f"Skill '{skill_name}' not found."}
    command = f"{procedure} {arguments}".strip()
    output_buffer = []
    async for chunk in _backend.execute_stream(command):
        output_buffer.append(chunk)
    raw_output = "".join(output_buffer)
    status = "error" if "error" in raw_output.lower() else "success"
    return {"status": status, "summary": raw_output[-500:]}

skill_executor_tool = FunctionTool(func=execute_skill)
