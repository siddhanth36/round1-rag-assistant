# Part B — Django backend design (INDUSTRIX AI)

Status: **design, awaiting approval. No code written.** Branch: `feature/django-product`.
Ponytail is ON: least code that fully meets the brief; deliberate shortcuts are marked
**ponytail:** with the condition that should trigger an upgrade. Ponytail never cuts security,
server-side permissions or tests.

Everything about `rag/` below was read from the code on `origin/feature/rag-engine` (commit `ac090f2`),
not from the docs. Where the docs and the code disagree, the code wins and the mismatch is listed in §9.

---

## 1. Layout and apps

Proposed in the brief: `config, accounts, machines, manuals, assistant, supervisor` + `frontend/`.
**Ponytail result: 3 apps.**

```
repo root/
├── manage.py                    # at the root, next to rag/ → `import rag` works with no sys.path hacks
├── requirements.txt             # -r rag/requirements.txt + Django + DRF + psycopg (no re-pinning rag deps)
├── rag/                         # read-only for Part B
├── backend/
│   ├── config/                  # settings.py, urls.py, wsgi.py   (asgi.py cut: no async server)
│   ├── rag_runtime.py           # lazy, thread-safe store/LLM singletons + write lock (§5)
│   ├── accounts/                # User(role), login/logout/me API, role permission classes, page-role decorator
│   ├── machines/                # Machine + Manual, machine & manual APIs, upload + indexing, PDF stream
│   └── assistant/               # Question/Answer/Citation, ask, history, supervisor log + analytics
└── frontend/
    ├── templates/               # Django templates (base + one per page)
    └── static/                  # css/ js/ vendor/ (Bootstrap, Bootstrap Icons sprite, GSAP, fonts)
```

| Cut | Why it is safe |
|---|---|
| `manuals` → merged into `machines` | Manual is a child of Machine (FK, same lifecycle, same admin owner). Two apps would just add a cross-app import for every query. |
| `supervisor` → merged into `assistant` | It owns no models: two read-only views over `Question`/`Answer`. A separate app is a folder of imports. |
| Kept `accounts` separate | The custom `User` must exist before the first migration (`AUTH_USER_MODEL`); isolating it keeps that migration stable while the others churn. Also maps cleanly to the team split (brief: every member explains their part). |

Apps are importable as `backend.accounts` etc. with short labels (`accounts`, `machines`, `assistant`) so
migrations and `AUTH_USER_MODEL = "accounts.User"` stay readable.

## 2. Database schema

Portable ORM only (works on PostgreSQL and MySQL). IDs exactly as `machine_manual_index.csv`.

### accounts.User (extends `AbstractUser`)
| Field | Type | Notes |
|---|---|---|
| role | CharField(12), choices `ADMIN` / `ENGINEER` / `SUPERVISOR`, indexed | required; drives every permission |

`is_staff`/`is_superuser` only gate Django's `/admin/` (used to create users). The app never reads them.

### machines.Machine
| Field | Type | Notes |
|---|---|---|
| machine_id | CharField(32), **unique** | `M-HP200`, `M-AC90`; regex `^[A-Z0-9-]+$`; immutable after create (it is stored in every vector record) |
| model | CharField(32), **unique** | `HP-200`, `AC-90` |
| machine_name | CharField(120) | `Hydraulic forming press`, `Air compressor` |

ponytail: no `is_active` / `description` / `created_at`: the brief never deactivates machines. Add `is_active` when a machine can be retired.

