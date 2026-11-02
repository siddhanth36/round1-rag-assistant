# Project Architecture — INDUSTRIX AI

Status: **Design (pre-implementation)**. Requirements source: `challenge-pack/Round1_Student_Challenge_Brief.pdf` v1.0.

## 1. System overview

A modular-monolith Django application with an embedded, framework-independent RAG package.

```
┌────────────────────────── Browser ───────────────────────────┐
│ Django templates · HTML5 · CSS3 · Bootstrap 5 · vanilla JS   │
│ (fetch → JSON APIs, CSRF token, PDF opened at #page=N)       │
└───────────────┬──────────────────────────────────────────────┘
                │ HTTP(S), session cookie
┌───────────────▼──────────────────────────────────────────────┐
│ Django                                                       │
│  ├─ accounts   : login/logout, custom User(role)             │
│  ├─ machines   : Machine, Manual, IngestionJob, PDF serving  │
│  ├─ assistant  : Question, Answer, Citation, history         │
│  ├─ DRF API    : serializers, role permission classes        │
│  └─ services   : ingest_manual(), ask_question()  ◄── orchestration + persistence
│                       │                                      │
│               ┌───────▼──────── rag/ (no Django imports) ───┐│
│               │ ingestion/pdf_extractor.py  PyMuPDF pages   ││
│               │ ingestion/chunker.py  page-bounded chunks   ││
│               │ embeddings/embedder.py  Sentence Transformers││
│               │ vectorstore/chroma_store.py  ChromaDB +     ││
│               │              machine-filtered search        ││
│               │ generation/llm_client.py  provider iface    ││
│               │ generation/prompts.py  grounded prompt      ││
│               │ generation/citation_validator.py            ││
│               │ generation/answer_service.py answer_question││
│               └─────────────────────────────────────────────┘│
└───────┬──────────────────────────┬───────────────────────────┘
        │                          │
┌───────▼────────┐      ┌──────────▼──────────┐     ┌───────────────┐
│ PostgreSQL     │      │ ChromaDB (on disk)  │     │ External LLM  │
│ source of truth│      │ derived vector index│     │ API           │
└────────────────┘      └─────────────────────┘     └───────────────┘
        ▲
   MEDIA_ROOT/manuals/  (uploaded PDF files, served only via auth view)
```

The `rag/` side of the diagram reflects the implemented modules; the Django side is still a proposal. The boundary is the public interface in `docs/RAG_INTEGRATION_CONTRACT.md` (no `rag/service.py` facade was built).

### `rag/` package structure

```
rag/
├── __init__.py
├── config.py              # env-driven settings: chunking, embeddings, vector store   [implemented]
├── schemas.py             # frozen dataclasses: ExtractedPage, ExtractedDocument, Chunk,
│                          #   IndexSummary, SearchResult                               [implemented]
├── exceptions.py          # RagError hierarchy (InvalidPdfError, EmbeddingModelError,
│                          #   VectorStoreError, RevisionNotFoundError, ...)            [implemented]
├── ingestion/
│   ├── pdf_extractor.py   # extract_pdf(): PyMuPDF, one-based page text + metadata    [implemented]
│   └── chunker.py         # chunk_document()/chunk_page(): page-bounded chunks        [implemented]
├── embeddings/
│   └── embedder.py        # SentenceTransformerEmbedder, get_embedder() (cached load) [implemented]
├── vectorstore/
│   └── chroma_store.py    # ChromaVectorStore: index/remove/set_current_revision/search [implemented]
├── generation/
│   ├── llm_client.py      # LLMClient protocol, GroqClient adapter, create_llm_client()      [implemented]
│   ├── prompts.py         # system prompt, tagged-evidence user prompt, correction prompt   [implemented]
│   ├── citation_validator.py # parse LLM JSON, validate tags/claims against evidence     [implemented]
│   └── answer_service.py  # answer_question(): retrieval -> abstention -> LLM -> validation [implemented]
├── evaluation.py          # `python -m rag.evaluation`: 13-question retrieval evaluation  [implemented]
├── tests/                 # pytest suite (real manuals; temp ChromaDB dirs; fake LLMs)    [implemented]
├── requirements.txt       # runtime deps for rag/
├── requirements-dev.txt   # + pytest
```

