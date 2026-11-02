# Technology Stack — INDUSTRIX AI

Legend — **Source**: *Brief* = explicitly required by `Round1_Student_Challenge_Brief.pdf`; *Team* = team's selection where the brief leaves the choice open.

| Technology | Layer | Source | Purpose |
|---|---|---|---|
| Python 3 | Runtime | Brief ("Python Full Stack") | Language for backend and RAG |
| Django | Backend framework | Brief | Web app, ORM, auth, templates, file handling |
| Django REST Framework | API layer | Brief | JSON APIs, serializers, permissions |
| PostgreSQL | Relational persistence | Brief (PostgreSQL **or** MySQL) — team prefers PostgreSQL | Source of truth |
| HTML5 | Presentation | Brief | Page structure (Django templates) |
| CSS3 | Presentation | Brief | Styling on top of Bootstrap |
| JavaScript | Presentation | Brief | `fetch` API calls, dynamic rendering |
| Bootstrap 5 | Presentation | Brief ("Bootstrap") — version 5 is team choice | Layout, forms, cards, tables, modals |
| PyMuPDF | PDF processing | Brief | Page-level text extraction |
| Sentence Transformers (`all-MiniLM-L6-v2`) | RAG — embeddings | Brief requires "embeddings"; library/model are team choice | Text → vectors |
| ChromaDB | RAG — vector store | Brief requires "a vector store"; ChromaDB is team choice | Similarity search with metadata filters |
| External LLM API | RAG — generation | Brief requires "an LLM"; provider per college's separately announced model access | Grounded answer text |

## Python 3
- **Why:** required; one language across web and ML code.
- **Version:** 3.11 or 3.12 recommended (open question: confirm lab Python version and that ChromaDB/torch wheels exist for it).
- **Used for:** Django project, `rag/` package, tests, management commands.

## Django
- **Layer:** backend / web.
- **Why:** required; provides ORM + migrations, session auth, CSRF protection, `FileField` uploads, templates, test runner.
- **Used for:** models (`accounts`, `machines`, `assistant`), login/logout, server-rendered pages, authenticated PDF serving, management commands (seed/reindex).

## Django REST Framework
- **Layer:** API.
- **Why:** required; serializers for validation, `APIView`/generic views, permission classes for server-side role enforcement, `APITestCase`/`APIClient` for the mandated automated checks.
- **Configuration:** `SessionAuthentication` + `IsAuthenticated` default; custom role permission classes.

## PostgreSQL (MySQL-compatible design)
- **Layer:** relational persistence.
- **Why:** brief allows PostgreSQL or MySQL; PostgreSQL supports partial unique constraints and is well supported by Django.
- **Driver:** `psycopg` (PostgreSQL) or `mysqlclient` (MySQL) — selected via `DB_ENGINE`.
- **Portability rule:** only the partial unique constraint on current revision is PostgreSQL-specific; it is duplicated in service logic.

## HTML5 / CSS3 / JavaScript
- **Layer:** presentation.
- **Why:** required.
- **Approach:** Django templates render pages; vanilla JS (`fetch`, DOM APIs) calls DRF endpoints and renders answers/citations/history. No bundler, no SPA framework. Custom CSS limited to a small `static/css/app.css`.
- **Security:** render all user/LLM/passage text with `textContent` (never `innerHTML`) to prevent XSS.

## Bootstrap 5
- **Layer:** presentation.
- **Why:** required ("Bootstrap"); responsive grid, forms, cards, badges (status), tables (history), modal (PDF view), alerts (insufficient evidence).
- **Delivery:** pinned version from a CDN or vendored into `static/` (open question: does the evaluation environment have internet? If not, vendor it).

## PyMuPDF (`pymupdf`)
- **Layer:** ingestion. **Status:** implemented (`rag/ingestion/pdf_extractor.py`). **Installed:** 1.28.2 (`pymupdf>=1.26,<2`).
- **Why:** required by the brief for text extraction.
- **Used for:** opening PDFs (also rejects non-PDF, empty, encrypted or page-less files), per-page text via `get_text("blocks", sort=True)` with one-based page numbers.
- **Note:** imported as `import pymupdf`; the legacy `fitz` name prints a deprecation notice in 1.28.

