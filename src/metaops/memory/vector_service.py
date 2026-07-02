import math
import re
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


# ── BM25 Okapi implementation ────────────────────────────────────────────────

class _BM25:
    """Lightweight BM25 scorer (Okapi variant) for keyword search."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    def score(self, query: str, doc: str, avg_dl: float, idf: dict[str, float]) -> float:
        q_tokens = self._tokenize(query)
        d_tokens = self._tokenize(doc)
        dl = len(d_tokens)
        tf_map: dict[str, int] = {}
        for t in d_tokens:
            tf_map[t] = tf_map.get(t, 0) + 1
        score = 0.0
        for qt in q_tokens:
            if qt not in idf:
                continue
            tf = tf_map.get(qt, 0)
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * dl / max(avg_dl, 1))
            score += idf[qt] * numerator / denominator
        return score


def _compute_idf(doc_freqs: dict[str, int], n_docs: int) -> dict[str, float]:
    """Compute IDF scores using the Okapi formula."""
    idf = {}
    for term, df in doc_freqs.items():
        idf[term] = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)
    return idf


def _bm25_search(query: str, documents: list[str], top_k: int = 10) -> list[tuple[int, float]]:
    """Run BM25 search over a list of documents. Returns (index, score) pairs sorted desc."""
    if not documents:
        return []

    scorer = _BM25()
    doc_token_lists = [scorer._tokenize(doc) for doc in documents]
    n_docs = len(documents)
    avg_dl = sum(len(d) for d in doc_token_lists) / max(n_docs, 1)

    doc_freqs: dict[str, int] = {}
    for tokens in doc_token_lists:
        seen = set()
        for t in tokens:
            if t not in seen:
                doc_freqs[t] = doc_freqs.get(t, 0) + 1
                seen.add(t)

    idf = _compute_idf(doc_freqs, n_docs)

    scored = []
    for i, doc in enumerate(documents):
        s = scorer.score(query, doc, avg_dl, idf)
        if s > 0:
            scored.append((i, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def _reciprocal_rank_fusion(
    result_lists: list[list[tuple[str, float]]],
    k: int = 60,
    weights: Optional[list[float]] = None,
) -> list[tuple[str, float]]:
    """Fuse multiple ranked result lists using Reciprocal Rank Fusion.

    Each result_list is [(doc_id, original_score), ...] sorted by relevance.
    Returns fused [(doc_id, rrf_score), ...] sorted descending.
    """
    if weights is None:
        weights = [1.0] * len(result_lists)
    rrf_scores: dict[str, float] = {}
    for w, results in zip(weights, result_lists):
        for rank, (doc_id, _) in enumerate(results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + w / (k + rank + 1)
    fused = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return fused


class HybridVectorMemoryService(BaseMemoryService):
    """MemOS-inspired Memory OS with ChromaDB Vector Cubes + BM25 hybrid search.

    Collections:
      - episodic_memory   — conversation history (searchable chunks)
      - semantic_memory   — ingested file dependencies (searchable chunks)
      - procedural_memory — learned skills (searchable chunks)
      - documents         — full document text (KV lookup, not embedded)
    """

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
        self.documents  = self.client.get_or_create_collection("documents")

    # ── Write ──────────────────────────────────────────────────────────────

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
            logger.info("Indexed %d episodic chunks from session %s", len(documents), session.id)

    async def add_events_to_memory(
        self,
        app_name: str,
        user_id: str,
        session_id: str,
        events: list,
    ) -> None:
        """Index only specific events (incremental) instead of the full session."""
        documents, metadatas, ids = [], [], []
        for event in events:
            if event.content and event.content.parts:
                text = "\n".join([p.text for p in event.content.parts if p.text])
                if text.strip():
                    documents.append(text)
                    metadatas.append({
                        "session_id": session_id,
                        "author": event.author,
                        "app_name": app_name,
                        "user_id": user_id,
                    })
                    ids.append(str(uuid.uuid4()))
        if documents:
            self.episodic.add(documents=documents, metadatas=metadatas, ids=ids)
            logger.info("Indexed %d episodic chunks from session %s", len(documents), session_id)

    async def add_document(
        self,
        doc_id: str,
        full_text: str,
        chunks: list[str],
        metadatas: list[dict],
        chunk_ids: list[str],
        collection_name: str = "semantic",
    ) -> None:
        """Store full document text + indexed chunks. Two-collection pattern."""
        self.documents.upsert(
            ids=[doc_id],
            documents=[full_text],
            metadatas=[{"collection": collection_name, "chunk_count": len(chunks)}],
        )
        collection = getattr(self, collection_name)
        collection.upsert(
            ids=chunk_ids,
            documents=chunks,
            metadatas=metadatas,
        )

    # ── Hybrid Search ──────────────────────────────────────────────────────

    async def search_memory(self, *, app_name: str, user_id: str, query: str) -> SearchMemoryResponse:
        """Hybrid search: semantic embeddings + BM25 keyword + RRF fusion."""
        results = []
        try:
            query_embeddings = self.embed_fn([query])
        except Exception as e:
            logger.warning("Failed to generate embedding for memory search: %s", e)
            return SearchMemoryResponse(memories=[])

        scope_filter = {"$and": [{"app_name": app_name}, {"user_id": user_id}]}

        for collection, label in [
            (self.episodic,   "Past Context"),
            (self.semantic,   "File Dependency"),
            (self.procedural, "Skill"),
        ]:
            try:
                # Semantic search
                sem_res = collection.query(
                    query_embeddings=query_embeddings,
                    n_results=5,
                    where=scope_filter,
                )
                sem_docs = sem_res["documents"][0] if sem_res["documents"] else []
                sem_ids = sem_res["ids"][0] if sem_res["ids"] else []

                # BM25 keyword search
                bm25_results = _bm25_search(query, sem_docs, top_k=5)
                bm25_list = [(sem_ids[i], score) for i, score in bm25_results]

                # Semantic results as (id, score) pairs
                sem_list = [(sem_ids[i], 1.0 / (i + 1)) for i in range(len(sem_ids))]

                # RRF fusion
                fused = _reciprocal_rank_fusion(
                    [sem_list, bm25_list],
                    weights=[0.6, 0.4],
                )

                # Collect top results
                doc_to_text = {sem_ids[i]: sem_docs[i] for i in range(len(sem_ids))}
                for doc_id, rrf_score in fused[:3]:
                    text = doc_to_text.get(doc_id, "")
                    if text:
                        results.append(MemoryEntry(
                            content=types.Content(parts=[types.Part(text=f"[{label}] {text}")]),
                            author="memory_system",
                        ))
            except Exception as exc:
                logger.debug("Search failed on collection %s: %s", label, exc)

        return SearchMemoryResponse(memories=results)

    async def get_document(self, doc_id: str) -> Optional[str]:
        """Retrieve full document text from the documents collection."""
        try:
            res = self.documents.get(ids=[doc_id])
            if res and res["documents"]:
                return res["documents"][0]
        except Exception:
            pass
        return None

    # ── Embedding-based Summarization ──────────────────────────────────────

    async def summarize_with_embeddings(
        self,
        text: str,
        max_chunks: int = 5,
    ) -> str:
        """Create an embedding-aware summary by clustering similar chunks.

        Uses embeddings to group semantically similar passages, then returns
        the most representative chunk from each cluster as a summary.
        """
        if not text.strip():
            return ""

        # Split into overlapping chunks
        chunk_size = 500
        overlap = 100
        chunks = []
        for i in range(0, len(text), chunk_size - overlap):
            chunk = text[i:i + chunk_size].strip()
            if chunk:
                chunks.append(chunk)

        if len(chunks) <= max_chunks:
            return "\n\n".join(chunks)

        # Generate embeddings for all chunks
        try:
            embeddings = self.embed_fn(chunks)
        except Exception:
            return "\n\n".join(chunks[:max_chunks])

        # Simple greedy clustering by cosine similarity
        clusters: list[list[int]] = []
        threshold = 0.7

        for i, emb in enumerate(embeddings):
            placed = False
            for cluster in clusters:
                # Compare with first element of cluster
                ref_emb = embeddings[cluster[0]]
                sim = _cosine_similarity(emb, ref_emb)
                if sim >= threshold:
                    cluster.append(i)
                    placed = True
                    break
            if not placed:
                clusters.append([i])

        # Pick the longest chunk from each cluster (most informative)
        summary_chunks = []
        for cluster in clusters:
            best_idx = max(cluster, key=lambda i: len(chunks[i]))
            summary_chunks.append(chunks[best_idx])

        return "\n\n".join(summary_chunks[:max_chunks])


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
