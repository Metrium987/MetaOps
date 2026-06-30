from typing import List, Optional
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools import BaseTool, FunctionTool
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.tool_context import ToolContext
from metaops.backends.local import LocalTerminalBackend

_backend = LocalTerminalBackend()

async def execute_secure_command(command: str, tool_context: ToolContext) -> dict:
    user_role = tool_context.state.get("user:role", "admin")
    if user_role != "admin" and any(cmd in command for cmd in ["rm ", "sudo ", "mkfs"]):
        return {"status": "error", "message": "Insufficient permissions."}
    
    output = []
    async for chunk in _backend.execute_stream(command):
        output.append(chunk)
    return {"status": "success", "output": "".join(output)[-2000:]}

class SecureMetaOpsToolset(BaseToolset):
    def __init__(self):
        super().__init__()
        self._tool = FunctionTool(func=execute_secure_command)

    async def get_tools(self, readonly_context: Optional[ReadonlyContext] = None) -> List[BaseTool]:
        return [self._tool]

    async def close(self) -> None: pass
