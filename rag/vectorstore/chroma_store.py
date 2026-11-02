"""Persistent ChromaDB store for manual chunks.

Design rules (see docs/PROJECT_ARCHITECTURE.md):

* One collection holds every machine's chunks; isolation comes from metadata filters that
  are applied **inside** ChromaDB's query, never by filtering results afterwards.
* Embeddings are always computed by our embedder and passed explicitly. The collection is
  opened with ``embedding_function=None`` and queried only with ``query_embeddings``, so
  ChromaDB's default embedding function is never used.
* Record ids are the deterministic chunk ids (``{document_id}:{revision}:p{page}:c{index}``),
  so re-indexing a revision overwrites instead of duplicating.
* ``is_current`` marks the single revision of each document that retrieval may return.
* The store is derived data: it can be deleted and rebuilt from the source PDFs.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Sequence
from typing import Any

import chromadb
from chromadb.config import Settings

from rag.config import VectorStoreConfig, load_vector_store_config
from rag.embeddings.embedder import TextEmbedder
from rag.exceptions import (
    InvalidChunkError,
    InvalidMetadataError,
    RevisionNotFoundError,
    VectorStoreError,
)
from rag.ingestion.chunker import make_chunk_id
from rag.schemas import Chunk, IndexSummary, SearchResult

logger = logging.getLogger(__name__)

# ChromaDB 1.5 serializes ``embedding_function=None`` as a "legacy" config and emits this
# DeprecationWarning whenever the collection config is read. There is no non-legacy way to
# declare "no embedding function"; the alternative (registering our model as a Chroma
# embedding function) would let Chroma embed text itself, which this design forbids.
# Silence exactly this message, only when raised from chromadb modules.
warnings.filterwarnings(
    "ignore",
    message="legacy embedding function config",
    category=DeprecationWarning,
    module=r"chromadb(\.|$)",
)

DISTANCE_SPACE = "cosine"
MAX_SEARCH_LIMIT = 50
_COLLECTION_MODEL_KEY = "embedding_model"
_COLLECTION_DIMENSION_KEY = "embedding_dimension"


class ChromaVectorStore:
    """Machine-isolated, revision-aware vector store backed by a persistent ChromaDB client."""

    def __init__(self, embedder: TextEmbedder, config: VectorStoreConfig | None = None) -> None:
        self._embedder = embedder
        self._config = config or load_vector_store_config()
        try:
            self._config.persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(self._config.persist_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name=self._config.collection_name,
                embedding_function=None,  # never use ChromaDB's default embedding function
                configuration={"hnsw": {"space": DISTANCE_SPACE}},
                metadata={
                    _COLLECTION_MODEL_KEY: embedder.model_name,
                    _COLLECTION_DIMENSION_KEY: embedder.dimension,
                },
            )
        except Exception as exc:
            raise VectorStoreError(
                f"Could not open ChromaDB collection {self._config.collection_name!r} "
                f"in {self._config.persist_dir}: {exc}"
            ) from exc
        self._check_collection_compatibility()

    # ------------------------------------------------------------------ info

    @property
    def collection_name(self) -> str:
        return self._config.collection_name

    def count(self) -> int:
        """Total number of stored chunk records (all machines, all revisions)."""
        return self._collection.count()

    # ------------------------------------------------------------------ indexing

    def index_manual(self, chunks: Sequence[Chunk], *, make_current: bool = True) -> IndexSummary:
        """Index all chunks of ONE document revision.

        * Validates that every chunk belongs to the same machine, document, revision and file,
          and that the document is not already indexed under a different machine.
        * Embeds all chunk texts in batches before writing anything (an embedding failure
          leaves the store untouched).
        * Upserts by deterministic chunk id, so re-indexing never duplicates records, and
          deletes records of this revision that the new chunking no longer produces.
        * Current-revision state: a revision that was already current stays current. With
          ``make_current=True`` (default) this revision becomes the current one for its
          document via :meth:`set_current_revision`; with ``False`` a new revision is stored
          but not retrievable until promoted.
        """
        machine_id, document_id, revision, source_filename = _validate_chunks(chunks)
        self._ensure_document_belongs_to_machine(document_id, machine_id)

        existing = self._collection.get(
            where=_where(machine_id=machine_id, document_id=document_id, revision=revision),
            include=["metadatas"],
        )
        was_current = any(m.get("is_current") is True for m in existing["metadatas"] or [])

        embeddings = self._embedder.embed_documents([c.text for c in chunks])
        new_ids = [c.chunk_id for c in chunks]
        stale_ids = sorted(set(existing["ids"]) - set(new_ids))

        try:
            self._collection.upsert(
                ids=new_ids,
                embeddings=embeddings,
                documents=[c.text for c in chunks],
                metadatas=[_chunk_metadata(c, is_current=was_current) for c in chunks],
            )
            if stale_ids:
                self._collection.delete(ids=stale_ids)
        except Exception as exc:
            raise VectorStoreError(f"Failed to write {document_id} rev {revision}: {exc}") from exc

        if make_current:
            self.set_current_revision(machine_id=machine_id, document_id=document_id, revision=revision)

        is_current = make_current or was_current
        logger.info(
            "Indexed %s rev %s (%s): %d chunks, %d stale removed, current=%s",
            document_id, revision, machine_id, len(chunks), len(stale_ids), is_current,
        )
        return IndexSummary(
            machine_id=machine_id,
            document_id=document_id,
            revision=revision,
            source_filename=source_filename,
            chunks_indexed=len(chunks),
            pages_indexed=tuple(sorted({c.pdf_page for c in chunks})),
            stale_records_removed=len(stale_ids),
            is_current=is_current,
            embedding_model=self._embedder.model_name,
        )

    def remove_manual(self, *, machine_id: str, document_id: str, revision: str | None) -> int:
        """Delete indexed records and return how many were removed.

        ``revision`` is required and explicit: pass a revision label to remove only that
        revision, or ``None`` to remove every revision of the document. The machine id is
        always part of the filter, so another machine's records can never be deleted.
        Removing the current revision leaves the document with no current revision.
        """
        filters = {"machine_id": machine_id, "document_id": document_id}
        if revision is not None:
            filters["revision"] = revision
        where = _where(**_require_non_empty(**filters))
        ids = self._collection.get(where=where, include=[])["ids"]
        if ids:
            self._collection.delete(ids=ids)
        logger.info("Removed %d records for %s rev %s (%s)", len(ids), document_id, revision or "*", machine_id)
        return len(ids)

    def set_current_revision(self, *, machine_id: str, document_id: str, revision: str) -> int:
        """Make ``revision`` the only current revision of ``document_id`` on ``machine_id``.

        Only records of that machine + document are touched; other documents and machines are
        unaffected. Other revisions are demoted *before* the target is promoted, so a reader
        can briefly see no current revision but never two. Returns the number of records whose
        ``is_current`` flag changed.

        Raises:
            RevisionNotFoundError: the machine/document/revision has no indexed records
                (nothing is changed in that case).
        """
        filters = _require_non_empty(machine_id=machine_id, document_id=document_id, revision=revision)
        records = self._collection.get(
            where=_where(machine_id=filters["machine_id"], document_id=filters["document_id"]),
            include=["metadatas"],
        )
        target, demote = [], []
        for record_id, meta in zip(records["ids"], records["metadatas"] or [], strict=True):
            if meta["revision"] == filters["revision"]:
                if meta.get("is_current") is not True:
                    target.append(record_id)
            elif meta.get("is_current") is True:
                demote.append(record_id)

        if not any(m["revision"] == filters["revision"] for m in records["metadatas"] or []):
            raise RevisionNotFoundError(
                f"No indexed records for machine {machine_id!r}, document {document_id!r}, revision {revision!r}"
            )
        if demote:
            self._collection.update(ids=demote, metadatas=[{"is_current": False}] * len(demote))
        if target:
            self._collection.update(ids=target, metadatas=[{"is_current": True}] * len(target))
        return len(demote) + len(target)

    # ------------------------------------------------------------------ retrieval

    def search(self, query: str, *, machine_id: str, limit: int = 8) -> list[SearchResult]:
        """Return up to ``limit`` current-revision chunks of ``machine_id`` closest to ``query``.

        The filter ``machine_id == <machine> AND is_current == True`` is passed to ChromaDB's
        query, so other machines' chunks and superseded revisions are excluded during the
        search itself. Results are ordered by ascending cosine distance.
        """
        machine_id = _require_non_empty(machine_id=machine_id)["machine_id"]
        if not isinstance(query, str) or not query.strip():
            raise InvalidMetadataError("query must be a non-empty string")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise InvalidMetadataError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}, got {limit}")

        query_embedding = self._embedder.embed_query(query)
        try:
            raw = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                where=_where(machine_id=machine_id, is_current=True),
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise VectorStoreError(f"Vector search failed for machine {machine_id!r}: {exc}") from exc

        results = [
            _to_search_result(record_id, text, meta, distance)
            for record_id, text, meta, distance in zip(
                raw["ids"][0], raw["documents"][0], raw["metadatas"][0], raw["distances"][0], strict=True
            )
        ]
        # Defence in depth only: the filter above already guarantees this.
        leaked = [r.chunk_id for r in results if r.machine_id != machine_id]
        if leaked:
            raise VectorStoreError(f"Machine isolation violated for {machine_id!r}: {leaked}")
        return results

    # ------------------------------------------------------------------ internals

    def _check_collection_compatibility(self) -> None:
        """Refuse to mix embeddings from different models / metrics in one collection."""
        meta = self._collection.metadata or {}
        stored_model = meta.get(_COLLECTION_MODEL_KEY)
        stored_dim = meta.get(_COLLECTION_DIMENSION_KEY)
        if stored_model != self._embedder.model_name or stored_dim != self._embedder.dimension:
            raise VectorStoreError(
                f"Collection {self.collection_name!r} was built with model {stored_model!r} "
                f"(dimension {stored_dim!r}) but the configured embedder is "
                f"{self._embedder.model_name!r} (dimension {self._embedder.dimension}). "
                "Use a different CHROMA_COLLECTION / CHROMA_DIR or delete and rebuild the index."
            )
        space = (self._collection.configuration or {}).get("hnsw", {}).get("space")
        if space != DISTANCE_SPACE:
            raise VectorStoreError(
                f"Collection {self.collection_name!r} uses distance {space!r}, expected {DISTANCE_SPACE!r}"
            )

    def _ensure_document_belongs_to_machine(self, document_id: str, machine_id: str) -> None:
        other = self._collection.get(
            where={"$and": [{"document_id": document_id}, {"machine_id": {"$ne": machine_id}}]},
            limit=1,
            include=["metadatas"],
        )
        if other["ids"]:
            owner = other["metadatas"][0]["machine_id"]
            raise InvalidChunkError(
                f"Document {document_id!r} is already indexed for machine {owner!r}; "
                f"it cannot also be indexed for {machine_id!r}"
            )


def _validate_chunks(chunks: Sequence[Chunk]) -> tuple[str, str, str, str]:
    """Check chunks form one consistent document revision; return its identity."""
    if not chunks:
        raise InvalidChunkError("No chunks to index")
    first = chunks[0]
    identity = (first.machine_id, first.document_id, first.revision, first.source_filename)
    _require_non_empty(
        machine_id=first.machine_id, document_id=first.document_id,
        revision=first.revision, source_filename=first.source_filename,
    )
    seen: set[str] = set()
    for chunk in chunks:
        if (chunk.machine_id, chunk.document_id, chunk.revision, chunk.source_filename) != identity:
            raise InvalidChunkError(
                f"Chunks mix sources: {identity} vs "
                f"{(chunk.machine_id, chunk.document_id, chunk.revision, chunk.source_filename)}"
            )
        if chunk.pdf_page < 1:
            raise InvalidChunkError(f"Chunk {chunk.chunk_id!r} has invalid pdf_page {chunk.pdf_page}")
        if not chunk.text.strip():
            raise InvalidChunkError(f"Chunk {chunk.chunk_id!r} has empty text")
        expected_id = make_chunk_id(chunk.document_id, chunk.revision, chunk.pdf_page, chunk.chunk_index)
        if chunk.chunk_id != expected_id:
            raise InvalidChunkError(f"Chunk id {chunk.chunk_id!r} does not match its source ({expected_id!r})")
        if chunk.chunk_id in seen:
            raise InvalidChunkError(f"Duplicate chunk id {chunk.chunk_id!r}")
        seen.add(chunk.chunk_id)
    return identity


def _require_non_empty(**values: str) -> dict[str, str]:
    for name, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise InvalidMetadataError(f"{name} must be a non-empty string, got {value!r}")
    return values


def _where(**equals: str | bool) -> dict[str, Any]:
    """Build a ChromaDB ``where`` filter requiring every given field to equal its value."""
    clauses = [{key: value} for key, value in equals.items()]
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _chunk_metadata(chunk: Chunk, *, is_current: bool) -> dict[str, str | int | bool]:
    return {
        "chunk_id": chunk.chunk_id,
        "chunk_index": chunk.chunk_index,
        "machine_id": chunk.machine_id,
        "document_id": chunk.document_id,
        "revision": chunk.revision,
        "source_filename": chunk.source_filename,
        "pdf_page": chunk.pdf_page,
        "is_current": is_current,
    }


def _to_search_result(record_id: str, text: str, meta: dict[str, Any], distance: float) -> SearchResult:
    return SearchResult(
        chunk_id=record_id,
        chunk_index=int(meta["chunk_index"]),
        machine_id=meta["machine_id"],
        document_id=meta["document_id"],
        revision=meta["revision"],
        source_filename=meta["source_filename"],
        pdf_page=int(meta["pdf_page"]),
        text=text,
        cosine_distance=float(distance),
    )
