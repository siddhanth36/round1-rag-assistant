"""Sentence Transformers embedding component.

The model is loaded once per configuration (see :func:`get_embedder`) and reused for
every batch. Embeddings are L2-normalized so cosine distance in ChromaDB is meaningful.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from rag.config import EmbeddingConfig, load_embedding_config
from rag.exceptions import EmbeddingInputError, EmbeddingModelError

logger = logging.getLogger(__name__)


class TextEmbedder(Protocol):
    """What the vector store needs from an embedder."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerEmbedder:
    """Embeds text with a Sentence Transformers model loaded once at construction."""

    def __init__(self, config: EmbeddingConfig | None = None) -> None:
        self._config = config or load_embedding_config()
        self._model = _load_model(self._config)
        dimension = self._model.get_embedding_dimension()
        if not isinstance(dimension, int) or dimension < 1:
            raise EmbeddingModelError(
                f"Model {self._config.model_name!r} did not report a valid embedding dimension: {dimension!r}"
            )
        self._dimension = dimension
        logger.info("Loaded embedding model %s (dimension %d)", self._config.model_name, dimension)

    @property
    def model_name(self) -> str:
        return self._config.model_name

    @property
    def dimension(self) -> int:
        """Embedding size as reported by the loaded model (384 for all-MiniLM-L6-v2)."""
        return self._dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed many texts in batches of ``config.batch_size``. Order is preserved."""
        _validate_texts(texts)
        vectors = self._model.encode(
            list(texts),
            batch_size=self._config.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        if vectors.ndim != 2 or vectors.shape != (len(texts), self._dimension):
            raise EmbeddingModelError(
                f"Expected embeddings of shape ({len(texts)}, {self._dimension}), got {vectors.shape}"
            )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        return self.embed_documents([text])[0]


@lru_cache(maxsize=4)
def get_embedder(config: EmbeddingConfig | None = None) -> SentenceTransformerEmbedder:
    """Return a process-wide embedder for ``config`` (environment config if omitted).

    The model is loaded on the first call only; later calls with an equal config reuse it.
    """
    return SentenceTransformerEmbedder(config or load_embedding_config())


def _load_model(config: EmbeddingConfig):  # -> SentenceTransformer (imported lazily: heavy)
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(config.model_name, device=config.device, cache_folder=config.cache_dir)
    except Exception as exc:
        raise EmbeddingModelError(
            f"Could not load embedding model {config.model_name!r} on device {config.device!r}: {exc}. "
            "The first load downloads the model from Hugging Face (network needed) into the cache "
            "(EMBEDDING_CACHE_DIR or ~/.cache/huggingface); later loads work offline with HF_HUB_OFFLINE=1."
        ) from exc


def _validate_texts(texts: Sequence[str]) -> None:
    if isinstance(texts, str):
        raise EmbeddingInputError("embed_documents expects a sequence of strings, not a single string")
    if len(texts) == 0:
        raise EmbeddingInputError("Cannot embed an empty list of texts")
    for i, text in enumerate(texts):
        if not isinstance(text, str):
            raise EmbeddingInputError(f"Text at position {i} is {type(text).__name__}, expected str")
        if not text.strip():
            raise EmbeddingInputError(f"Text at position {i} is empty or whitespace")
