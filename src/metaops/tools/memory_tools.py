from google.adk.tools import FunctionTool, ToolContext
from google.genai import types


async def save_procedural_skill(name: str, code: str, tool_context: ToolContext) -> dict:
    """Saves a reusable procedure as a versioned ADK Artifact."""
    artifact_part = types.Part.from_text(text=code)
    version = await tool_context.save_artifact(f"skill_{name}", artifact_part)

    current_skills = tool_context.state.get("user:learned_skills", [])
    if name not in current_skills:
        current_skills.append(name)
    tool_context.state["user:learned_skills"] = current_skills

    return {"status": "success", "artifact_version": version}


async def recall_past_context(query: str, tool_context: ToolContext) -> dict:
    """Semantic search over past sessions via ADK native MemoryService."""
    memory_response = await tool_context.search_memory(query)

    context_snippets = []
    for memory in memory_response.memories:
        if memory.content and memory.content.parts:
            text = memory.content.parts[0].text
            if text:
                context_snippets.append(text)

    return {"status": "success", "snippets": context_snippets}


skill_saver_tool = FunctionTool(func=save_procedural_skill)
memory_search_tool = FunctionTool(func=recall_past_context)
