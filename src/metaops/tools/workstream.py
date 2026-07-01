from google.adk.agents import Agent
from google.adk.tools import AgentTool, FunctionTool, LongRunningFunctionTool, ToolContext
from metaops.backends.local import LocalTerminalBackend
from metaops.config import get_config
from metaops.tools._shell_guard import check_command_allowed

config = get_config()
_backend = LocalTerminalBackend()

async def execute_workstream_command(command: str, tool_context: ToolContext = None) -> dict:
    """Execute a long-running shell pipeline and return its output."""
    user_role = "guest"
    if tool_context and tool_context.state:
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

_workstream_terminal_tool = LongRunningFunctionTool(func=execute_workstream_command)

workstream_executor = Agent(
    name="workstream_executor",
    description="Executes complex multi-step bash pipelines in an isolated context. Use for long-running tasks that would pollute the main context window. Returns a concise 1-sentence summary of the outcome.",
    model=config.workstream.to_model(),
    instruction="Execute complex bash pipelines. Return ONLY a 1-sentence summary.",
    tools=[_workstream_terminal_tool],
)

workstream_tool = AgentTool(agent=workstream_executor)
