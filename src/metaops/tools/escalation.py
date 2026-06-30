from google.adk.tools import FunctionTool, ToolContext

def request_human_approval(action_description: str, tool_context: ToolContext) -> dict:
    tool_context.actions.transfer_to_agent = "human_approver_agent"
    tool_context.state["temp:pending_approval"] = action_description
    return {"status": "paused", "message": "Transferring to approval gateway."}

escalation_tool = FunctionTool(func=request_human_approval)
