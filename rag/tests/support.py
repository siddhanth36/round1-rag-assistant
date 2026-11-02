"""Test-only helpers.

``HashingEmbedder`` is a deterministic, offline stand-in for the Sentence Transformers model
so vector-store tests (isolation, revisions, persistence) do not depend on downloading a
model. It hashes lowercase word tokens into a fixed-size normalized vector: texts sharing
words get closer vectors. It is NOT used outside tests; real-model behaviour is covered by
test_embedder.py and test_retrieval_integration.py.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from rag.exceptions import EmbeddingInputError

_TOKEN = re.compile(r"\w+")


class HashingEmbedder:
    def __init__(self, dimension: int = 384, model_name: str = "test-hashing-embedder") -> None:
        self._dimension = dimension
        self._model_name = model_name
        self.calls = 0  # number of embed_documents calls (to check batching)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts or any(not t.strip() for t in texts):
            raise EmbeddingInputError("empty input")
        self.calls += 1
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in _TOKEN.findall(text.lower()):
            bucket = int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "big") % self._dimension
            vector[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


# --- answer-generation fakes (no network, deterministic) -------------------------------------

import json  # noqa: E402
from collections.abc import Callable  # noqa: E402

from rag.schemas import Chunk, SearchResult  # noqa: E402

_PASSAGE = re.compile(r'<passage tag="(S\d+)"[^>]*>\n(.*?)\n</passage>', re.DOTALL)
_SENTENCE_END = re.compile(r"\.(?=\s|$)")


def search_result(chunk: Chunk, distance: float = 0.5) -> SearchResult:
    """Wrap a real chunk as a vector-search hit."""
    return SearchResult(
        chunk_id=chunk.chunk_id, chunk_index=chunk.chunk_index, machine_id=chunk.machine_id,
        document_id=chunk.document_id, revision=chunk.revision, source_filename=chunk.source_filename,
        pdf_page=chunk.pdf_page, text=chunk.text, cosine_distance=distance,
    )


class StubStore:
    """Returns fixed search results and records every call."""

    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.calls: list[tuple[str, str, int]] = []

    def search(self, query: str, *, machine_id: str, limit: int = 8) -> list[SearchResult]:
        self.calls.append((query, machine_id, limit))
        return self.results[:limit]


Reply = str | Exception | Callable[[str], str]


class ScriptedLLM:
    """Returns scripted replies in order (a string, an exception to raise, or fn(user_prompt))."""

    def __init__(self, *replies: Reply, model_name: str = "fake-llm") -> None:
        self._replies = list(replies)
        self._model_name = model_name
        self.prompts: list[tuple[str, str]] = []  # (system_prompt, user_prompt)

    @property
    def model_name(self) -> str:
        return self._model_name

    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        if not self._replies:
            raise AssertionError("ScriptedLLM called more times than scripted")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(user_prompt) if callable(reply) else reply


def passages_in_prompt(user_prompt: str) -> dict[str, str]:
    """tag -> passage text, as rendered by rag.generation.prompts.build_user_prompt."""
    return dict(_PASSAGE.findall(user_prompt))


def answer_json(*statements: tuple[str, list[str]]) -> str:
    return json.dumps({"status": "answered", "statements": [{"text": t, "sources": s} for t, s in statements]})


INSUFFICIENT_JSON = json.dumps({"status": "insufficient_evidence", "statements": []})


def extractive_reply(*needles: str) -> Callable[[str], str]:
    """Fake LLM behaviour: for each needle, quote the sentence starting at it from the first
    passage (in tag order) that contains it, citing that passage. Abstains if any needle is
    missing. Deterministic stand-in for a grounded LLM; it reads only the prompt it is given."""

    def reply(user_prompt: str) -> str:
        passages = passages_in_prompt(user_prompt)
        statements = []
        for needle in needles:
            hit = next(((tag, text) for tag, text in passages.items() if needle in text), None)
            if hit is None:
                return INSUFFICIENT_JSON
            tag, text = hit
            start = text.index(needle)
            end = _SENTENCE_END.search(text, start)  # "." followed by whitespace, so "6.5" is not an end
            sentence = " ".join(text[start : end.start() + 1 if end else len(text)].split())
            statements.append((sentence, [tag]))
        return answer_json(*statements)

    return reply
