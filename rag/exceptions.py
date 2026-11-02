"""Structured exceptions raised by the RAG engine."""


class RagError(Exception):
    """Base class for every error raised by the rag package."""


class InvalidMetadataError(RagError, ValueError):
    """Required source metadata (machine ID, document ID, revision, ...) is missing or malformed."""


class InvalidPdfError(RagError):
    """The input is missing, unreadable, not a PDF, corrupted, encrypted, or has no pages."""


class PdfExtractionError(RagError):
    """A valid PDF was opened but text extraction failed for a specific page."""

    def __init__(self, message: str, *, pdf_page: int | None = None) -> None:
        super().__init__(message)
        self.pdf_page = pdf_page


class ConfigError(RagError, ValueError):
    """A setting (usually read from the environment) is invalid."""


class ChunkingConfigError(ConfigError):
    """Chunk size / overlap settings are invalid."""


class EmbeddingInputError(RagError, ValueError):
    """Text passed for embedding is empty or not a string."""


class EmbeddingModelError(RagError):
    """The embedding model could not be loaded or produced output of the wrong shape."""


class InvalidChunkError(RagError, ValueError):
    """Chunks given to the vector store are empty, inconsistent, or mix sources."""


class VectorStoreError(RagError):
    """The vector store could not be opened or used, or returned an inconsistent result."""


class RevisionNotFoundError(VectorStoreError, LookupError):
    """The requested machine / document / revision has no indexed records."""


class LLMProviderError(RagError):
    """An LLM provider call failed (network, auth, rate limit, timeout, bad payload).

    Adapters must keep messages free of secrets (API keys, auth headers).
    """


class MalformedLLMResponseError(RagError, ValueError):
    """The LLM output could not be parsed into the required JSON structure."""
