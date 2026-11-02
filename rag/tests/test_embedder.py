"""Tests for rag.embeddings.embedder with the real all-MiniLM-L6-v2 model.

The model is loaded from the local Hugging Face cache; the first run on a machine needs
network access to download it (~90 MB). If it cannot be loaded, these tests are skipped
with the loading error as the reason — they are never reported as passed.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from rag.config import (
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingConfig,
    load_embedding_config,
)
from rag.embeddings.embedder import SentenceTransformerEmbedder, get_embedder
from rag.exceptions import ConfigError, EmbeddingInputError, EmbeddingModelError

EXPECTED_MINILM_DIMENSION = 384  # documented model property; verified against the loaded model below


@pytest.fixture(scope="module")
def embedder() -> SentenceTransformerEmbedder:
    try:
        return get_embedder(EmbeddingConfig())
    except EmbeddingModelError as exc:
        pytest.skip(f"Embedding model unavailable: {exc}")


def test_model_loads_with_configured_name(embedder: SentenceTransformerEmbedder) -> None:
    assert embedder.model_name == DEFAULT_EMBEDDING_MODEL == "sentence-transformers/all-MiniLM-L6-v2"


def test_dimension_is_reported_by_the_loaded_model(embedder: SentenceTransformerEmbedder) -> None:
    assert embedder.dimension == EXPECTED_MINILM_DIMENSION
    assert len(embedder.embed_query("hydraulic pressure")) == embedder.dimension


def test_batch_embedding_of_real_chunks(embedder: SentenceTransformerEmbedder, manual_chunks) -> None:
    texts = [c.text for chunks in manual_chunks.values() for c in chunks]
    vectors = embedder.embed_documents(texts)
    assert len(vectors) == len(texts)
    for vector in vectors:
        assert len(vector) == embedder.dimension
        assert all(isinstance(v, float) for v in vector)
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-4)  # normalized


def test_batch_order_matches_individual_embeddings(embedder: SentenceTransformerEmbedder, manual_chunks) -> None:
    texts = [c.text for c in manual_chunks["AC-OM-01"][:3]]
    batch = embedder.embed_documents(texts)
    for text, batch_vector in zip(texts, batch, strict=True):
        single = embedder.embed_query(text)
        assert max(abs(a - b) for a, b in zip(single, batch_vector, strict=True)) < 1e-4


def test_small_batch_size_gives_same_vectors(embedder: SentenceTransformerEmbedder, manual_chunks) -> None:
    texts = [c.text for c in manual_chunks["HP-MNT-01"]]
    small = SentenceTransformerEmbedder(EmbeddingConfig(batch_size=2))
    for a, b in zip(small.embed_documents(texts), embedder.embed_documents(texts), strict=True):
        assert max(abs(x - y) for x, y in zip(a, b, strict=True)) < 1e-4


@pytest.mark.parametrize("bad", [[], [""], ["valid", "   "], [None], "a plain string"])
def test_empty_or_invalid_input_is_rejected(embedder: SentenceTransformerEmbedder, bad) -> None:
    with pytest.raises(EmbeddingInputError):
        embedder.embed_documents(bad)


def test_blank_query_is_rejected(embedder: SentenceTransformerEmbedder) -> None:
    with pytest.raises(EmbeddingInputError):
        embedder.embed_query("  ")


def test_get_embedder_reuses_the_loaded_model(embedder: SentenceTransformerEmbedder) -> None:
    assert get_embedder(EmbeddingConfig()) is embedder


def test_missing_model_raises_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-model"
    with pytest.raises(EmbeddingModelError, match="Could not load embedding model"):
        SentenceTransformerEmbedder(EmbeddingConfig(model_name=str(missing)))


def test_embedding_config_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL", "some/model")
    monkeypatch.setenv("EMBEDDING_DEVICE", "cpu")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "8")
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", "/tmp/models")
    assert load_embedding_config() == EmbeddingConfig("some/model", "cpu", 8, "/tmp/models")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "0")
    with pytest.raises(ConfigError):
        load_embedding_config()
