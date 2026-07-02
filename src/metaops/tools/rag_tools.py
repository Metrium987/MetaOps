import os
from pathlib import Path
from google.adk.tools import FunctionTool, ToolContext
from metaops.memory.vector_service import HybridVectorMemoryService

_memory_service: HybridVectorMemoryService = None
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

def init_rag_tools(memory_service: HybridVectorMemoryService):
    global _memory_service
    _memory_service = memory_service

_context_summarizer_agent = None

def _get_context_summarizer():
    global _context_summarizer_agent
    if _context_summarizer_agent is None:
        from google.adk.agents import Agent
        from metaops.config import get_config
        _context_summarizer_agent = Agent(
            name="context_summarizer",
            model=get_config().auditor.to_model(),
            instruction=(
                "You are an assistant that summarizes the global context of a file. "
                "Given the name of a file and its entire contents, write a single concise sentence "
                "(max 50 words) that describes what the file is about. "
                "Output ONLY the single sentence. Do not include any headers, markdown, or formatting."
            )
        )
    return _context_summarizer_agent

async def ingest_file_dependency(file_path: str, description: str, tool_context: ToolContext) -> dict:
    """
    Reads a local file, chunks it, and indexes it into the Semantic Memory cube with Contextual Chunking.
    Use this to record file dependencies so the LLM can recall their contents later.
    """
    user_role = "guest"
    if tool_context and tool_context.state:
        user_role = tool_context.state.get("user:role", "guest")
    if user_role == "guest":
        return {"status": "error", "message": "Access denied: guests are not allowed to ingest file dependencies."}

    # Prevent directory traversal / out-of-workspace file leakage. resolve()
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

    from metaops.memory.parsers import parse_file
    content = parse_file(str(abs_path))
    if not content:
        content = abs_path.read_text(encoding='utf-8', errors='ignore')

    app_name = tool_context.session.app_name if tool_context and tool_context.session else "metaops"
    user_id = tool_context.session.user_id if tool_context and tool_context.session else "user"

    filename = os.path.basename(file_path)
    # Generate the global context summary using the auditor model
    prompt = f"File Name: {filename}\nDescription: {description}\n\nContent:\n{content[:15000]}"
    try:
        from metaops.workflows.agent_runner import run_agent_once
        agent = _get_context_summarizer()
        global_context = await run_agent_once(agent, prompt)
        global_context = global_context.strip().replace("\n", " ")
    except Exception as exc:
        global_context = description or f"Technical source file {filename}"

    from metaops.config import get_config
    chunk_size = get_config().rag_chunk_size
    chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
    
    # Prepend context to each chunk (Contextual Chunking)
    enriched_chunks = []
    for chunk in chunks:
        enriched_chunks.append(
            f"<document_context>\n"
            f"File: {filename}\n"
            f"Summary: {global_context}\n"
            f"</document_context>\n\n"
            f"{chunk}"
        )

    ids = [f"{filename}_{i}" for i in range(len(enriched_chunks))]
    metadatas = [
        {
            "file": str(abs_path),
            "description": description,
            "chunk": i,
            "app_name": app_name,
            "user_id": user_id,
            "global_context": global_context
        }
        for i in range(len(enriched_chunks))
    ]

    _memory_service.semantic.add(documents=enriched_chunks, metadatas=metadatas, ids=ids)

    # Record the ingestion metadata in unified SQLite database
    try:
        from metaops.memory.database import get_db
        db = await get_db()
        file_size = len(content)
        chunk_count = len(enriched_chunks)
        await db.execute("""
            INSERT OR REPLACE INTO rag_sources (
                file_path, filename, description, global_context, file_size, chunk_count, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (str(abs_path), filename, description, global_context, file_size, chunk_count))
        await db.commit()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Failed to record RAG source in SQLite: %s", exc)

    return {"status": "success", "message": f"Indexed {len(enriched_chunks)} chunks from {file_path} into Semantic Memory."}

ingest_file_tool = FunctionTool(func=ingest_file_dependency)