### Embeddings (implemented)

- Model `sentence-transformers/all-MiniLM-L6-v2` (env `EMBEDDING_MODEL`), CPU by default (`EMBEDDING_DEVICE`).
- **Dimension 384**, read from the loaded model (`get_embedding_dimension()`) and checked on every batch; max input 256 word-pieces.
- `get_embedder()` loads the model once per configuration and reuses it. `embed_documents()` encodes a list in batches of `EMBEDDING_BATCH_SIZE` (default 32); vectors are L2-normalized and returned as `list[list[float]]`.
- Empty lists, blank strings and non-strings raise `EmbeddingInputError`; load failures raise `EmbeddingModelError` with download/cache guidance.
- Model files live in the Hugging Face cache (`~/.cache/huggingface`, or `EMBEDDING_CACHE_DIR`). The first load downloads ~90 MB; afterwards `HF_HUB_OFFLINE=1` works.

### Vector store (implemented)

- `chromadb.PersistentClient` at `CHROMA_DIR` (default `<project root>/chroma_db/`, git-ignored), collection `CHROMA_COLLECTION` (default `manual_chunks`), HNSW **cosine** space, telemetry off.
- Opened with `embedding_function=None` and queried only with `query_embeddings`: ChromaDB's default embedding function is never used. The collection records `embedding_model` and `embedding_dimension`; reopening it with a different model is refused (rebuild instead of mixing vectors).
- Record id = chunk id `{document_id}:{revision}:p{pdf_page}:c{chunk_index}`; the record's document is the full chunk text.
- Record metadata (ChromaDB types: str/int/bool):

| Key | Type | Example |
|---|---|---|
| chunk_id | str | `HP-OPS-01:1.0:p2:c1` |
| chunk_index | int | `1` (position within the page) |
| machine_id | str | `M-HP200` |
| document_id | str | `HP-OPS-01` |
| revision | str | `1.0` |
| source_filename | str | `HP200_Operations_Rev1_0.pdf` |
| pdf_page | int | `2` (original one-based PDF page) |
| is_current | bool | `true` |

Operations (`ChromaVectorStore`):

| Method | Behaviour |
|---|---|
| `index_manual(chunks, make_current=True)` | Validates one consistent machine/document/revision/file, rejects a document already indexed for another machine, embeds all texts first (failure writes nothing), upserts by chunk id (no duplicates), deletes this revision's stale ids, keeps an already-current revision current, and with `make_current` promotes it. Returns `IndexSummary`. |
| `remove_manual(machine_id=, document_id=, revision=)` | `revision` is required: a label removes that revision, `None` removes all revisions of the document. `machine_id` is always in the filter, so other machines are never touched. Returns records removed. |
| `set_current_revision(machine_id=, document_id=, revision=)` | Demotes other revisions of that machine+document first, then promotes the target (never two current revisions; briefly possibly none). Unknown target → `RevisionNotFoundError`, nothing changed. Returns flags changed. |
| `search(query, machine_id=, limit=8)` | Rejects empty `machine_id`/query and `limit` outside 1–50. Calls `collection.query(where={"$and": [{"machine_id": m}, {"is_current": True}]})`, so the filter is applied **inside** the search. Returns `SearchResult`s with full metadata and raw `cosine_distance` (lower = closer; a ranking signal, **not** a calibrated confidence). A post-check raises if any result had another machine id (defence in depth only). |

### Answer generation (implemented; Groq adapter tested with a mocked SDK)

**Status.** The pipeline, prompt, citation validation and abstention are implemented and covered by
deterministic tests with fake LLMs. The provider is **Groq** (`GroqClient` in
`rag/generation/llm_client.py`, official `groq` SDK). Its tests mock the SDK; a live Groq call has not been
run in this repository's test suite (it needs a real key — use the smoke test below).

