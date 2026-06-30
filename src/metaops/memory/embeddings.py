import os
from typing import Optional
from chromadb import EmbeddingFunction, Documents, Embeddings


class MetaOpsEmbeddingFunction(EmbeddingFunction):
    """Hybrid embedding provider for ChromaDB.

    - "local": ONNX bundled in ChromaDB (no key required, slow on first run)
    - "api":   OpenAI-compatible API (OpenRouter, OpenAI, etc.)
    """

    def __init__(
        self,
        provider: str = "local",
        model: str = "openai/text-embedding-3-small",
        api_key: Optional[str] = None,
        base_url: Optional[str] = "https://openrouter.ai/api/v1",
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.base_url = base_url
        self._local_model = None

    def _load_local_model(self):
        if self._local_model is None:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            self._local_model = DefaultEmbeddingFunction()

    def __call__(self, input: Documents) -> Embeddings:
        if self.provider == "local":
            self._load_local_model()
            return self._local_model(input)

        import openai
        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        response = client.embeddings.create(input=input, model=self.model)
        return [item.embedding for item in response.data]
