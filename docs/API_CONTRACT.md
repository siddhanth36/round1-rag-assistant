# API Contract — INDUSTRIX AI

Status: **Design (pre-implementation)**.

The brief requires DRF APIs and the workflows (Admin: register machines, upload manuals; Engineer: ask, read answer, open cited page, saved history; Supervisor: review question log, identify unanswered queries) but does **not** specify endpoint paths or payloads. **Every path and field below is a proposed implementation detail** that satisfies those required workflows. Items marked *(optional)* are not required to meet the brief.

## Conventions

- Base path: `/api/`. JSON request/response unless stated (`multipart/form-data` for uploads).
- Auth: Django session cookie (`SessionAuthentication`). Unsafe methods (POST/PUT/PATCH/DELETE) require header `X-CSRFToken`.
- Timestamps: ISO 8601 with timezone (`USE_TZ = True`).
- Machines are addressed by business key `machine_id` (e.g. `M-HP200`); manuals by numeric `id`.
- Errors:

```json
{ "detail": "You do not have permission to perform this action." }       // 401/403
{ "field_name": ["Error message."] }                                        // 400 validation
```

| Status | Meaning |
|---|---|
| 200 / 201 | OK / created |
| 400 | Validation error |
| 403 | Not authenticated or wrong role (DRF returns 403 for session auth) |
| 404 | Unknown machine/manual/question, or a record the user may not see |
| 502 | LLM/vector store failure (question saved as `FAILED`) |

## 1. Authentication

### `GET/POST /accounts/login/` — Django `LoginView` (HTML form)
Server-rendered login page; on success redirects by role (Engineer → assistant, Supervisor → log, Admin → manuals). Not a JSON API.

### `POST /accounts/logout/` — Django `LogoutView`

### `GET /api/auth/me/` — current user — any authenticated role
```json
200 { "id": 7, "username": "eng1", "role": "ENGINEER" }
```
Used by JS to adapt the UI; server still enforces all permissions.

## 2. Machines

### `GET /api/machines/` — ADMIN, ENGINEER, SUPERVISOR
Engineers/Supervisors see active machines; Admin sees all (`?include_inactive=true`).
```json
200 [
  { "machine_id": "M-HP200", "model_code": "HP-200", "name": "Hydraulic forming press",
    "description": "", "is_active": true, "indexed_manual_count": 2 },
  { "machine_id": "M-AC90", "model_code": "AC-90", "name": "Air compressor",
    "description": "", "is_active": true, "indexed_manual_count": 1 }
]
```

### `POST /api/machines/` — ADMIN only
```json
request  { "machine_id": "M-AC90", "model_code": "AC-90", "name": "Air compressor", "description": "" }
201      { "machine_id": "M-AC90", "model_code": "AC-90", "name": "Air compressor",
           "description": "", "is_active": true, "indexed_manual_count": 0 }
400      { "machine_id": ["machine with this machine id already exists."] }
403      ENGINEER / SUPERVISOR
```

### `PATCH /api/machines/{machine_id}/` — ADMIN only *(optional)*
Editable: `name`, `description`, `is_active`. `machine_id` is immutable.

## 3. Manuals

### `GET /api/manuals/?machine_id=M-HP200` — ADMIN, ENGINEER, SUPERVISOR
```json
200 [
  { "id": 1, "machine_id": "M-HP200", "document_id": "HP-OPS-01",
    "document_name": "HP 200 Operations Manual", "revision": "1.0",
    "original_filename": "HP200_Operations_Rev1_0.pdf", "page_count": 3,
    "status": "INDEXED", "is_current": true,
    "uploaded_by": "admin1", "uploaded_at": "<ISO 8601>",
    "file_url": "/api/manuals/1/file/" }
]
```
Admin additionally receives `latest_job: { status, pages_processed, chunks_indexed, error_message, started_at, completed_at }`.

