# CLAUDE.md — INDUSTRIX AI · Manufacturing Manual Assistant

Permanent project instructions for Claude Code. Read this file fully before doing any work in this repository.

## 1. Project overview

INDUSTRIX AI is a Round 1 hackathon submission: a web application where engineers select a machine, ask a maintenance question, and receive an answer generated **only** from that machine's PDF manuals, with verifiable citations (document ID/name, revision, original PDF page, supporting passage, source link).

**Source of truth for requirements:** `challenge-pack/Round1_Student_Challenge_Brief.pdf` (DOCX copy is identical in content) and `challenge-pack/START_HERE.txt`. If this file, the docs, or a user prompt conflict with the brief, stop and flag it — do not silently pick one.

Detailed documentation:

| File | Purpose |
|---|---|
| `docs/PROJECT_ARCHITECTURE.md` | Components, flows, deployment, decisions |
| `docs/DATABASE_ARCHITECTURE.md` | Relational schema + ChromaDB metadata design |
| `docs/TECH_STACK.md` | Every technology, its layer and justification |
| `docs/API_CONTRACT.md` | REST endpoints and request/response shapes |
| `docs/IMPLEMENTATION_PLAN.md` | Phases, owners, dependencies, acceptance criteria, test questions |
| `docs/RAG_INTEGRATION_CONTRACT.md` | Exact `rag/` public interface Django must use (signatures, statuses, exceptions, workflows) |
| `docs/DJANGO_INTEGRATION_EXAMPLE.md` | Illustrative Django service-layer code calling `rag/` |

## 2. Mandatory technology stack — do not replace

| Layer | Technology |
|---|---|
| Backend | Python 3, Django, Django REST Framework |
| Frontend | Django templates + HTML5, CSS3, vanilla JavaScript, Bootstrap 5 |
| Relational DB | PostgreSQL (preferred). Design must stay MySQL-compatible (brief allows "PostgreSQL or MySQL") |
| PDF extraction | PyMuPDF (`import pymupdf`; the legacy `fitz` name is deprecated in 1.28) |
| Embeddings | Sentence Transformers, model `all-MiniLM-L6-v2` (subject to compatibility testing) |
| Vector store | ChromaDB, persistent client |
| Generation | External LLM API, provider/model chosen via environment variables |

Rules:
- **Never** introduce React, Next.js, Vue, Angular, HTMX-style frameworks, Celery, Redis, LangChain, LlamaIndex, FAISS, or any other framework/service not listed above without explicit user approval.
- Do not train or fine-tune any model.
- Do not add a dependency without stating why the standard library / existing stack cannot do it.

## 3. Challenge-specific facts (verified against `challenge-pack/`)

| machine_id | model | machine_name | document_id | revision | pdf_filename | pages |
|---|---|---|---|---|---|---|
| M-HP200 | HP-200 | Hydraulic forming press | HP-OPS-01 | 1.0 | HP200_Operations_Rev1_0.pdf | 3 |
| M-HP200 | HP-200 | Hydraulic forming press | HP-MNT-01 | 1.0 | HP200_Maintenance_Rev1_0.pdf | 3 |
| M-AC90 | AC-90 | Air compressor | AC-OM-01 | 1.0 | AC90_Operations_Maintenance_Rev1_0.pdf | 3 |

- **Only** the three PDFs in `challenge-pack/Manuals/` are the answer corpus. Never index the brief, `START_HERE.txt`, the CSV, or any README.
- PDF page numbers are **one-based**; page 1 of the PDF = printed page 1.
- Both HP-200 manuals are **complementary current** references (not revisions of each other). Answers may need to cite both.
- Error codes are **machine-specific**: E17 = low hydraulic pressure (HP-200) vs high discharge temperature (AC-90); E31 = guard-open (HP-200) vs condensate-drain fault (AC-90); E42 = RF-20 return filter (HP-200) vs AF-10 inlet filter (AC-90).
- Equipment and data are fictional training material.
- Never modify anything inside `challenge-pack/`.

## 4. Architecture principles

- Modular monolith: one Django project, small apps, plus a framework-independent `rag/` package.
- PostgreSQL is the **source of truth**. ChromaDB is a **derived, rebuildable index** — anything in it must be reproducible from the stored PDFs + relational metadata.
- `rag/` must not import Django. Django calls it through a small service interface (see `docs/PROJECT_ARCHITECTURE.md` §4). Django passes plain values in, receives plain dataclasses out.
- Business logic lives in service functions, not in views or templates.
- Secrets and environment-specific values come from environment variables only.

## 5. Database principles