## Sentence Transformers — `all-MiniLM-L6-v2`
- **Layer:** RAG embeddings. **Status:** implemented (`rag/embeddings/embedder.py`).
- **Installed:** sentence-transformers 6.2.0 (`>=6.2,<7`), transformers 5.22.1, torch 2.15.0+cpu (`>=2.15,<3`, CPU-only wheel from the PyTorch CPU index), numpy 2.5.4.
- **Why:** small (~90 MB download), CPU-friendly; runs locally so manual text is not sent to a third-party embedding API.
- **Verified:** loads on Python 3.13.14 / Kali; embedding dimension **384** read from the loaded model; max input 256 word-pieces (chunks are ≤180 words); vectors L2-normalized; first load ~10 s cold, cached afterwards; works offline with `HF_HUB_OFFLINE=1` once cached.
- **Retrieval quality observed** on the practice questions (temporary index, real manuals): correct machine always; AF-10 schedule, AC-90 E17 alarm table and HP-200 filter schedule rank in the top 3; a two-manual HP-200 question returns both manuals in the top 4. Weakness: short codes (`E17`) carry little weight — the HP-200 alarm-table chunk is not in the top 4 for "What does E17 mean?". No lexical boost is implemented yet.
- **Model change:** configurable (`EMBEDDING_MODEL`), but the collection records its model and refuses a different one — rebuild the index after changing it.

## ChromaDB
- **Layer:** RAG vector store. **Status:** implemented (`rag/vectorstore/chroma_store.py`). **Installed:** 1.5.9 (`>=1.5.9,<2`).
- **Why:** embedded persistent mode (no extra server), metadata `where` filters applied inside the query (machine isolation), simple Python API.
- **Configuration:** `PersistentClient(CHROMA_DIR)` (default `<project root>/chroma_db/`, git-ignored), collection `manual_chunks`, HNSW cosine space, anonymized telemetry disabled, `embedding_function=None` (embeddings always supplied by our embedder).
- **Version notes (1.5.9, verified):** `get_collection()` without `embedding_function=None` silently attaches Chroma's default embedding function, so the store always passes `None`; `embedding_function=None` triggers a "legacy embedding function config" `DeprecationWarning`, filtered narrowly in `chroma_store.py`; `update(metadatas=...)` merges keys (used for `is_current`).
- **Used as:** derived index; rebuildable from the PDFs.

## LLM API
- **Layer:** RAG generation. **Status:** provider-independent pipeline **implemented and tested with fake LLMs**;
  **real provider pending** — no provider has been approved and no credentials exist (the brief: model access
  is "announced separately"). No real LLM call has been made or tested.
- **Why:** brief requires an LLM; no training.
- **Interface:** `rag/generation/llm_client.py` — `LLMClient` protocol (`model_name`,
  `complete(*, system_prompt, user_prompt) -> str`) and `create_llm_client()` (returns `None` when
  `LLM_PROVIDER` is empty). No concrete adapter and no provider SDK are installed; adding one is a single
  class registered in `_ADAPTERS`, plus that provider's official SDK (or the standard library for HTTP).
- **Configuration:** `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` (environment only; excluded from `repr`),
  `LLM_TIMEOUT_SECONDS` (default 30). Adapters should request temperature 0.
- **Constraints enforced in code:** the LLM receives only the selected machine's retrieved, current-revision
  passages tagged `S1..Sn`; its JSON reply is parsed and validated server-side (tags must belong to this
  request, numbers/codes/identifiers/pages/documents must be supported by the cited passages); citation
  metadata comes from retrieved records, never from LLM output.
- **Answer settings:** `RAG_TOP_K` (8), `RAG_MAX_DISTANCE` (0.80, provisional — see architecture doc
  "Threshold tuning"; `off` disables), `RAG_MAX_CORRECTIONS` (1).

## Explicitly not used
React/Vue/Next.js/Angular, Celery/Redis, LangChain/LlamaIndex, FAISS/other vector DBs, JWT libraries, PDF.js (unless approved later), model training/fine-tuning.

## Python dependencies
Installed in `.venv/` (never globally) and declared in `rag/requirements.txt` / `rag/requirements-dev.txt`:

| Package | Constraint | Tested version | Status |
|---|---|---|---|
| pymupdf | `>=1.26,<2` | 1.28.2 | in use |
| torch (CPU wheel) | `>=2.15,<3` | 2.15.0+cpu | in use (via sentence-transformers) |
| sentence-transformers | `>=6.2,<7` | 6.2.0 | in use |
| chromadb | `>=1.5.9,<2` | 1.5.9 | in use |
| pytest (dev) | `>=8,<10` | 9.2.0 | in use |
| Django, djangorestframework, psycopg / mysqlclient | — | — | not installed yet (Django phase) |
| LLM provider SDK | — | — | none: provider not yet approved |

Install order (CPU torch first):
```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install "torch>=2.15,<3" --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r rag/requirements-dev.txt
```