### `POST /api/manuals/` — ADMIN only — `multipart/form-data`
| Field | Required | Notes |
|---|---|---|
| machine_id | yes | existing, active machine |
| document_id | yes | e.g. `HP-MNT-01` |
| document_name | yes | e.g. `HP 200 Maintenance Manual` |
| revision | yes | e.g. `1.0`; `(document_id, revision)` must be new |
| file | yes | PDF only; size limit (proposed 20 MB); must open with PyMuPDF |

Ingestion runs synchronously; response reflects the result.
```json
201 { "id": 2, "machine_id": "M-HP200", "document_id": "HP-MNT-01", "revision": "1.0",
      "status": "INDEXED", "is_current": true, "page_count": 3,
      "latest_job": { "status": "SUCCEEDED", "pages_processed": 3, "chunks_indexed": <n>,
                      "error_message": "", "started_at": "<ISO>", "completed_at": "<ISO>" } }
201 (ingestion failed: manual stored, not searchable)
    { "id": 2, ..., "status": "FAILED", "is_current": false,
      "latest_job": { "status": "FAILED", "error_message": "<summary>" } }
400 { "file": ["Only PDF files are accepted."] }
400 { "non_field_errors": ["HP-MNT-01 revision 1.0 already exists."] }
403 { "detail": "You do not have permission to perform this action." }   // ENGINEER, SUPERVISOR
```
**Mandated automated check:** an ENGINEER `POST` returns 403 and creates no `Manual` row and no file.

### `GET /api/manuals/{id}/file/` — any authenticated role
Streams the PDF (`Content-Type: application/pdf`, `Content-Disposition: inline`). Links append `#page=N` client-side to open the cited page. 404 if not found.

### `POST /api/manuals/{id}/reindex/` — ADMIN only *(optional)*
Re-runs ingestion for a stored manual (e.g. after a failed job). Returns the manual payload above.

## 4. Questions & answers

### `POST /api/questions/` — ENGINEER only
```json
request { "machine_id": "M-HP200", "question": "What does E17 mean?" }
```
Validation: machine exists and is active; question 1–1000 chars after trimming.

**Answered response — 201**
```json
{
  "id": 42,
  "machine": { "machine_id": "M-HP200", "model_code": "HP-200", "name": "Hydraulic forming press" },
  "question": "What does E17 mean?",
  "status": "ANSWERED",
  "created_at": "<ISO 8601>",
  "answer": {
    "text": "<validated answer; each statement ends with its source tags, e.g. [S1]>",
    "created_at": "<ISO 8601>"
  },
  "citations": [
    {
      "rank": 1,
      "source_tag": "S1",
      "document_id": "HP-OPS-01",
      "document_name": "HP 200 Operations Manual",
      "revision": "1.0",
      "pdf_page": 2,
      "passage": "<verbatim chunk text from HP-OPS-01 page 2>",
      "source_url": "/api/manuals/1/file/#page=2",
      "chunk_id": "HP-OPS-01:1.0:p2:c1"
    }
  ]
}
```
Multi-document answers return one citation per supporting chunk (e.g. HP-OPS-01 p.2 **and** HP-MNT-01 p.3).
`source_tag` links the `[S#]` markers in `answer.text` to citation cards. `document_name` and `source_url`
come from the Django `Manual` row; every other citation field comes from the RAG `Citation`, which is
resolved from the retrieved record (never from LLM text).

**Insufficient-evidence response — 201**
```json
{
  "id": 43,
  "machine": { "machine_id": "M-HP200", "model_code": "HP-200", "name": "Hydraulic forming press" },
  "question": "What is the pump replacement price?",
  "status": "INSUFFICIENT_EVIDENCE",
  "created_at": "<ISO 8601>",
  "answer": {
    "text": "The available manuals for the selected machine do not contain enough information to answer this question.",
    "created_at": "<ISO 8601>"
  },
  "citations": []
}
```
- `answer.text` for this status is the fixed RAG message (`INSUFFICIENT_EVIDENCE_MESSAGE`), never LLM text.
  Django may substitute the model code (e.g. "for HP-200") when displaying it.
- Implemented behaviour: insufficient-evidence results carry **no citations** (the open question about
  citing an explicit "information not provided" passage remains open).

