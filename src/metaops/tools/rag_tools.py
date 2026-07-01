import os
from pathlib import Path
from google.adk.tools import FunctionTool, ToolContext
from metaops.memory.vector_service import HybridVectorMemoryService

_memory_service: HybridVectorMemoryService = None
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

def init_rag_tools(memory_service: HybridVectorMemoryService):
    global _memory_service
    _memory_service = memory_service

async def ingest_file_dependency(file_path: str, description: str, tool_context: ToolContext) -> dict:
    """
    Reads a local file, chunks it, and indexes it into the Semantic Memory cube.
    Use this to record file dependencies so the LLM can recall their contents later.
    """
    user_role = "guest"
    if tool_context and tool_context.state:
        user_role = tool_context.state.get("user:role", "guest")
    if user_role == "guest":
        return {"status": "error", "message": "Access denied: guests are not allowed to ingest file dependencies."}

    # Prevent directory traversal / out-of-workspace file leakage. resolve()
    # also collapses ".." segments and follows symlinks, so a symlink planted
    # inside the workspace that points outside it is caught too — a plain
    # string-prefix check (e.g. abs_path.startswith(workspace_root)) is not:
    # it would let "/workspace-evil/x" pass a check against "/workspace".
    workspace_root = _PROJECT_ROOT.resolve()
    try:
        abs_path = Path(file_path).resolve()
    except (OSError, RuntimeError):
        return {"status": "error", "message": f"Invalid file path: {file_path}"}
    if not abs_path.is_relative_to(workspace_root):
        return {"status": "error", "message": "Access denied: cannot ingest files outside the workspace directory."}

    if not _memory_service:
        return {"status": "error", "message": "Memory service not initialized."}
    if not abs_path.exists():
        return {"status": "error", "message": f"File not found: {file_path}"}

    content = abs_path.read_text(encoding='utf-8', errors='ignore')

    from metaops.config import get_config
    chunk_size = get_config().rag_chunk_size
    chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
    ids = [f"{os.path.basename(file_path)}_{i}" for i in range(len(chunks))]
    metadatas = [
        {"file": str(abs_path), "description": description, "chunk": i, "app_name": app_name, "user_id": user_id}
        for i in range(len(chunks))
    ]

    _memory_service.semantic.add(documents=chunks, metadatas=metadatas, ids=ids)
    return {"status": "success", "message": f"Indexed {len(chunks)} chunks from {file_path} into Semantic Memory."}

ingest_file_tool = FunctionTool(func=ingest_file_dependency)
