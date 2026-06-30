from google.adk.tools import FunctionTool
from metaops.backends.local import LocalTerminalBackend

_backend = LocalTerminalBackend()

async def execute_terminal_command(command: str) -> dict:
    """Executes a shell command. Returns status and output."""
    output = []
    async for chunk in _backend.execute_stream(command):
        output.append(chunk)
    raw = "".join(output)
    status = "error" if "error" in raw.lower() else "success"
    return {"status": status, "output": raw[-2000:]}

terminal_tool = FunctionTool(func=execute_terminal_command)