- Normalized schema: Machine → Manual → (IngestionJob); User → Question → Answer → Citation → Manual.
- Natural business keys (`machine_id` like `M-HP200`, `document_id` + `revision`) are unique-constrained; surrogate integer PKs are used for FKs.
- Manuals and revisions are never hard-deleted while cited; FKs from citations use `PROTECT`.
- A question is persisted **before** RAG runs so failures are still logged.
- Status values are explicit enums: questions end as `ANSWERED`, `INSUFFICIENT_EVIDENCE` or `FAILED`.
- Use only Django ORM features that work on both PostgreSQL and MySQL; where a PostgreSQL-only feature is used (e.g. partial unique constraint), also enforce the rule in the service layer.

## 6. RAG rules (non-negotiable)

1. Machine is resolved server-side from the database; the client never supplies raw filter values.
2. Retrieval **must** pass a Chroma `where` filter on `machine_id` (and `is_current`) **inside the vector query**. Post-filtering alone is forbidden. A retrieval call without a machine filter must raise an error.
3. Only retrieved chunks are sent to the LLM. The prompt instructs it to use only those passages and to abstain otherwise.
4. The LLM cites passages by opaque source tags (e.g. `[S1]`); the server maps tags → chunk metadata. **Document IDs, revisions and page numbers in citations always come from chunk metadata, never from LLM text.**
5. Any citation tag not in the retrieved set is discarded; if no valid citations remain, the result is `INSUFFICIENT_EVIDENCE`.
6. Abstain (fixed message: the available manuals for the selected machine do not contain enough information) when retrieval relevance is below threshold, the LLM signals insufficiency, or validation fails.
7. Never fabricate pages, document IDs, revisions, passages, or specifications. Never answer from general knowledge or another machine's manual.

## 7. Machine isolation & security

- Machine isolation is a correctness **and** security requirement. Every retrieval code path must be covered by a test proving no foreign-machine chunk is returned.
- Roles: `ADMIN`, `ENGINEER`, `SUPERVISOR`. Enforce with DRF permission classes and view-level checks on the server; hiding UI elements is cosmetic only.
- Only `ADMIN` registers machines and uploads manuals. `ENGINEER` sees only their own history. `SUPERVISOR` is strictly read-only.
- Manual PDFs are served through an authenticated Django view, not as public static files.
- Validate uploads: PDF extension + MIME + `fitz` can open it; size limit; sanitized filenames.
- No API keys, passwords or tokens in code, docs, commits or logs. Use `.env` (git-ignored) and keep `.env.example` with placeholder names only.
- Keep Django's CSRF protection on; DRF uses `SessionAuthentication`.

## 8. Coding standards

- Python: PEP 8, type hints on public functions, docstrings on service/RAG interfaces, `logging` not `print`.
- Small functions, explicit names, no dead code, no speculative abstractions.
- JavaScript: vanilla ES modules/`fetch`, no build step; include the CSRF token on unsafe requests.
- Templates extend one base layout; Bootstrap 5 from a pinned version.
- Configuration via `settings.py` reading `os.environ`.

## 9. Git & ownership

Branches (never rename): `main`, `feature/rag-engine`, `feature/django-product`.

| Owner | Branch | Owns |
|---|---|---|
| Developer A | `feature/rag-engine` | `rag/` package, RAG tests, ingestion/retrieval/LLM/validation logic |
| Developer B | `feature/django-product` | Django project/apps, models, auth, APIs, templates, static, Django tests, integration of `rag/` |
| Shared | `main` | `CLAUDE.md`, `docs/`, `.gitignore`, `.env.example`, `requirements.txt` |

- Do not edit files owned by the other developer without explicit coordination. Changes to the `rag/` public interface must be agreed and reflected in `docs/RAG_INTEGRATION_CONTRACT.md` (and its contract tests, `rag/tests/test_public_interface.py`).
- Commit or push only when the user asks. Small, focused commits with descriptive messages.

## 10. Testing requirements

The brief mandates two automated checks — these must exist and pass before submission:
1. **An Engineer cannot upload a manual** (API returns 403; no Manual row created).
2. **Retrieval excludes passages belonging to another machine** (query for M-AC90 never returns M-HP200 chunks and vice versa, including for shared codes like E17).

Also test: abstention path, citation validation drops unknown tags, supervisor cannot write, engineer sees only own history. Run tests before declaring any stage done and report real results.

## 11. How to work in this repo

- **Inspect before modifying:** read the relevant existing files and the docs before changing code. Don't assume structure.
- **Don't invent requirements.** If the brief is silent, mark it as an open question or a clearly labeled "proposed implementation detail".
- **Don't replace required technologies.**
- **Work in small, verifiable stages** following `docs/IMPLEMENTATION_PLAN.md`; verify each stage (tests, manual run) and report before moving on.
- Do not create fake data, fake answers, or fake citations — including in demo fixtures.
- Do not install packages or start a new phase without user approval when the user has asked to gate work.
