# Implementation Plan — INDUSTRIX AI

Status: **Awaiting approval.** No phase starts until approved.

Time box: the brief allows **6.5 hours** of development (Day 1, 12:00–18:30), evaluation 19:00–22:00. Time estimates below are for that window with two active developers working in parallel.

Owners: **A** = Developer A (`feature/rag-engine`), **B** = Developer B (`feature/django-product`), **A+B** = both.

> **Implementation note (RAG phases 4–8 done):** the `rag/` file and type names below (`rag/extract.py`, `rag/chunk.py`,
> `rag/llm.py`, `rag/service.answer_question()`, `RagResult`) were proposals. The implemented layout is in
> `docs/PROJECT_ARCHITECTURE.md` ("`rag/` package structure") and the interface in `docs/RAG_INTEGRATION_CONTRACT.md`.
> PyMuPDF is imported as `pymupdf`. Requirements: `rag/requirements.txt` (torch CPU wheel installed first).

## Dependency graph

```
1 Foundation ─┬─► 2 Django backend ─► 3 DB & auth ─┬─► 9 Frontend ─► 10 PDF viewer
              │                                     │
              └─► 4 PDF ingestion ─► 5 Embeddings ─► 6 Filtered retrieval ─► 7 LLM ─► 8 Citation validation
                                                    │                                         │
                                                    └──────────────► 12 Integration ◄─────────┘
                                                                          │
                                         11 Testing (continuous; gate) ───┤
                                                                          ▼
                                                                  13 Demo preparation
```
Phases 2–3 (B) and 4–8 (A) run in parallel; they meet at 12, using the interface in `PROJECT_ARCHITECTURE.md` §4.

## Phase 1 — Project foundation (A+B, ~20 min)
- Approve these docs; commit `CLAUDE.md` + `docs/` to `main`; merge `main` into both feature branches.
- Add `.env.example` (variable names only), `requirements.txt` (pinned after install test), extend `.gitignore` (`media/`, `.env`).
- Create venv; install and smoke-test heavy deps (sentence-transformers/torch, chromadb, pymupdf) on the target machine; pre-download `all-MiniLM-L6-v2`.
- Confirm LLM access method and key handling.

**Acceptance:** both branches contain the docs; `pip install -r requirements.txt` succeeds; `python -c "import fitz, chromadb, sentence_transformers"` works; embedding model loads offline.

## Phase 2 — Django backend skeleton (B, ~20 min) — depends on 1
- `django-admin startproject` (project package name proposed `config`); apps `accounts`, `machines`, `assistant`.
- Settings read env vars; DRF installed with `SessionAuthentication` default; `MEDIA_ROOT`; base template with Bootstrap 5.

**Acceptance:** `runserver` starts; `/accounts/login/` renders; `python manage.py check` clean.

## Phase 3 — Database & authentication (B, ~50 min) — depends on 2
- Custom `User(role)` **before first migration**; models per `DATABASE_ARCHITECTURE.md`; migrations on PostgreSQL.
- Role permission classes; login redirect by role; `GET /api/auth/me/`.
- Machine endpoints; manual upload/list endpoints (with a stub ingestion call until Phase 12); authenticated PDF file view.
- Seed command (proposed): register the two machines from `machine_manual_index.csv`; create demo users per role (passwords from env, not committed).

**Acceptance:** migrations apply; Admin can create machine + upload PDF (file stored, row created); Engineer/Supervisor upload → 403; mandatory test #1 passes.

## Phase 4 — PDF ingestion (A, ~30 min) — depends on 1
- `rag/extract.py`: open with PyMuPDF, return `[(pdf_page, text)]` one-based; record page count.
- `rag/chunk.py`: page-bounded, section-aware chunks (headings/blocks), ≤ ~200 words, deterministic `chunk_id`; attach full metadata (machine_id, document_id, document_name, revision, pdf_page, chunk_id, chunk_index, source_filename, is_current, embedding_model).

**Acceptance:** all three manuals → 3 pages each; every chunk has every metadata key; no chunk spans pages; page numbers match printed footers (e.g. "HP-OPS-01 | Rev 1.0 | Page 2" text lands in `pdf_page=2`).

