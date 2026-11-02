"""RAG configuration read from environment variables.

Every setting has a safe default, so nothing is required to run locally. Values come from the
process environment, plus the project's local ``.env`` file (git-ignored), loaded once at import;
variables already set in the environment win. The only secret is ``GROQ_API_KEY``; it is never
logged and is excluded from ``LLMConfig.__repr__``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from rag.exceptions import ChunkingConfigError, ConfigError

# Sizes are in whitespace-separated words. all-MiniLM-L6-v2 truncates input at
# 256 word-pieces, so ~180 words keeps a chunk (plus a short header) within limits.
DEFAULT_CHUNK_SIZE_WORDS = 180
DEFAULT_CHUNK_OVERLAP_WORDS = 30


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Chunk size and overlap, both measured in words."""

    chunk_size: int = DEFAULT_CHUNK_SIZE_WORDS
    overlap: int = DEFAULT_CHUNK_OVERLAP_WORDS

    def __post_init__(self) -> None:
        if self.chunk_size < 1:
            raise ChunkingConfigError(f"chunk_size must be >= 1, got {self.chunk_size}")
        if self.overlap < 0:
            raise ChunkingConfigError(f"overlap must be >= 0, got {self.overlap}")
        if self.overlap >= self.chunk_size:
            raise ChunkingConfigError(
                f"overlap ({self.overlap}) must be smaller than chunk_size ({self.chunk_size})"
            )


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _env_int(name: str, default: int, error: type[ConfigError] = ConfigError) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise error(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def load_chunking_config() -> ChunkingConfig:
    """Build a ChunkingConfig from ``RAG_CHUNK_SIZE_WORDS`` / ``RAG_CHUNK_OVERLAP_WORDS``."""
    return ChunkingConfig(
        chunk_size=_env_int("RAG_CHUNK_SIZE_WORDS", DEFAULT_CHUNK_SIZE_WORDS, ChunkingConfigError),
        overlap=_env_int("RAG_CHUNK_OVERLAP_WORDS", DEFAULT_CHUNK_OVERLAP_WORDS, ChunkingConfigError),
    )


# --- embeddings ------------------------------------------------------------------------------

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DEVICE = "cpu"
DEFAULT_EMBEDDING_BATCH_SIZE = 32


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Sentence Transformers settings.

    ``cache_dir`` overrides where model files are cached (default: the Hugging Face cache,
    ``~/.cache/huggingface``). Set ``HF_HUB_OFFLINE=1`` to forbid network access once cached.
    """

    model_name: str = DEFAULT_EMBEDDING_MODEL
    device: str = DEFAULT_EMBEDDING_DEVICE
    batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE
    cache_dir: str | None = None

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ConfigError("Embedding model name must not be empty")
        if self.batch_size < 1:
            raise ConfigError(f"Embedding batch size must be >= 1, got {self.batch_size}")


def load_embedding_config() -> EmbeddingConfig:
    """Build an EmbeddingConfig from ``EMBEDDING_MODEL``, ``EMBEDDING_DEVICE``,
    ``EMBEDDING_BATCH_SIZE`` and ``EMBEDDING_CACHE_DIR``."""
    return EmbeddingConfig(
        model_name=_env_str("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        device=_env_str("EMBEDDING_DEVICE", DEFAULT_EMBEDDING_DEVICE),
        batch_size=_env_int("EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE),
        cache_dir=_env_str("EMBEDDING_CACHE_DIR", "") or None,
    )


# --- vector store ----------------------------------------------------------------------------

# Project root (parent of the rag/ package). The default store lives in <root>/chroma_db/,
# outside the source packages and git-ignored.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path | None = None) -> bool:
    """Load ``KEY=VALUE`` lines from the local ``.env`` (default ``<project root>/.env``) into
    ``os.environ`` without overriding variables that are already set. Missing file = no-op.
    ``interpolate=False`` so secrets containing ``$`` are taken literally. Nothing is logged."""
    return load_dotenv(path or PROJECT_ROOT / ".env", override=False, interpolate=False)


load_env_file()
DEFAULT_CHROMA_DIR = PROJECT_ROOT / "chroma_db"
DEFAULT_CHROMA_COLLECTION = "manual_chunks"


@dataclass(frozen=True, slots=True)
class VectorStoreConfig:
    """Persistent ChromaDB location and collection name."""

    persist_dir: Path = DEFAULT_CHROMA_DIR
    collection_name: str = DEFAULT_CHROMA_COLLECTION

    def __post_init__(self) -> None:
        if not self.collection_name.strip():
            raise ConfigError("Chroma collection name must not be empty")


def load_vector_store_config() -> VectorStoreConfig:
    """Build a VectorStoreConfig from ``CHROMA_DIR`` and ``CHROMA_COLLECTION``."""
    return VectorStoreConfig(
        persist_dir=Path(_env_str("CHROMA_DIR", str(DEFAULT_CHROMA_DIR))).expanduser(),
        collection_name=_env_str("CHROMA_COLLECTION", DEFAULT_CHROMA_COLLECTION),
    )


# --- LLM provider ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """LLM provider settings. ``provider`` empty = no provider configured.

    ``api_key`` is excluded from ``repr`` so it never appears in logs or tracebacks.
    """

    provider: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if self.timeout_seconds < 1:
            raise ConfigError(f"LLM timeout must be >= 1 second, got {self.timeout_seconds}")


DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def load_llm_config() -> LLMConfig:
    """Build an LLMConfig from ``LLM_PROVIDER``, ``GROQ_MODEL``, ``GROQ_API_KEY``, ``LLM_TIMEOUT_SECONDS``."""
    return LLMConfig(
        provider=_env_str("LLM_PROVIDER", "").lower(),
        model=_env_str("GROQ_MODEL", DEFAULT_GROQ_MODEL),
        api_key=os.environ.get("GROQ_API_KEY", "").strip(),
        timeout_seconds=_env_int("LLM_TIMEOUT_SECONDS", 30),
    )


# --- answer generation ---------------------------------------------------------------------------

DEFAULT_RETRIEVAL_LIMIT = 8
# Provisional, measured on the supplied manuals (docs/PROJECT_ARCHITECTURE.md, "Abstention"):
# every expected source in the 13-question evaluation had cosine distance <= 0.663, and the
# best match for clearly off-topic questions was >= 0.862. Not a calibrated confidence.
DEFAULT_MAX_DISTANCE = 0.80
DEFAULT_MAX_CORRECTIONS = 1
DEFAULT_MAX_QUESTION_CHARS = 1000


@dataclass(frozen=True, slots=True)
class AnswerConfig:
    """Retrieval and abstention settings for answer_question().

    ``max_distance``: passages with a larger cosine distance are not sent to the LLM
    (``None`` disables the filter). ``max_corrections``: how many times the LLM is asked to
    fix a malformed or unsupported draft before giving up.
    """

    retrieval_limit: int = DEFAULT_RETRIEVAL_LIMIT
    max_distance: float | None = DEFAULT_MAX_DISTANCE
    max_corrections: int = DEFAULT_MAX_CORRECTIONS
    max_question_chars: int = DEFAULT_MAX_QUESTION_CHARS

    def __post_init__(self) -> None:
        if not 1 <= self.retrieval_limit <= 50:
            raise ConfigError(f"retrieval_limit must be between 1 and 50, got {self.retrieval_limit}")
        if self.max_distance is not None and not 0.0 < self.max_distance <= 2.0:
            raise ConfigError(f"max_distance must be in (0, 2] (cosine distance) or None, got {self.max_distance}")
        if self.max_corrections < 0:
            raise ConfigError(f"max_corrections must be >= 0, got {self.max_corrections}")
        if self.max_question_chars < 1:
            raise ConfigError(f"max_question_chars must be >= 1, got {self.max_question_chars}")


def load_answer_config() -> AnswerConfig:
    """Build an AnswerConfig from ``RAG_TOP_K``, ``RAG_MAX_DISTANCE`` (number or ``off``)
    and ``RAG_MAX_CORRECTIONS``."""
    raw_distance = _env_str("RAG_MAX_DISTANCE", "")
    if not raw_distance:
        max_distance: float | None = DEFAULT_MAX_DISTANCE
    elif raw_distance.lower() == "off":
        max_distance = None
    else:
        try:
            max_distance = float(raw_distance)
        except ValueError as exc:
            raise ConfigError(f"RAG_MAX_DISTANCE must be a number or 'off', got {raw_distance!r}") from exc
    return AnswerConfig(
        retrieval_limit=_env_int("RAG_TOP_K", DEFAULT_RETRIEVAL_LIMIT),
        max_distance=max_distance,
        max_corrections=_env_int("RAG_MAX_CORRECTIONS", DEFAULT_MAX_CORRECTIONS),
    )
