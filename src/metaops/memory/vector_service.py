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
        self.episodic   = self.client.get_or_create_collection("episodic_memory",   embedding_function=self.embed_fn)
        self.semantic   = self.client.get_or_create_collection("semantic_memory",   embedding_function=self.embed_fn)
        self.procedural = self.client.get_or_create_collection("procedural_memory", embedding_function=self.embed_fn)

    async def add_session_to_memory(self, session: Session) -> None:
        documents, metadatas, ids = [], [], []
        for event in session.events:
            if event.content and event.content.parts:
                text = "\n".join([p.text for p in event.content.parts if p.text])
                if text.strip():
                    documents.append(text)
                    metadatas.append({
                        "session_id": session.id,
                        "author": event.author,
                        "app_name": session.app_name,
                        "user_id": session.user_id,
                    })
                    ids.append(str(uuid.uuid4()))
        if documents:
            self.episodic.add(documents=documents, metadatas=metadatas, ids=ids)
            logger.info(f"Indexed {len(documents)} episodic chunks from session {session.id}")

    async def search_memory(self, *, app_name: str, user_id: str, query: str) -> SearchMemoryResponse:
        results = []
        try:
            # Pre-compute the query embedding once to avoid redundant API calls
            query_embeddings = self.embed_fn([query])
        except Exception as e:
            logger.warning("Failed to generate embedding for memory search: %s", e)
            return SearchMemoryResponse(memories=[])

        # Scope every collection query to this app/user — without this filter,
        # any user's recall_past_context/preload_memory call returns every
        # other user's indexed conversation history (cross-user data leak).
        scope_filter = {"$and": [{"app_name": app_name}, {"user_id": user_id}]}

        for collection, label in [
            (self.episodic,   "Past Context"),
            (self.semantic,   "File Dependency"),
            (self.procedural, "Skill"),
        ]:
            try:
                res = collection.query(
                    query_embeddings=query_embeddings,
                    n_results=3,
                    where=scope_filter,
                )
                for doc in res['documents'][0]:
                    results.append(MemoryEntry(
                        content=types.Content(parts=[types.Part(text=f"[{label}] {doc}")]),
                        author="memory_system",
                    ))
            except Exception:
                pass
        return SearchMemoryResponse(memories=results)
