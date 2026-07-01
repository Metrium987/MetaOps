from typing import List, Optional
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools import BaseTool, FunctionTool
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.tool_context import ToolContext
from metaops.backends.local import LocalTerminalBackend
from metaops.tools._shell_guard import check_command_allowed

_backend = LocalTerminalBackend()


async def execute_secure_command(command: str, tool_context: ToolContext) -> dict:
    user_role = tool_context.state.get("user:role", "guest")
    error = check_command_allowed(command, user_role)
    if error:
        return {"status": "error", "message": error}

    from metaops.config import get_config
    max_chars = get_config().tool_output_max_chars

    output = []
    async for chunk in _backend.execute_stream(command):
        output.append(chunk)
    return {"status": "success", "output": "".join(output)[-max_chars:]}

class SecureMetaOpsToolset(BaseToolset):
    def __init__(self):
        super().__init__()
        self._tool = FunctionTool(func=execute_secure_command)

    async def get_tools(self, readonly_context: Optional[ReadonlyContext] = None) -> List[BaseTool]:
        return [self._tool]

    async def close(self) -> None: pass