### machines.Manual
| Field | Type | Notes |
|---|---|---|
| machine | FK Machine, `PROTECT` | |
| document_id | CharField(32) | `HP-OPS-01`, `HP-MNT-01`, `AC-OM-01`; regex `^[A-Z0-9][A-Z0-9-]{0,31}$` |
| revision | CharField(16) | `1.0`; regex `^[0-9A-Za-z][0-9A-Za-z.-]{0,15}$` (label, not a number) |
| filename | CharField(255) | original upload name, shown in citations (`HP200_Operations_Rev1_0.pdf`) |
| file | FileField | stored as `manuals/<machine_id>/<document_id>_rev<revision>.pdf`, a name **we** build from validated fields, never from the upload name |
| page_count | PositiveSmallIntegerField, null | from `ExtractedDocument.page_count`; bounds the page endpoint and citation pages |
| indexing_status | CharField(12), choices `PROCESSING` / `INDEXED` / `FAILED` | |
| indexing_error | TextField, blank | admin-only diagnostic; never secrets |
| uploaded_by | FK User, `PROTECT` | must be ADMIN (enforced by the view's permission) |
| uploaded_at | DateTimeField auto_now_add | |

Constraint: `UniqueConstraint(document_id, revision)`. Service check: a `document_id` already used by
another machine is rejected with 400 (rag would also raise `InvalidChunkError`).

ponytail: no `is_current` column. "Current" = the latest `INDEXED` revision of a `document_id` (by `uploaded_at`), which is exactly what `index_manual(make_current=True)` makes current in Chroma. Add the column when admins need to roll back to an older revision.
ponytail: no `IngestionJob`, `file_sha256`, `file_type`, `document_name`. Ingestion is synchronous and 3 pages; `indexing_status` + `indexing_error` answer "is it usable and why not". The brief accepts "document name **or** ID".
ponytail: a `FAILED` row blocks re-uploading the same document+revision (unique constraint); the admin deletes it in `/admin/` first. Add a "retry" action if failures become routine.

### assistant.Question
| Field | Type | Notes |
|---|---|---|
| user | FK User, `PROTECT` | asker |
| machine | FK Machine, `PROTECT` | selected machine, resolved server-side |
| text | TextField | trimmed, 1–1000 chars (same limit as rag's `max_question_chars`) |
| created_at | DateTimeField auto_now_add, indexed | |

Saved **before** RAG runs, so failures are still logged.

### assistant.Answer (one-to-one with Question)
| Field | Type | Notes |
|---|---|---|
| question | OneToOneField Question, `CASCADE`, `related_name="answer"` | |
| text | TextField | validated answer with `[S#]` tags, or a fixed system message |
| status | CharField(24), choices `answered` / `insufficient_evidence` / `error`, indexed | the brief's explicit status (+ `error`) |
| retrieval_trace | JSONField | `asdict(result.retrieval)` + `rag_status`, `llm_model`, `rejection_reasons`, `error`, `dropped_citations`; staff-only |
| latency_ms | PositiveIntegerField | wall time around `answer_question` |

An Answer row is written for **every** outcome, including errors, so "unanswered" =
`status != answered` or no Answer row (crashed mid-request). No `PENDING` enum needed.

### assistant.Citation
| Field | Type | Notes |
|---|---|---|
| answer | FK Answer, `CASCADE`, `related_name="citations"` | |
| manual | FK Manual, `PROTECT` | pins the exact revision + file; cited manuals can never be deleted |
| document_id | CharField(32) | snapshot from the rag Citation (always equals `manual.document_id`) |
| revision | CharField(16) | snapshot (always equals `manual.revision`) |
| pdf_page | PositiveSmallIntegerField | one-based; `1 ≤ pdf_page ≤ manual.page_count` checked before insert |
| passage | TextField | snapshot of the chunk text, so history survives re-indexing |
| source_tag | CharField(8) | `S1`… links the `[S#]` markers in `Answer.text` |

Constraint: `UniqueConstraint(answer, source_tag)`. Display order = insertion order (rag returns citations deduplicated in first-use order).

## 3. REST contract

Conventions: JSON; base `/api/`; session cookie + `X-CSRFToken` on every unsafe method (§7);
timestamps ISO 8601 with offset; machines addressed by `machine_id`, manuals by numeric `id`.

| Code | When |
|---|---|
| 400 | validation error: `{"field": ["message"]}` or `{"detail": "..."}` |
| 401 | not logged in: `{"detail": "Authentication credentials were not provided."}` |
| 403 | logged in, wrong role (e.g. **Engineer upload**), or CSRF failure |
| 404 | unknown machine/manual/page |
| 405 | write method on a read-only resource (no PUT/PATCH/DELETE routes exist for Q/A/citations) |

401 vs 403: DRF's `SessionAuthentication` returns 403 for anonymous users because it sends no
`WWW-Authenticate` header. A 3-line subclass adds `authenticate_header() -> 'Session'` so anonymous
requests get **401**. (Changes API_CONTRACT, see §9.)

Shared shapes:
```json
Machine  { "machine_id": "M-HP200", "model": "HP-200", "machine_name": "Hydraulic forming press", "indexed_manual_count": 2 }

Manual   { "id": 1, "machine_id": "M-HP200", "model": "HP-200", "document_id": "HP-OPS-01", "revision": "1.0",
           "filename": "HP200_Operations_Rev1_0.pdf", "page_count": 3, "indexing_status": "INDEXED",
           "uploaded_by": "admin1", "uploaded_at": "2026-11-03T12:40:00+05:30", "file_url": "/api/manuals/1/file/" }
           // ADMIN only, extra: "indexing_error": ""

Citation { "source_tag": "S1", "manual_id": 1, "document_id": "HP-OPS-01", "revision": "1.0", "pdf_page": 2,
           "passage": "<verbatim chunk text>", "source_url": "/api/manuals/1/file/#page=2",
           "viewer_url": "/manuals/1/view/?page=2" }

Question { "id": 42, "machine": { "machine_id": "M-HP200", "model": "HP-200", "machine_name": "Hydraulic forming press" },
           "question": "What does E17 mean?", "created_at": "<ISO>",
           "status": "answered" | "insufficient_evidence" | "error",
           "answer": "<text>", "latency_ms": 2310, "citations": [Citation, ...] }
           // supervisor/admin views, extra: "asked_by": "eng1", "retrieval_trace": { ... }
```

### Auth
| Endpoint | Roles | Request | Success | Errors |
|---|---|---|---|---|
| `POST /api/auth/login/` | anonymous | `{"username": "eng1", "password": "…"}` | 200 `{"id": 7, "username": "eng1", "role": "ENGINEER", "redirect": "/"}`; session rotated | 400 `{"detail": "Invalid username or password."}` (same message for both cases: no user enumeration); 403 missing CSRF |
| `POST /api/auth/logout/` | any authenticated | `{}` | 204 | 401, 403 CSRF |
| `GET /api/auth/me/` | any authenticated | | 200 `{"id": 7, "username": "eng1", "role": "ENGINEER"}` | 401 |

The login view is decorated `csrf_protect` explicitly: DRF only enforces CSRF for *authenticated* session
requests, so an undecorated login endpoint would be open to login-CSRF. The login page template sets the
CSRF cookie (`ensure_csrf_cookie`).
ponytail: no login rate limiting / lockout. Add `django-axes` (or a cache counter) before any internet-facing deployment.

### Machines
| Endpoint | Roles | Request | Success | Errors |
|---|---|---|---|---|
| `GET /api/machines/` | ADMIN, ENGINEER, SUPERVISOR | | 200 `[Machine, ...]` ordered by `machine_id` | 401 |
| `POST /api/machines/` | ADMIN | `{"machine_id": "M-AC90", "model": "AC-90", "machine_name": "Air compressor"}` | 201 `Machine` | 400 `{"machine_id": ["machine with this machine id already exists."]}` / bad format; 401; 403 |

### Manuals
| Endpoint | Roles | Request | Success | Errors |
|---|---|---|---|---|
| `GET /api/manuals/?machine_id=M-HP200` | all roles | optional filter | 200 `[Manual, ...]` | 401; 400 unknown `machine_id` filter |
| `POST /api/manuals/upload/` | **ADMIN only** | multipart: `machine_id`, `document_id`, `revision`, `file` | 201 `Manual` with `indexing_status: "INDEXED"`; or 201 with `"FAILED"` + `indexing_error` when the PDF was valid but indexing failed (row kept for audit) | 400 `{"file": ["Upload a PDF file (max 20 MB)."]}`, `{"non_field_errors": ["HP-MNT-01 revision 1.0 already exists."]}`, `{"document_id": ["HP-OPS-01 belongs to M-HP200."]}`, unknown machine; 401; **403 ENGINEER / SUPERVISOR, with no Manual row and no file written** |
| `GET /api/manuals/{id}/` | all roles | | 200 `Manual` | 401, 404 |
| `GET /api/manuals/{id}/page/{page}/` | all roles | | 200 `{"manual": Manual, "pdf_page": 2, "page_count": 3, "prev_page": 1, "next_page": 3, "file_url": "/api/manuals/1/file/#page=2"}` | 401; 404 if the manual is not `INDEXED` or `page` is outside `1..page_count` |
| `GET /api/manuals/{id}/file/` | all roles | | 200 `application/pdf`, `Content-Disposition: inline; filename="<filename>"`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN` (the viewer embeds it in an iframe; Django's default `DENY` would block that) | 401, 404 |

Upload validation, all before anything is written: role (permission class runs before the body is read);
machine exists; `document_id`/`revision` match their regexes (this also blocks path traversal, since they
build the stored path); `(document_id, revision)` is new; `document_id` is not used by another machine;
extension `.pdf`; size ≤ 20 MB; first bytes `%PDF-`. Real PDF parsing is left to `rag.extract_pdf`, which
raises `InvalidPdfError` for corrupt or encrypted files (→ 400, file deleted, no row).
The file endpoint streams with `FileResponse`. PDFs are never under `STATIC`/`MEDIA_URL`: `MEDIA_ROOT`
is not routed at all.

### Assistant, history, supervisor
| Endpoint | Roles | Request | Success | Errors |
|---|---|---|---|---|
| `POST /api/assistant/ask/` | **ENGINEER** | `{"machine_id": "M-HP200", "question": "What does E17 mean?"}` | **201** `Question` for every recorded outcome; `status` carries the result (§4) | 400 `{"question": ["Ask a question of 1 to 1000 characters."]}`, `{"machine_id": ["Unknown machine."]}`; 401; 403 ADMIN / SUPERVISOR |
| `GET /api/history/?machine_id=&status=&page=` | **ENGINEER**, own questions only (queryset filtered by `request.user`, never by a parameter) | | 200 `{"count", "next", "previous", "results": [Question, ...]}`, newest first, 25 per page | 401; 403 other roles |
| `GET /api/supervisor/questions/?status=…&unanswered=true&machine_id=&page=` | SUPERVISOR, ADMIN | `status` repeatable; `unanswered=true` = `insufficient_evidence` + `error` + no answer | 200 paginated `Question` incl. `asked_by`, `retrieval_trace` | 401; 403 ENGINEER; 400 bad `status` value |
| `GET /api/supervisor/analytics/?machine_id=` | SUPERVISOR, ADMIN | | 200 `{"total": 40, "unanswered": 9, "by_status": {"answered": 31, "insufficient_evidence": 7, "error": 2}, "by_machine": [{"machine_id": "M-HP200", "model": "HP-200", "total": 25, "answered": 20, "insufficient_evidence": 4, "error": 1}], "avg_latency_ms": 2140}` | 401; 403 ENGINEER |

Why 201 for `error` too: the request *did* succeed at what the API promises (the question is logged with
an explicit outcome), and one response shape keeps the frontend to a single render path. The old contract's
502/503 is in §9.
ponytail: no question detail endpoint; list items already carry answer + citations. Add `GET /api/history/{id}/` if a deep link to one question is needed.
ponytail: ADMIN cannot ask (brief lists asking under Engineer only; open question in IMPLEMENTATION_PLAN).

### HTML pages (server-side role check on each; anonymous → `/login/`)
`/login/` · `/` (role-aware dashboard) · `/assistant/?machine=M-HP200` (ENGINEER) · `/manuals/` (all) ·
`/manuals/<id>/view/?page=N` (all) · `/manage/` (ADMIN: register machine + upload) · `/history/` (ENGINEER) ·
`/supervisor/` (SUPERVISOR, ADMIN). Hiding nav links is cosmetic; the APIs enforce everything.

## 4. RAG status → API status (from `rag/generation/answer_service.py`)

| What rag does | `Answer.status` | `Answer.text` | Citations |
|---|---|---|---|
| returns `AnswerStatus.ANSWERED` and ≥1 citation resolves (§5) | `answered` | `result.answer` | resolved ones |
| returns `ANSWERED` but resolution leaves the text with an unresolved `[S#]` | `insufficient_evidence` | `INSUFFICIENT_EVIDENCE_MESSAGE` | none |
| returns `INSUFFICIENT_EVIDENCE` (nothing within `RAG_MAX_DISTANCE`, LLM abstained, or draft still unsupported after `RAG_MAX_CORRECTIONS`) | `insufficient_evidence` | `INSUFFICIENT_EVIDENCE_MESSAGE` | none |
| returns `PROVIDER_UNAVAILABLE` (evidence found, `llm is None`) | `error` | fixed UI message; `rag_status` kept in trace | none |
| returns `GENERATION_ERROR` (provider raised, or output malformed after corrections) | `error` | fixed UI message | none |
| raises `VectorStoreError` / `EmbeddingModelError` / any other `RagError` | `error` | fixed UI message; exception class + message in trace | none |
| `create_llm_client()` raises `ConfigError` (unknown `LLM_PROVIDER`) | `error` | fixed UI message; logged at ERROR | none |
| raises `InvalidMetadataError` | (never reached: the serializer enforces the same rules first) → 400 | — | — |
| unexpected `Exception` | `error`, logged with traceback | fixed UI message | none |

Engineers see only `status`, `answer`, `citations`. `retrieval_trace` (`rejection_reasons`, `error`,
distances, `rag_status`) is returned only to SUPERVISOR/ADMIN and is free of secrets: rag guarantees
that for its own messages, and the key never leaves `LLMConfig` (`repr=False`).

## 5. Citation resolution rule

The rag `Citation` fields already come from retrieved Chroma records, never from LLM text. Django adds a
second, database-side check, because the DB is the source of truth and Chroma is a derived index:

1. Load the selected machine's `INDEXED` manuals once: `{(document_id, revision): manual}`.
2. For each rag citation, keep it only if **all** hold: `c.machine_id == question.machine.machine_id`,
   `(c.document_id, c.revision)` is in that dict, and `1 ≤ c.pdf_page ≤ manual.page_count`.
3. Every dropped citation is logged at WARNING (question id, tag, document, revision, page, reason) and
   recorded in `retrieval_trace.dropped_citations`.
4. **Stricter than "none left":** if any `[S#]` tag in the answer text no longer has a kept citation, the
   whole result becomes `insufficient_evidence` with the fixed message. Otherwise a statement would be
   shown with a source we can't show, which is the same as an invented citation.
   (This includes the "no valid citations left" case. **Needs your approval:** it is stricter than the rule
   you wrote.)
5. Citations are never created, edited or re-numbered by Django; we copy `source_tag`, `document_id`,
   `revision`, `pdf_page` and `passage` verbatim. `Answer` + `Citation` rows are written in one transaction.

## 6. RAG lifecycle (`backend/rag_runtime.py`)

```python
_init_lock = threading.Lock()
_write_lock = threading.Lock()      # serialises index_manual / remove_manual (rag thread-safety untested)
_store = None; _llm = None; _llm_loaded = False

def get_store():                    # first ask or first upload builds it (~10 s cold model load), then reused
    global _store
    if _store is None:              # double-checked: no lock cost after the first call
        with _init_lock:
            if _store is None:
                _store = ChromaVectorStore(get_embedder())   # CHROMA_DIR / EMBEDDING_* from env
    return _store
# get_llm(): same pattern around create_llm_client(); returns None while no provider is configured
```

- Never built in `AppConfig.ready()`, so `migrate`, `makemigrations`, `check` and the test runner never load torch.
- One Django process (`runserver`; its autoreloader parent never serves requests, so it never builds the store).
  Several processes on one embedded Chroma directory is unsupported by rag (contract §2). For production,
  run one gunicorn worker with threads.
- Tests patch `backend.rag_runtime.get_store` / `get_llm` with a temp-dir `ChromaVectorStore(HashingEmbedder())`
  and `ScriptedLLM` from `rag.tests.support`: real extraction, chunking, Chroma filtering, no model download.

## 7. Indexing on upload (rag's own API, no duplicated chunking/embedding)

```
validated upload ──► save file + Manual(PROCESSING)                       (commit)
  doc    = rag.ingestion.pdf_extractor.extract_pdf(path, machine_id=machine.machine_id,
             document_id=…, revision=…, source_filename=manual.filename)
  chunks = rag.ingestion.chunker.chunk_document(doc)
  with _write_lock: summary = get_store().index_manual(chunks, make_current=True)
  ──► Manual(INDEXED, page_count=doc.page_count)
on InvalidPdfError                 ──► delete file + row, 400 {"file": [...]}
on any other RagError / Exception  ──► with _write_lock: store.remove_manual(machine_id=…, document_id=…, revision=…)
                                       Manual(FAILED, indexing_error="<ExcClass>: <msg>"), 201
```
ponytail: `make_current=True` in one call instead of the contract's staged `make_current=False` → DB commit → `set_current_revision`. The DB has no `is_current` column to keep in sync (§2), so the staged dance buys nothing yet. Switch to the staged flow when `is_current` is added.
ponytail: synchronous indexing inside the request (3-page PDFs take seconds once the model is warm). Move to a background worker when manuals get large.

## 8. Auth: session + CSRF

Same-origin Django templates calling same-origin APIs is exactly what Django session auth is built for:
- the session id lives in an **HttpOnly** cookie, so injected JS can't read or exfiltrate it (unlike a JWT in `localStorage`);
- no token issuing, refresh, revocation or storage code to write: `login()` / `logout()` rotate and flush sessions;
- CSRF protection covers every unsafe method: `fetch` sends `X-CSRFToken` read from the `csrftoken` cookie;
  `SameSite=Lax` cookies add defence in depth;
- DRF `SessionAuthentication` (401 subclass, §3) + role permission classes `IsAdmin`, `IsEngineer`,
  `IsSupervisorOrAdmin`; querysets are scoped server-side (engineer history = `user=request.user`).

Settings: `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`, `CSRF_COOKIE_SAMESITE="Lax"`,
`SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` from env (True behind HTTPS), `SECRET_KEY`/DB credentials/`DEBUG`
from env only. JWT would only pay off for third-party or mobile API clients, which the brief doesn't have.
ponytail: no Content-Security-Policy header (would need `django-csp`). Templates are written CSP-ready (no inline scripts or handlers, all dynamic text set with `textContent`); add the header before public deployment.

## 9. Mismatches to raise with Developer A

Repository / process
1. `CLAUDE.md`, `docs/` and `rag/` exist **only** on `feature/rag-engine`. `main` and `feature/django-product` contain just `README.md` and `.gitignore`. IMPLEMENTATION_PLAN Phase 1 ("commit docs to main, merge into both branches") hasn't happened. Part B needs `rag/` on this branch (merge `main` once A has merged the docs and `rag/` into it).
2. `challenge-pack/Manuals/*.pdf` and `machine_manual_index.csv` are whitelisted in `.gitignore` as "kept" but **not committed**. `rag/tests/conftest.py` raises `FileNotFoundError` at import on a fresh clone, so the teammate's 233 can't be reproduced from Git alone.
3. **No LLM adapter exists** (`_ADAPTERS = {}`), so every answerable question returns `provider_unavailable` → `error`. The brief requires an LLM, and demo items 1 and 2 can't pass without one. Blocked on open question 1 (approved provider).
4. Python: rag was tested on 3.13 (Kali); this machine has **3.14 only**. The test run here confirms whether the pins resolve on 3.14. Django must be a release that supports 3.14 (6.0.x).
5. **PostgreSQL is not installed** on this machine; the brief requires PostgreSQL or MySQL. Decide: install PG locally (preferred), or MySQL.

Docs vs code (stale text)
6. `CLAUDE.md` §7 "`fitz` can open it" and PROJECT_ARCHITECTURE §6 pseudo-flow (`fitz.open`, `rag.index_manual(...)`, `rag.set_current_revision(document_id, revision)`, `rag.remove_manual(document_id, revision)`) don't match the real API: `pymupdf`, store methods are keyword-only and require `machine_id`, `remove_manual` requires `revision`.
7. DJANGO_INTEGRATION_EXAMPLE decides 503 vs 502 by comparing `error_message == "provider_unavailable"` (string coupling). Not used; we map from `result.status`.
8. The Django test example imports `rag.tests.support` (`HashingEmbedder`, `ScriptedLLM`). Ask A to treat these as a stable test API (we'll depend on them).

Contract changes Part B is proposing (API_CONTRACT / DATABASE_ARCHITECTURE need updating; they're shared docs)
9. Paths: `POST /api/manuals/` → `/api/manuals/upload/`; `/api/questions/` (POST, GET, detail) → `/api/assistant/ask/`, `/api/history/`, `/api/supervisor/questions/`; new `/api/manuals/{id}/`, `/page/{page}/`, `/api/supervisor/analytics/`; HTML `LoginView` → JSON `POST /api/auth/login/` + `/logout/`.
10. Anonymous API requests: contract says 403 (DRF default); Part B returns **401**.
11. RAG failures: contract says 502/503 with no Answer row; Part B returns **201** with `status: "error"` and always writes an Answer row.
12. Status vocabulary: contract/DB use `ANSWERED` / `INSUFFICIENT_EVIDENCE` / `FAILED` / `PENDING` on `Question`; Part B uses `answered` / `insufficient_evidence` / `error` on `Answer` (the same lowercase strings as rag where they overlap).
13. Schema: Machine `model_code`/`name` → CSV names `model`/`machine_name`; dropped `is_active`, `description`, `document_name`, `file_type`, `file_sha256`, `is_current`, `IngestionJob`, `Citation.chunk_id`/`rank`, `Answer.llm_model`/`retrieved_count` (now in `retrieval_trace`); added `Manual.page_count`, `Manual.indexing_error`, `Answer.retrieval_trace`, `Answer.latency_ms`, `Citation.document_id`/`revision` snapshots. The PostgreSQL-only partial unique constraint is gone with `is_current`.
14. Upload uses `index_manual(make_current=True)` (contract §8 recommends staged). Fine by the contract ("acceptable when DB/vector consistency on failure is not a concern"), but A should know.

Open product questions (from IMPLEMENTATION_PLAN, still unanswered)
15. Can ADMIN ask questions? (Part B: no.) 16. "Unanswered" = `insufficient_evidence` + `error` (Part B: yes). 17. Should an insufficient-evidence answer cite a passage that says "information not provided" (e.g. pump price)? (rag: no citations; Part B follows.) 18. Is there internet at evaluation? (Part B vendors Bootstrap, fonts and GSAP locally regardless.) 19. Team of 3–4 vs a 2-developer plan: who owns frontend vs Django APIs? 20. The distance threshold (0.80) was tuned without an LLM. Re-run `python -m rag.evaluation` once a provider is live.

## 10. Tests Part B will add (Django `APITestCase`, offline, no model download)

1. **Mandatory: Engineer cannot upload:** `POST /api/manuals/upload/` as ENGINEER → 403, `Manual.objects.count()` unchanged, `MEDIA_ROOT` empty. Same for SUPERVISOR; ADMIN → 201 `INDEXED` (real PDF, temp Chroma, `HashingEmbedder`).
2. **Mandatory, Django path: retrieval excludes the other machine:** index all three PDFs; ask "What does E17 mean?" on M-AC90 and on M-HP200; every `retrieval_trace.evidence` document and every saved citation belongs to the selected machine; a spy confirms `store.search` received `Machine.machine_id` from the DB row. (rag's own isolation tests stay in `rag/tests/`.)
3. Citation resolution: unknown document, foreign machine, page > `page_count` → dropped + logged → `insufficient_evidence`; partially unresolved `[S#]` → `insufficient_evidence`.
4. Status mapping: `provider_unavailable`, `generation_error`, raised `VectorStoreError`, `ConfigError` → `error`, Question + Answer rows still saved; engineer response has no `retrieval_trace`.
5. Permissions: engineer history shows only own questions; engineer → supervisor endpoints 403; supervisor/admin → ask 403; PUT/PATCH/DELETE → 405; anonymous API → 401; anonymous page → redirect to `/login/`.
6. Auth: login without CSRF token → 403; wrong password → 400 with the generic message; logout flushes the session.
7. Files: file endpoint requires auth and sends `nosniff` + `SAMEORIGIN`; page endpoint 404 outside `1..page_count`; upload rejects non-PDF magic bytes, > 20 MB, traversal-shaped `document_id`.

Tests run on the configured database (PostgreSQL), plus `python -m pytest rag/tests` unchanged.