## Phase 5 — RAG embeddings & vector store (A, ~30 min) — depends on 4
- `rag/embed.py`: load model once (module-level cache), normalized embeddings.
- `rag/store.py`: `PersistentClient(CHROMA_DIR)`, cosine collection, `upsert` with explicit embeddings; `remove_manual`, `set_current_revision`.
- `rag/service.index_manual()`.

**Acceptance:** indexing the 3 PDFs is idempotent (re-run → same chunk count); collection count equals sum of chunks; index survives process restart.

## Phase 6 — Machine-filtered retrieval (A, ~30 min) — depends on 5
- `rag/retrieve.py`: query with `where={"$and":[{"machine_id": m},{"is_current": True}]}`; raise if `machine_id` missing; relevance threshold; lexical boost for codes/part IDs within the same filter; defensive post-check.

**Acceptance:** mandatory test #2 passes (see Phase 11); "What does E17 mean?" retrieves HP-OPS-01 p.2 for M-HP200 and AC-OM-01 p.2 for M-AC90, with zero cross-machine chunks.

## Phase 7 — LLM answer generation (A, ~40 min) — depends on 6 + LLM access
- `rag/llm.py`: provider adapter from env; temperature 0; timeout; one retry on invalid JSON.
- Prompt: answer only from passages `[S1..Sn]`, cite tags, return `insufficient_evidence: true` when passages don't answer; never use outside knowledge; concise.

**Acceptance:** supported test questions produce answers with source tags; unsupported ones return the insufficiency flag.

## Phase 8 — Citation & evidence validation (A, ~25 min) — depends on 7
- `rag/validate.py`: drop unknown tags; map tags → metadata; enforce same machine; zero valid citations → `INSUFFICIENT_EVIDENCE`; fixed abstention message.
- `rag/service.answer_question()` returns `RagResult`.

**Acceptance:** unit tests with a stubbed LLM: fabricated tag `[S9]` is dropped; LLM text claiming "page 7" cannot produce a page-7 citation; all-invalid citations → abstain.

## Phase 9 — Frontend (B, ~60 min) — depends on 3
- Engineer assistant page: machine `<select>` from `/api/machines/`, question form, loading state, answer card, citation cards (document name/ID, revision, page, passage, "Open page N"), insufficient-evidence alert.
- History page (engineer), Log page (supervisor/admin) with status/machine filters and "unanswered" filter; detail view.
- Admin pages: machine registration form, manual upload form + status table.
- All dynamic text via `textContent`; CSRF header on POST.

**Acceptance:** every brief workflow is clickable end-to-end with stubbed RAG; responsive at laptop width.

## Phase 10 — PDF viewer (B, ~15 min) — depends on 3, 9
- Citation link → `/api/manuals/{id}/file/#page=N` in a new tab or Bootstrap modal iframe.

**Acceptance:** clicking a citation for page 3 opens the manual on page 3 in Chrome/Firefox; unauthenticated access is refused.

## Phase 11 — Testing (A+B, continuous; hard gate before 13)

Mandatory automated checks (brief):
1. **Engineer cannot upload manuals** (B, Django `APITestCase`): engineer `POST /api/manuals/` → 403; `Manual.objects.count()` unchanged; no file written. Also Supervisor → 403; Admin → 201.
2. **Retrieval excludes another machine's passages** (A, test against a temp Chroma dir with the three real PDFs indexed): for each test query, results for M-AC90 contain only `machine_id == "M-AC90"` and vice versa; `answer_question` without machine raises.

Additional tests: engineer sees only own history; supervisor write attempts → 403/405; abstention path persists `INSUFFICIENT_EVIDENCE`; citation validation unit tests; revision switch hides old revision.

### Grounded evaluation questions (expected evidence verified from the manuals)

