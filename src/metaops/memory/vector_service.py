import chromadb
import uuid
import logging
from pathlib import Path
from typing import Optional
from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions import Session
from google.genai import types
from metaops.memory.embeddings import MetaOpsEmbeddingFunction

logger = logging.getLogger(__name__)

class HybridVectorMemoryService(BaseMemoryService):
    """MemOS-inspired Memory OS with distinct ChromaDB Vector Cubes."""

    def __init__(
        self,
        db_path: str = "./metaops_memory_db",
        embedding_provider: str = "local",
        embedding_model: str = "openai/text-embedding-3-small",
        embedding_api_key: Optional[str] = None,
        embedding_base_url: Optional[str] = None,
    ):
        Path(db_path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=db_path)
        self.embed_fn = MetaOpsEmbeddingFunction(
            provider=embedding_provider,
            model=embedding_model,
            api_key=embedding_api_key,
            base_url=embedding_base_url,
        )
        self.episodic  = self.client.get_or_create_collection("episodic_memory",   embedding_function=self.embed_fn)
        self.semantic  = self.client.get_or_create_collection("semantic_memory",   embedding_function=self.embed_fn)
        self.procedural = self.client.get_or_create_collection("procedural_memory", embedding_function=self.embed_fn)
        self.persona   = self.client.get_or_create_collection("persona_memory",    embedding_function=self.embed_fn)

    async def add_session_to_memory(self, session: Session) -> None:
        documents, metadatas, ids = [], [], []
        for event in session.events:
            if event.content and event.content.parts:
                text = "\n".join([p.text for p in event.content.parts if p.text])
                if text.strip():
                    documents.append(text)
                    metadatas.append({"session_id": session.id, "author": event.author})
                    ids.append(str(uuid.uuid4()))
        if documents:
            self.episodic.add(documents=documents, metadatas=metadatas, ids=ids)
            logger.info(f"Indexed {len(documents)} episodic chunks from session {session.id}")

    async def search_memory(self, *, app_name: str, user_id: str, query: str) -> SearchMemoryResponse:
        results = []
        for collection, label in [
            (self.episodic,   "Past Context"),
            (self.semantic,   "File Dependency"),
            (self.procedural, "Skill"),
        ]:
            try:
                res = collection.query(query_texts=[query], n_results=3)
                for doc in res['documents'][0]:
                    results.append(MemoryEntry(
                        content=types.Content(parts=[types.Part(text=f"[{label}] {doc}")]),
                        author="memory_system",
                    ))
            except Exception:
                pass
        return SearchMemoryResponse(memories=results)