**Provider configuration.** `LLM_PROVIDER=groq`, `GROQ_API_KEY` (required; environment only, excluded from
`repr`, redacted from error messages), `GROQ_MODEL` (default `llama-3.3-70b-versatile`, listed as a Groq
production model when checked; if your account cannot use it, set e.g. `GROQ_MODEL=llama-3.1-8b-instant`),
`LLM_TIMEOUT_SECONDS` (default 30). Empty `LLM_PROVIDER` = no provider (`provider_unavailable`); another value
→ `ConfigError`; `groq` without a key → `ConfigError`. Requests use temperature 0 and Groq JSON mode
(`response_format={"type": "json_object"}`); the SDK retries 429/5xx twice. Any `groq.APIError` becomes
`LLMProviderError` ("Groq <ErrorClass> (HTTP n): message", key redacted, SDK exception not chained), which
`answer_question` returns as `generation_error`. Groq returns text only; tags are resolved through the
server-side source map, so citation metadata never comes from the model.

**Local setup and testing (Groq).**
```bash
cp .env.example .env                  # once; .env is git-ignored
# edit .env in an editor: GROQ_API_KEY=<your key>  (never commit it, never paste it into code, docs or chat)
# rag/config.py loads <project root>/.env automatically at import (real environment variables win)
python -m pytest rag/tests -q         # unit tests mock the SDK: no network, no key needed
python - <<'PY'                       # optional live smoke test (uses your quota)
import tempfile; from pathlib import Path
from rag.evaluation import build_index
from rag.generation.answer_service import answer_question
from rag.generation.llm_client import create_llm_client
with tempfile.TemporaryDirectory() as d:
    r = answer_question("What does E17 mean?", "M-HP200", store=build_index(Path(d)), llm=create_llm_client())
    print(r.status, r.answer, [(c.document_id, c.pdf_page) for c in r.citations], r.rejection_reasons, r.error)
PY
```

**Prompt architecture** (`rag/generation/prompts.py`). The system prompt restricts the LLM to the supplied
passages, forbids outside knowledge and invented values/procedures/IDs/pages, states that codes are
machine-specific, requires abstention when evidence is missing, requires conflicts to be reported, and
treats passage/question text as data (prompt-injection guard). The user prompt lists only this request's
passages as `<passage tag="S1" machine=… document=… revision=… pdf_page=…>`. Required reply:
`{"status": "answered", "statements": [{"text": "...", "sources": ["S1"]}]}` or
`{"status": "insufficient_evidence", "statements": []}`.

**Citation validation** (`rag/generation/citation_validator.py`). Tags are resolved only through the
per-request map `S1..Sn → SearchResult` built inside `answer_question`; citation metadata (machine,
document, revision, page, filename, passage) is copied from those records, never from LLM text. A draft
is rejected as a whole if any statement:
- cites no tag, or a tag (in `sources` or inline `[S#]`) that is not in this request's map;
- cites a record of another machine or with invalid metadata;
- names a document ID, page or revision that is neither a cited record's own value nor quoted in the cited passage;
- contains an error code (`E17`) or identifier (`RF-20`, `AC-L32`) absent from its cited passages;
- contains a number absent from its cited passages.