**Failure — 502 / 503**
```json
{ "id": 44, "status": "FAILED", "detail": "The assistant could not complete this request. It has been logged." }
```
- 503 when the RAG result is `provider_unavailable` (no LLM configured); 502 for `generation_error`
  (provider failure or malformed LLM output after correction) or a vector-store error.

**RAG status → API mapping** (proposed for Django integration; RAG side implemented and tested):

| `AnswerResult.status` (rag) | Question/Answer `status` | HTTP | Answer row | Citations |
|---|---|---|---|---|
| `answered` | `ANSWERED` | 201 | validated text | ≥ 1, resolved from retrieved records |
| `insufficient_evidence` | `INSUFFICIENT_EVIDENCE` | 201 | fixed message | none |
| `provider_unavailable` | `FAILED` | 503 | none | none |
| `generation_error` | `FAILED` | 502 | none | none |
| `InvalidMetadataError` raised | — (not saved) | 400 | — | — |
| `VectorStoreError` raised | `FAILED` | 502 | none | none |

`rejection_reasons` and `error` are for server logs / admin diagnostics only and are not returned to engineers.

**Errors:** 400 (empty/too long question, unknown/inactive machine), 403 (non-engineer).

### `GET /api/questions/{id}/` — owner ENGINEER, SUPERVISOR, ADMIN
Same shape as the POST response. Engineers get 404 for other users' questions.

## 5. History

### `GET /api/questions/` — role-scoped list
| Role | Scope |
|---|---|
| ENGINEER | own questions only (filter forced server-side) |
| SUPERVISOR, ADMIN | all questions |

Query params: `status` (`ANSWERED`, `INSUFFICIENT_EVIDENCE`, `FAILED`; repeatable), `machine_id`, `unanswered=true` (shorthand for `INSUFFICIENT_EVIDENCE` + `FAILED`), `page` (page size proposed 25).

```json
200 {
  "count": 2, "next": null, "previous": null,
  "results": [
    { "id": 43, "asked_by": "eng1", "machine_id": "M-HP200", "model_code": "HP-200",
      "question": "What is the pump replacement price?", "status": "INSUFFICIENT_EVIDENCE",
      "created_at": "<ISO>", "citation_count": 0 },
    { "id": 42, "asked_by": "eng1", "machine_id": "M-HP200", "model_code": "HP-200",
      "question": "What does E17 mean?", "status": "ANSWERED",
      "created_at": "<ISO>", "citation_count": 1 }
  ]
}
```
Detail (answer text + citations) via `GET /api/questions/{id}/`.

**Read-only guarantee:** no `PUT`/`PATCH`/`DELETE` routes exist for questions, answers or citations; any such request returns 405. Supervisors have no write endpoint at all.

## 6. Page routes (HTML, Django views — proposed)

| Path | Roles | Purpose |
|---|---|---|
| `/` | any | Redirect by role |
| `/assistant/` | ENGINEER | Machine selector, ask form, answer + citations |
| `/history/` | ENGINEER | Own history |
| `/log/` | SUPERVISOR, ADMIN | Question log with status/machine filters |
| `/manage/machines/` | ADMIN | Register/list machines |
| `/manage/manuals/` | ADMIN | Upload/list manuals and ingestion status |

Each page view checks the role server-side (redirect to login / 403), independent of the API checks.

## 7. Summary matrix

| Endpoint | ADMIN | ENGINEER | SUPERVISOR |
|---|:-:|:-:|:-:|
| `GET /api/auth/me/` | ✅ | ✅ | ✅ |
| `GET /api/machines/` | ✅ | ✅ | ✅ |
| `POST /api/machines/` | ✅ | 403 | 403 |
| `GET /api/manuals/` | ✅ | ✅ | ✅ |
| `POST /api/manuals/` | ✅ | **403** | 403 |
| `GET /api/manuals/{id}/file/` | ✅ | ✅ | ✅ |
| `POST /api/questions/` | open question | ✅ | 403 |
| `GET /api/questions/` | all | own | all |
| `GET /api/questions/{id}/` | ✅ | own only | ✅ |
