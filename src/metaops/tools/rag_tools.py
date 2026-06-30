import os
from google.adk.tools import FunctionTool, ToolContext
from metaops.memory.vector_service import HybridVectorMemoryService

_memory_service: HybridVectorMemoryService = None

def init_rag_tools(memory_service: HybridVectorMemoryService):
    global _memory_service
    _memory_service = memory_service

async def ingest_file_dependency(file_path: str, description: str, tool_context: ToolContext) -> dict:
    """
    Reads a local file, chunks it, and indexes it into the Semantic Memory cube.
    Use this to record file dependencies so the LLM can recall their contents later.
    """
    if not _memory_service:
        return {"status": "error", "message": "Memory service not initialized."}
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"File not found: {file_path}"}
        
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        
    chunk_size = 1000
    chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
    ids = [f"{os.path.basename(file_path)}_{i}" for i in range(len(chunks))]
    metadatas = [{"file": file_path, "description": description, "chunk": i} for i in range(len(chunks))]
    
    _memory_service.semantic.add(documents=chunks, metadatas=metadatas, ids=ids)
    return {"status": "success", "message": f"Indexed {len(chunks)} chunks from {file_path} into Semantic Memory."}

ingest_file_tool = FunctionTool(func=ingest_file_dependency)
