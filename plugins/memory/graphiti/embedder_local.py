"""Local embedder via ``fastembed`` — no API key required.

Graphiti-core ships embedders for OpenAI / Azure / Gemini / Voyage but
none for fully-local inference.  This adapter wraps
``fastembed.TextEmbedding`` (CPU-friendly ONNX models — bge, jina,
nomic, etc.) and exposes the ``EmbedderClient`` ABC.

When ``memory.graphiti.embedding_provider == "local"`` the plugin
constructs a ``FastembedEmbedder`` and hands it to ``Graphiti(embedder=
...)``.  No ``OPENAI_API_KEY`` is needed for embeddings in this mode —
the only place an LLM key still matters is entity extraction (and
that's handled separately via the ``llm_*`` config keys, which point
at any OpenAI-compatible endpoint including Ollama / vLLM / LiteLLM).

fastembed downloads the model weights on first use (~100-300 MB) and
caches them under its default cache dir.  Embedding inference is
synchronous; we wrap it in ``asyncio.to_thread`` so the bg event loop
doesn't block.

Default model is bge-m3 (1024-dim, multilingual, strong general
performance).  Override via ``embedding_model`` config key.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any, List, Optional

from graphiti_core.embedder.client import EmbedderClient

logger = logging.getLogger(__name__)


_DEFAULT_MODEL = "BAAI/bge-m3"

# fastembed accepts a handful of model name shapes; normalize a few
# common aliases users are likely to type.  Unknown names pass through
# unchanged — fastembed will validate.
_MODEL_ALIASES = {
    "bge-m3": "BAAI/bge-m3",
    "bge-small-en": "BAAI/bge-small-en-v1.5",
    "bge-small": "BAAI/bge-small-en-v1.5",
    "bge-base-en": "BAAI/bge-base-en-v1.5",
    "bge-large-en": "BAAI/bge-large-en-v1.5",
    "jina-v2-small": "jinaai/jina-embeddings-v2-small-en",
    "jina-v2-base": "jinaai/jina-embeddings-v2-base-en",
    "nomic-v1": "nomic-ai/nomic-embed-text-v1",
    "nomic-v1.5": "nomic-ai/nomic-embed-text-v1.5",
}


def _resolve_model(name: Optional[str]) -> str:
    n = (name or _DEFAULT_MODEL).strip()
    return _MODEL_ALIASES.get(n, n)


# Known embedding dimensions for resolved model names.  Avoids loading
# a 100-300 MB model just to discover an integer that is well-known for
# every model in our alias table.
_MODEL_DIMS: dict[str, int] = {
    "BAAI/bge-m3": 1024,
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    "jinaai/jina-embeddings-v2-small-en": 512,
    "jinaai/jina-embeddings-v2-base-en": 768,
    "nomic-ai/nomic-embed-text-v1": 768,
    "nomic-ai/nomic-embed-text-v1.5": 768,
}


class FastembedEmbedder(EmbedderClient):
    """Local CPU/GPU embedder backed by fastembed.

    Lazy-loads the model on first use so that simply constructing the
    embedder is cheap (~milliseconds; just records the model name).
    Loading the model itself happens inside the first ``create`` call,
    inside ``asyncio.to_thread`` so it doesn't block the event loop.
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        cache_dir: Optional[str] = None,
        threads: Optional[int] = None,
    ) -> None:
        self.model_name = _resolve_model(model)
        self._cache_dir = cache_dir
        self._threads = threads
        self._model: Any = None      # fastembed.TextEmbedding (lazy)
        self._dim: Optional[int] = None
        self._load_lock = asyncio.Lock()

    async def _ensure_loaded(self) -> Any:
        """Lazy-load the fastembed model under a lock.

        First call downloads weights (~100-300 MB) and may take 5–30 s
        depending on network.  Concurrent callers wait on the lock so
        we don't kick off two downloads in parallel.
        """
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:           # double-checked
                return self._model
            self._model = await asyncio.to_thread(self._construct_model)
        return self._model

    def _construct_model(self) -> Any:
        try:
            from fastembed import TextEmbedding  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "fastembed is not installed; install with "
                "`uv pip install 'intellect-agent[graphiti,fastembed]'` "
                "or configure embedding_provider: openai instead"
            ) from exc
        kwargs: dict[str, Any] = {"model_name": self.model_name}
        if self._cache_dir:
            kwargs["cache_dir"] = self._cache_dir
        if self._threads:
            kwargs["threads"] = self._threads
        logger.info(
            "graphiti: loading fastembed model %s (first call may "
            "download weights)",
            self.model_name,
        )
        return TextEmbedding(**kwargs)

    @property
    def embedding_dim(self) -> int:
        """Embedding dimensionality.

        Returns a known dimension for models in our alias table (avoids
        loading a 100-300 MB model for a single integer).  For custom /
        unknown models, probes via a 1-string warm-up call and caches the
        result.
        """
        if self._dim is not None:
            return self._dim

        # Known dimension?  Return it immediately — no model load needed.
        known = _MODEL_DIMS.get(self.model_name)
        if known is not None:
            self._dim = known
            return known

        # Unknown model — fall through to probing.
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async call; refuse to block.  Return
                # a conservative default; create() will discover the
                # real dimension on first use.
                return 1024
        except RuntimeError:
            pass
        sample = asyncio.run(self.create("probe"))
        self._dim = len(sample)
        return self._dim

    async def create(
        self,
        input_data: str | List[str] | Iterable[int] | Iterable[Iterable[int]],
    ) -> List[float]:
        """Embed a single text and return its vector.

        graphiti-core's contract: ``create`` returns ONE vector even if
        a list is passed — it uses ``create_batch`` for true batching.
        Lists arriving here happen to be single-item; we take the first.
        """
        model = await self._ensure_loaded()
        text = self._coerce_to_text(input_data)
        vectors = await asyncio.to_thread(_embed_one, model, text)
        if self._dim is None:
            self._dim = len(vectors)
        return vectors

    async def create_batch(self, input_data_list: List[str]) -> List[List[float]]:
        model = await self._ensure_loaded()
        if not input_data_list:
            return []
        vectors = await asyncio.to_thread(_embed_batch, model, input_data_list)
        if self._dim is None and vectors:
            self._dim = len(vectors[0])
        return vectors

    @staticmethod
    def _coerce_to_text(
        input_data: str | List[str] | Iterable[int] | Iterable[Iterable[int]],
    ) -> str:
        """graphiti-core sometimes passes a list-of-one or a token
        sequence; coerce to a single string so fastembed accepts it.
        """
        if isinstance(input_data, str):
            return input_data
        if isinstance(input_data, list):
            if not input_data:
                return ""
            first = input_data[0]
            if isinstance(first, str):
                return first
            # token-id sequences — fastembed can't consume these; we
            # fall back to a stringified form so the call doesn't crash.
            return str(first)
        return str(input_data)


def _embed_one(model: Any, text: str) -> List[float]:
    # fastembed.embed returns a generator of np.ndarrays.
    for vec in model.embed([text]):
        return [float(x) for x in vec]
    return []


def _embed_batch(model: Any, texts: List[str]) -> List[List[float]]:
    out: List[List[float]] = []
    for vec in model.embed(texts):
        out.append([float(x) for x in vec])
    return out