| # | Machine | Question | Expected behaviour | Expected source(s) |
|---|---|---|---|---|
| 1 | HP-200 | What does E17 mean? | Low hydraulic pressure (below 110 bar > 5 s during forming) | HP-OPS-01 p.2 |
| 2 | AC-90 | What does E17 mean? | High discharge temperature | AC-OM-01 p.2 |
| 3 | HP-200 | What is the normal operating pressure range? | 120–140 bar during forming | HP-OPS-01 p.1 |
| 4 | AC-90 | What is the normal operating pressure range? | 7.0–8.0 bar outlet under steady demand | AC-OM-01 p.1 |
| 5 | HP-200 | How often is the filter checked? | RF-20 indicator every 50 operating hours | HP-MNT-01 p.2 |
| 6 | AC-90 | How often should the AF-10 filter indicator be checked, and when is planned replacement due? | Every 100 h; replacement every 600 h | AC-OM-01 p.3 |
| 7 | HP-200 | What should be recorded before escalation? | Escalation record fields | HP-OPS-01 p.3 and/or HP-MNT-01 p.3 |
| 8 | AC-90 | What should be recorded before escalation? | Escalation record fields | AC-OM-01 p.3 |
| 9 | HP-200 | What triggers E17 and what should be checked first? | **Multi-document** | HP-OPS-01 p.2 + HP-MNT-01 p.3 |
| 10 | HP-200 | When is the AF-10 filter replaced? | **Isolation**: AF-10 exists only in AC-90 manual → insufficient evidence, no AC-OM-01 citation | — |
| 11 | AC-90 | What does E24 mean? | **Isolation**: E24 defined only for HP-200 → insufficient evidence | — |
| 12 | HP-200 | What is the pump replacement price? | **Unanswerable** → insufficient evidence | (HP-OPS-01 p.3 states it is not provided) |
| 13 | AC-90 | What does E31 mean? | Condensate-drain fault (not guard-open) | AC-OM-01 p.2 |

These are used for retrieval tuning (K, threshold) and as integration tests where the LLM is available. No model outputs are pre-written.

## Phase 12 — Integration (A+B, ~40 min) — depends on 3, 8 (and 9 for UI)
- Merge `feature/rag-engine` and `feature/django-product` into `main` (via PRs or agreed merge order).
- Replace ingestion stub with `rag.service.index_manual`; implement `ask_question` service: save Question → `answer_question` → persist Answer/Citations in a transaction → FAILED on exception.
- Upload the three real PDFs as Admin through the UI; run the evaluation table.

**Acceptance:** full test suite green on `main`; all 13 evaluation questions behave as expected (or deviations documented).

## Phase 13 — Demo preparation (A+B, ~20 min) — depends on 11, 12
Rehearse the brief's demonstration checklist:
1. Supported question → open cited page (Q3 or Q6).
2. Question needing both HP-200 manuals (Q9).
3. Evidence only on the other machine → isolation shown (Q10 / Q11).
4. Unanswerable question (Q12).
5. Engineer upload attempt → denial; reopen stored history as Engineer and as Supervisor.

Also: model pre-downloaded, DB seeded, LLM key loaded from env, each member can explain their contribution (brief requirement), `python manage.py test` output ready to show.

## Open questions (need answers, not assumptions)

1. **LLM provider/model** — brief says model access is "announced separately". Which API is approved, and is there a rate limit?
2. **Network at evaluation** — is internet available (LLM API, Bootstrap CDN, Hugging Face download)? Determines vendoring Bootstrap and pre-caching the model.
3. **Database** — is PostgreSQL available in the lab, or is MySQL mandated there?
4. **Admin asking questions** — should Admin also use the assistant? Brief lists asking under Engineer only; default is Engineer-only.
5. **"Unanswered queries"** — interpreted as `INSUFFICIENT_EVIDENCE` + `FAILED`. Confirm.
6. **Abstention citations** — when a manual explicitly says information is not provided (e.g. pump price), should the insufficient-evidence response cite that passage, or show no citations?
7. **Supervisor access to PDFs** — proposed yes (to verify logged citations); confirm.
8. **Team size** — brief requires 3–4 students and that every member explain their contribution; this plan covers 2 active developers. Should roles be split for additional members?
9. **Repository state** — root `README.md` is deleted in the working tree and a `challenge-pack/README.md` exists untracked; `challenge-pack/` (including the original ZIP) is untracked. Should `challenge-pack/` be committed (manuals needed by both developers) or kept local?
10. **AI-assistant policy** — brief references the college's policy on AI coding assistants; confirm usage is permitted and whether it must be declared.