*Limitation:* these are lexical checks. They catch fabricated values, codes, parts, pages, documents and
foreign-machine terms, but cannot prove that a paraphrase is entailed by the passage (e.g. a wrong
relationship between two values that both appear, or a code's meaning restated without any code/number).
They can also reject a correct answer that, say, names the model `HP-200` while citing a passage that
does not contain that string; the correction round tells the LLM exactly which token to drop.

**Abstention layers** (fixed message *"The available manuals for the selected machine do not contain
enough information to answer this question."*):
1. no retrieved passage → `insufficient_evidence` (no LLM call);
2. relevance filter: passages with cosine distance > `RAG_MAX_DISTANCE` (default 0.80) are not sent;
   none left → `insufficient_evidence` (no LLM call);
3. LLM returns `insufficient_evidence`;
4. citation validation fails after `RAG_MAX_CORRECTIONS` (default 1) correction round(s) → `insufficient_evidence` with `rejection_reasons`;
5. malformed output after corrections → `generation_error`.

**Threshold tuning.** Cosine distance is a ranking signal, not a calibrated confidence. 0.80 is provisional,
chosen from 19 measured queries: every expected source in the 13-question evaluation had distance ≤ 0.663;
the best match for 6 clearly off-topic questions (e.g. "What is the capital of France?") was ≥ 0.862.
Distance **cannot** separate in-domain unanswerable questions (Q10 best 0.564 is closer than answerable
Q8's 0.663), so those rely on layers 3–4. Re-tune by running `python -m rag.evaluation` (and off-topic
probes) after any change to manuals, chunking or model, keeping every expected source under the threshold.

**Statuses → Django** (proposed mapping for integration): `answered` → `ANSWERED`;
`insufficient_evidence` → `INSUFFICIENT_EVIDENCE`; `provider_unavailable` and `generation_error` → `FAILED`.
`answer_question` raises `InvalidMetadataError` for a blank/oversized question or missing machine ID and
lets `VectorStoreError` propagate; provider failures never raise.

### Retrieval evaluation (13 questions, `python -m rag.evaluation`, limit 8, real model)

| Q | Machine | Question | Expected source | Retrieved (rank of expected) | Best distance | Sufficient evidence retrieved? |
|---|---|---|---|---|---|---|
| 1 | HP-200 | What does E17 mean? | HP-OPS-01 p2 | HP-OPS-01 p2 rank 1 (trigger text); alarm-table chunk p2:c0 rank 5 | 0.491 | Yes |
| 2 | AC-90 | What does E17 mean? | AC-OM-01 p2 | rank 1 | 0.517 | Yes |
| 3 | HP-200 | Normal operating pressure range? | HP-OPS-01 p1 | rank 1 | 0.495 | Yes |
| 4 | AC-90 | Normal operating pressure range? | AC-OM-01 p1 | rank 1 | 0.526 | Yes |
| 5 | HP-200 | How often is the filter checked? | HP-MNT-01 p2 | rank 2 (rank 1 HP-MNT-01 p1, RF-20 reference data) | 0.538 | Yes |
| 6 | AC-90 | AF-10 check / replacement interval | AC-OM-01 p3 | rank 1 | 0.410 | Yes |
| 7 | HP-200 | What to record before escalation? | HP-OPS-01 p3 and/or HP-MNT-01 p3 | ranks 1 and 2 | 0.529 | Yes |
| 8 | AC-90 | What to record before escalation? | AC-OM-01 p3 | rank 1 | 0.663 | Yes |
| 9 | HP-200 | E17 trigger + first checks (two manuals) | HP-OPS-01 p2 + HP-MNT-01 p3 | ranks 1 and 4 | 0.446 | Yes (both) |
| 10 | HP-200 | When is the AF-10 filter replaced? | none (abstain) | only HP-200 chunks; no passage mentions AF-10 | 0.564 | Correctly none |
| 11 | AC-90 | What does E24 mean? | none (abstain) | only AC-90 chunks; no passage mentions E24 | 0.593 | Correctly none |
| 12 | HP-200 | Pump replacement price? | none (abstain) | HP-OPS-01 p3 "Information not provided" rank 2 | 0.732 | Correctly none (needs LLM to abstain) |
| 13 | AC-90 | What does E31 mean? | AC-OM-01 p2 | rank 1 | 0.632 | Yes |

Every result set contained only the selected machine. **HP-200 E17 investigation:** the alarm-table chunk
ranks 5th (distance 0.644) — inside the default limit of 8 and under the threshold — and rank 1 is the same
page's "E17 is generated when pressure stays below 110 bar…" chunk. Both reach the LLM, and the integration
test quoting the table row `E17 Low hydraulic pressure` passes. No reproducible retrieval failure, so **no
hybrid/keyword retrieval was added**. It would become justified if a lower `RAG_TOP_K` dropped the table chunk.

## 2. Component responsibilities

| Component | Owner | Responsibility |
|---|---|---|
| Templates + static (HTML/CSS/JS/Bootstrap) | Dev B | Login, machine selector, ask form, answer + citation cards, PDF open, history tables, admin upload form, supervisor log |
| `accounts` app | Dev B | Custom `User` with `role` field; login/logout; role helpers |
| `machines` app | Dev B | Machine registration, manual upload/listing, authenticated PDF file view, triggers ingestion |
| `assistant` app | Dev B | Question submission, answer/citation persistence, history views |
| DRF layer | Dev B | Serializers, views, permission classes (`IsAdminRole`, `IsEngineerRole`, `IsSupervisorOrAdmin`, read-only enforcement) |
| Django services | Dev B (calls Dev A's interface) | Transaction boundaries, status transitions, mapping `rag` results ↔ models |
| `rag/` package | Dev A | Extraction, chunking, embeddings, Chroma storage, filtered retrieval, LLM call, evidence validation |
| PostgreSQL | — | Users, machines, manuals, ingestion jobs, questions, answers, citations |
| ChromaDB | — | Chunk text + embeddings + metadata; rebuildable from PDFs |
| LLM API | — | Text generation from supplied evidence only |

## 3. Data flow summary

1. Admin registers machines (values from `machine_manual_index.csv`) and uploads the three PDFs with document ID, name and revision.
2. Upload → PDF stored in `MEDIA_ROOT/manuals/` → `Manual` row → ingestion → chunks in ChromaDB → `Manual.status = INDEXED`.
3. Engineer asks a question for a selected machine → `Question` row → RAG → `Answer` + `Citation` rows → JSON response → UI renders answer and citation cards.
4. Citation link → authenticated PDF view → browser PDF viewer at `#page=N`.
5. Engineer views own history; Supervisor (and Admin) view the full log, filterable by status/machine.

## 4. RAG interface (contract between Dev A and Dev B)

Authoritative, detailed version: `docs/RAG_INTEGRATION_CONTRACT.md` (exact signatures, exceptions, ingestion
and revision workflows, API compatibility). Django examples: `docs/DJANGO_INTEGRATION_EXAMPLE.md`.

Implemented, framework-independent calls Django will use (no Django imports in `rag/`):

```python
# ingestion
doc = extract_pdf(pdf_path, machine_id=..., document_id=..., revision=..., source_filename=...)  # -> ExtractedDocument
chunks = chunk_document(doc)                                                                     # -> list[Chunk]

# vector store (one instance per process; embedder loaded once via get_embedder())
store = ChromaVectorStore(get_embedder())
store.index_manual(chunks, make_current=True)                       # -> IndexSummary
store.remove_manual(machine_id=..., document_id=..., revision=...)  # revision=None removes all revisions
store.set_current_revision(machine_id=..., document_id=..., revision=...)

# answering
llm = create_llm_client()                                           # None until a provider is configured
result = answer_question(question, machine_id, store=store, llm=llm)  # -> AnswerResult
```

`AnswerResult` (`rag/schemas.py`): `status` (`answered` | `insufficient_evidence` | `provider_unavailable`
| `generation_error`), `answer`, `citations` (each: `source_tag`, `chunk_id`, `machine_id`, `document_id`,
`revision`, `pdf_page`, `source_filename`, `passage`), `machine_id`, `question`, `retrieval`
(`limit`, `max_distance`, `retrieved_count`, `evidence` = tag/chunk/document/revision/page/distance per
passage sent to the LLM), `llm_model`, `rejection_reasons`, `error`.

- Blank/oversized question or missing machine ID → `InvalidMetadataError`; vector-store faults →
  `VectorStoreError` (caller marks the question `FAILED`). Provider faults are returned as `generation_error`.
- A thin `rag/service.py` facade wiring these together is still optional; any interface change must be
  agreed with Dev B and this section updated.

## 5. Authentication & authorization flow

1. User submits the Django login form (`/accounts/login/`); Django creates a session.
2. Templates and DRF both use the session (`SessionAuthentication`); unsafe API methods require the CSRF token (JS reads the `csrftoken` cookie and sends `X-CSRFToken`).
3. Each API view declares a permission class based on `request.user.role`. Unauthenticated → 403 (API) / redirect to login (pages); wrong role → 403.
4. Querysets are scoped server-side: engineer history is filtered by `user=request.user`.
5. Template navigation hides links the role cannot use (cosmetic only).

Permission matrix:

| Action | ADMIN | ENGINEER | SUPERVISOR |
|---|:-:|:-:|:-:|
| Log in / log out | ✅ | ✅ | ✅ |
| List machines | ✅ | ✅ | ✅ |
| Register / edit machine | ✅ | ❌ | ❌ |
| Upload manual / new revision | ✅ | ❌ (403) | ❌ (403) |
| List manuals & ingestion status | ✅ | ✅ (read-only) | ✅ (read-only) |
| Open manual PDF / cited page | ✅ | ✅ | ✅ (to verify logged citations) |
| Ask a question | ❓ open question (default ❌) | ✅ | ❌ |
| View own history | ✅ (n/a if Admin can't ask) | ✅ | — |
| View all users' history | ✅ (read-only) | ❌ | ✅ (read-only) |
| Filter unanswered / insufficient-evidence | ✅ | ❌ | ✅ |
| Modify/delete questions, answers, citations | ❌ (immutable log) | ❌ | ❌ |

## 6. Manual ingestion flow

```
Admin POST /api/manuals/ (multipart: machine_id, document_id, document_name, revision, file)
 → validate role=ADMIN; machine exists & active; (document_id, revision) unique; file is a real PDF
 → save file to MEDIA_ROOT/manuals/<machine_id>/<document_id>_rev<revision>.pdf
 → Manual(status=PROCESSING), IngestionJob(status=RUNNING, started_at)
 → rag.index_manual(...)
     extract: fitz.open → for page_no, page in enumerate(doc, start=1): page.get_text()
     chunk:   split each page into section-sized chunks; chunks never span pages
     embed:   all-MiniLM-L6-v2 (normalized vectors)
     store:   collection.upsert(ids, embeddings, documents, metadatas) with deterministic ids,
              is_current=False until the job succeeds
 → success: rag.set_current_revision(document_id, revision)  (new rev → True, older revs → False)
            Manual(status=INDEXED, page_count, is_current=True); older Manual rows is_current=False
            Job(SUCCEEDED, pages_processed, chunks_indexed, completed_at)
 → failure: rag.remove_manual(document_id, revision) to clear partial chunks
            Manual(status=FAILED), Job(FAILED, error_message); previous revision stays current
```

Ingestion runs **synchronously** in the request (each manual is 3 pages; expected to take seconds once the model is loaded). The status fields allow moving to background processing later without schema change.

A management command (proposed: `python manage.py reindex_manuals`) rebuilds the Chroma index from stored PDFs + `Manual` rows — this is what makes Chroma a derived index.

## 7. Question–answer (RAG) flow

```
Engineer POST /api/questions/ {machine_id, question}                 (Django — not implemented yet)
 1. Authenticated + role=ENGINEER; machine exists and is_active
 2. Question(status=PENDING) saved
 3. rag answer_question(question, machine.machine_id, store=..., llm=...)   (implemented)
    a. validate question (non-blank, ≤ 1000 chars) and machine_id (non-empty)
    b. store.search(): where={"$and": [{"machine_id": m}, {"is_current": True}]} inside the vector query, limit RAG_TOP_K
    c. drop passages with cosine distance > RAG_MAX_DISTANCE; none left → insufficient_evidence (no LLM call)
    d. no LLM configured → provider_unavailable
    e. prompt = system rules + this request's passages tagged S1..Sn + question
    f. LLM reply JSON {status, statements[{text, sources}]} → parse → validate (see "Citation validation")
       invalid/malformed → correction round(s) → still invalid → insufficient_evidence / generation_error
    g. citations resolved from the request's tag map (document/revision/page never taken from LLM text)
 4. One DB transaction: Answer + Citation rows; Question.status from the mapping in §1 "Answer generation"
 5. Response JSON (see API_CONTRACT.md) → UI renders answer + citation cards with "Open page N" links
```

The insufficient-evidence message is a fixed server string, not LLM-generated text.

### Multi-document answers (HP-200)
Both HP-200 manuals share `machine_id = M-HP200`, so one filtered query ranks chunks from HP-OPS-01 and HP-MNT-01 together. The prompt carries passages from both; the LLM cites each tag it relies on; the server stores one Citation per used chunk. Example from the manuals: "What triggers E17 and what should be checked first?" needs HP-OPS-01 p.2 (trigger: below 110 bar > 5 s during forming) and HP-MNT-01 p.3 (ordered observations). HP-OPS-01 p.3 itself says to cite both documents in this case.

Retrieval tuning: `RAG_TOP_K` defaults to 8 (the corpus is 18 chunks; HP-200 has 12). In the evaluation both
HP-200 sources for Q9 were in the top 4 and the integration test produces citations from both manuals. A
machine-filtered lexical boost for codes remains a documented option, not implemented (see the evaluation above).

## 8. PDF citation viewing

- `GET /api/manuals/{id}/file/` streams the stored PDF (`FileResponse`, `application/pdf`, `inline`) after an authentication check.
- Citation links use `…/file/#page=N`; built-in browser PDF viewers honor the page fragment. Opened in a new tab or a Bootstrap modal `<iframe>` (UI choice for Dev B).
- No PDF.js or other viewer library unless the native viewer proves inadequate (requires approval).

## 9. Deployment architecture (hackathon/demo)

Single host (developer laptop / lab machine):

| Process / store | Detail |
|---|---|
| Django dev server (`runserver`) | Demo only; `DEBUG` from env |
| PostgreSQL | Local service; credentials via env vars |
| ChromaDB | `chromadb.PersistentClient(path=CHROMA_DIR)`; `chroma_db/` is git-ignored |
| Media | `MEDIA_ROOT/manuals/` (should be git-ignored — proposed) |
| Embedding model | Downloaded once to the Hugging Face cache; pre-fetch before the demo |
| LLM | External HTTPS API; key in env var |

Environment variables (values never committed):
- RAG engine (implemented, see `.env.example`): `RAG_CHUNK_SIZE_WORDS`, `RAG_CHUNK_OVERLAP_WORDS`, `EMBEDDING_MODEL`, `EMBEDDING_DEVICE`, `EMBEDDING_BATCH_SIZE`, `EMBEDDING_CACHE_DIR`, `CHROMA_DIR`, `CHROMA_COLLECTION`, `RAG_TOP_K`, `RAG_MAX_DISTANCE`, `RAG_MAX_CORRECTIONS`, `LLM_PROVIDER`, `GROQ_API_KEY`, `GROQ_MODEL`, `LLM_TIMEOUT_SECONDS`.
- Django (proposed, not implemented): `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

## 10. Architectural decisions & trade-offs

| Decision | Rationale | Trade-off |
|---|---|---|
| Modular monolith | 6.5-hour build, 2 active devs, one deployable | Less isolation than services — fine at this scale |
| `rag/` has no Django imports | Dev A builds/tests independently on `feature/rag-engine`; clean integration seam | Small mapping layer in Django services |
| Session auth (not JWT) | Same-origin template app; built into Django + DRF; CSRF protection | Not suited to third-party API clients (not required) |
| Synchronous ingestion | Tiny PDFs; avoids Celery/Redis | Large manuals would block a request — status fields allow later change |
| Single Chroma collection + metadata filter | Machine filter in-query; multi-manual answers natural | Isolation depends on always passing `where` → enforced by interface + mandatory test |
| Page-bounded chunks | Each chunk maps to exactly one PDF page → exact page citations | Some chunks short |
| Citations derived from metadata, not LLM text | Prevents fabricated pages/IDs/revisions | LLM must emit source tags reliably → JSON + validation + retry |
| Threshold + LLM abstention + validation | Three independent gates against unsupported answers | Possible false abstentions; tune threshold on test set |
| Native browser PDF viewer | No extra dependency | Viewer UI varies by browser |
| Answer separate from Question | Question logged even when RAG fails; supports "unanswered" review | One extra join |
