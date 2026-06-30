from typing import List, Optional
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools import BaseTool, FunctionTool
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.tool_context import ToolContext
from metaops.backends.local import LocalTerminalBackend

_backend = LocalTerminalBackend()

import shlex

import os

async def execute_secure_command(command: str, tool_context: ToolContext) -> dict:
    user_role = tool_context.state.get("user:role", "guest")
    if user_role != "admin":
        try:
            tokens = shlex.split(command)
            forbidden = {"rm", "sudo", "mkfs", "format", "dd"}
            for tok in tokens:
                base_tok = os.path.basename(tok.replace("\\", "/")).lower()
                if any(base_tok.startswith(f) for f in forbidden):
                    return {"status": "error", "message": "Insufficient permissions to execute sensitive commands."}
        except ValueError:
            return {"status": "error", "message": "Command parsing failed. Rejected for security reasons."}
    
    
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
