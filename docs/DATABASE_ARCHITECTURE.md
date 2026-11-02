# Database Architecture — INDUSTRIX AI

Status: **Design (pre-implementation)**. Relational store: PostgreSQL (preferred), MySQL-compatible. Vector store: ChromaDB (derived index).

## 1. Principles

- **PostgreSQL is the source of truth** for users, machines, manuals, ingestion runs, questions, answers and citations.
- **ChromaDB is derived**: it can be deleted and rebuilt from stored PDFs + `Manual` rows at any time.
- Surrogate `BIGINT` primary keys for joins; business identifiers (`M-HP200`, `HP-OPS-01` + `1.0`) are unique-constrained columns.
- Revisions are rows, not overwrites. Cited manuals are never deleted (`on_delete=PROTECT`).
- The question log is append-only from the application's perspective.
- Only portable ORM features; any PostgreSQL-only constraint is also enforced in the service layer.

## 2. Entity-relationship overview

```
User (role) 1───* Question *───1 Machine 1───* Manual 1───* IngestionJob
                     │1                         ▲1
                     │                          │
                     1                          │
                   Answer 1───* Citation *──────┘
User (ADMIN) 1───* Manual.uploaded_by
```

| Relationship | Cardinality | Notes |
|---|---|---|
| Machine → Manual | 1 : N | HP-200 has 2 manuals; AC-90 has 1 |
| Manual → IngestionJob | 1 : N | One per indexing attempt |
| User → Manual (uploaded_by) | 1 : N | Must be an ADMIN |
| User → Question | 1 : N | Asker (ENGINEER) |
| Machine → Question | 1 : N | Selected machine at ask time |
| Question → Answer | 1 : 0..1 | No Answer if status PENDING/FAILED |
| Answer → Citation | 1 : N | 0 citations allowed only for INSUFFICIENT_EVIDENCE |
| Manual → Citation | 1 : N | Pins the exact document + revision cited |

## 3. Entities

Types are Django field types with the PostgreSQL column type in parentheses.

### 3.1 User (`accounts_user`) — custom user extending `AbstractUser`

| Field | Type | Constraints / notes |
|---|---|---|
| id | BigAutoField (bigint) | PK |
| username, password, first_name, last_name, email, is_active, last_login, date_joined | Django built-ins | `username` unique |
| role | CharField(20) (varchar) | choices `ADMIN`, `ENGINEER`, `SUPERVISOR`; NOT NULL; indexed |

- Custom user model must exist **before the first migration** (`AUTH_USER_MODEL = "accounts.User"`).
- `role` drives application permissions. `is_staff`/`is_superuser` only govern Django's built-in `/admin/` site (proposed: used for setup only).
- Proposed alternative rejected: Django Groups — a single required role per user is simpler and easier to test as a column.

### 3.2 Machine (`machines_machine`)

| Field | Type | Constraints / notes |
|---|---|---|
| id | BigAutoField | PK |
| machine_id | CharField(32) | **unique**; e.g. `M-HP200`, `M-AC90` (CSV `machine_id`) |
| model_code | CharField(32) | **unique**; e.g. `HP-200`, `AC-90` (CSV `model`) |
| name | CharField(120) | e.g. `Hydraulic forming press` (CSV `machine_name`) |
| description | TextField | optional |
| is_active | BooleanField | default True; inactive machines cannot be selected for questions |
| created_at | DateTimeField | auto_now_add |

Validation: `machine_id` matches `^[A-Z0-9-]+$`; trimmed; immutable after creation (it is copied into Chroma metadata).

### 3.3 Manual (`machines_manual`)

| Field | Type | Constraints / notes |
|---|---|---|
| id | BigAutoField | PK |
| machine | FK → Machine | `PROTECT`; indexed |
| document_id | CharField(32) | e.g. `HP-OPS-01` |
| document_name | CharField(200) | e.g. `HP 200 Operations Manual` (PDF title) |
| revision | CharField(16) | e.g. `1.0`; stored as text (revisions are labels, not numbers) |
| original_filename | CharField(255) | as uploaded, e.g. `HP200_Operations_Rev1_0.pdf` |
| file | FileField (varchar path) | stored under `MEDIA_ROOT/manuals/...` |
| file_type | CharField(50) | `application/pdf` (only accepted type) |
| file_sha256 | CharField(64) | proposed: integrity + duplicate-upload detection |
| page_count | PositiveIntegerField | NULL until extracted |
| uploaded_by | FK → User | `PROTECT`; must have role ADMIN |
| uploaded_at | DateTimeField | auto_now_add |
| status | CharField(20) | `PENDING`, `PROCESSING`, `INDEXED`, `FAILED`; indexed |
| is_current | BooleanField | True for the revision used in retrieval |

Constraints:
- `UNIQUE (document_id, revision)` — a revision is uploaded once.
- A `document_id` belongs to one machine (service-layer check: new revisions must use the same machine as earlier revisions).
- At most one current revision per `document_id`: `UniqueConstraint(fields=["document_id"], condition=Q(is_current=True))` — partial index on PostgreSQL; **not supported on MySQL**, so the ingestion service also enforces it inside a transaction.

Why both `Manual.status` and `IngestionJob`: `status` is the current state used to gate retrieval and the UI; jobs are the history of attempts with error detail and counts. Not redundant — different questions ("is it usable now?" vs "what happened on each run?").

### 3.4 IngestionJob (`machines_ingestionjob`)

| Field | Type | Constraints / notes |
|---|---|---|
| id | BigAutoField | PK |
| manual | FK → Manual | `CASCADE`; indexed |
| status | CharField(20) | `RUNNING`, `SUCCEEDED`, `FAILED` |
| pages_processed | PositiveIntegerField | default 0 |
| chunks_indexed | PositiveIntegerField | default 0 |
| embedding_model | CharField(100) | e.g. `all-MiniLM-L6-v2` |
| error_message | TextField | blank unless FAILED; no secrets/stack traces with keys |
| started_at | DateTimeField | auto_now_add |
| completed_at | DateTimeField | NULL until finished |

### 3.5 Question (`assistant_question`)

| Field | Type | Constraints / notes |
|---|---|---|
| id | BigAutoField | PK |
| user | FK → User | `PROTECT`; indexed |
| machine | FK → Machine | `PROTECT`; indexed |
| text | TextField | NOT blank; max 1000 chars (validated) |
| status | CharField(24) | `PENDING`, `ANSWERED`, `INSUFFICIENT_EVIDENCE`, `FAILED`; indexed |
| error_message | TextField | blank unless FAILED |
| created_at | DateTimeField | auto_now_add; indexed |

### 3.6 Answer (`assistant_answer`)

| Field | Type | Constraints / notes |
|---|---|---|
| id | BigAutoField | PK |
| question | OneToOneField → Question | `CASCADE`; unique |
| text | TextField | generated answer, or the fixed insufficient-evidence message |
| status | CharField(24) | `ANSWERED` or `INSUFFICIENT_EVIDENCE` |
| llm_model | CharField(100) | blank if no LLM call (abstained at retrieval) |
| retrieved_count | PositiveSmallIntegerField | passages retrieved for this answer (audit) |
| created_at | DateTimeField | auto_now_add |

- "Did the system abstain?" = `status == INSUFFICIENT_EVIDENCE`. A separate boolean would duplicate this; expose `is_insufficient_evidence` as a model/serializer property instead.
- `Question.status` mirrors the final answer status so history filters don't need a join and FAILED/PENDING (no Answer row) are representable. Both are written in the same transaction.
- Rule: `status = ANSWERED` ⇒ at least one Citation (service-layer check).
- RAG → DB status mapping (RAG side implemented): `answered` → `ANSWERED`; `insufficient_evidence` →
  `INSUFFICIENT_EVIDENCE`; `provider_unavailable` / `generation_error` → no Answer row, `Question.status = FAILED`
  with `error_message` set from the RAG `error` summary (never containing secrets).

### 3.7 Citation (`assistant_citation`)

| Field | Type | Constraints / notes |
|---|---|---|
| id | BigAutoField | PK |
| answer | FK → Answer | `CASCADE`; indexed |
| manual | FK → Manual | `PROTECT` (pins document_id + revision + file) |
| pdf_page | PositiveIntegerField | one-based; `1 ≤ pdf_page ≤ manual.page_count` |
| passage | TextField | snapshot of the chunk text shown as supporting passage |
| chunk_id | CharField(128) | Chroma id; for audit/debug |
| source_tag | CharField(8) | proposed: `S1`… as used in the answer text, to link markers to citations |
| rank | PositiveSmallIntegerField | display order |

Constraints: `UNIQUE (answer, chunk_id)`. Validation: manual.machine == answer.question.machine (defensive isolation check before insert).

The passage is snapshotted so history stays readable and accurate even if the vector index is rebuilt or a newer revision becomes current.

## 4. Indexing strategy

| Table | Index | Serves |
|---|---|---|
| user | `role` | role-based listings |
| machine | unique `machine_id`, unique `model_code` | lookup by business key |
| manual | unique `(document_id, revision)`; `(machine_id, is_current, status)` | revision lookup; "current indexed manuals for machine" |
| ingestionjob | `(manual_id, started_at)` | latest job per manual |
| question | `(user_id, created_at DESC)` | engineer history |
| question | `(status, created_at DESC)` | supervisor "unanswered" view |
| question | `(machine_id, created_at DESC)` | filter by machine |
| citation | `answer_id`; `manual_id` | render answer; "which answers cite this manual" |

FK columns get indexes automatically from Django.

## 5. History and supervisor read-only access

- **Engineer history**: `Question.objects.filter(user=request.user).select_related("machine", "answer").prefetch_related("answer__citations__manual").order_by("-created_at")`.
- **Supervisor / Admin log**: same query without the user filter; optional filters `status`, `machine_id`, date range. "Unanswered queries" = `status IN (INSUFFICIENT_EVIDENCE, FAILED)` (see open question in IMPLEMENTATION_PLAN.md).
- **Read-only** is enforced by exposing only `GET` endpoints for history (no update/delete routes exist) plus role permission classes; supervisors have no write endpoint anywhere. Records are therefore immutable through the application.
- Citations reference `Manual` rows by FK with `PROTECT`, so a historical answer always opens the exact revision it cited.

## 6. ChromaDB design

### 6.1 Collection
- One persistent collection, `CHROMA_COLLECTION` (default `manual_chunks`), in `CHROMA_DIR` (default `<project root>/chroma_db/`, git-ignored), HNSW **cosine** space (`configuration={"hnsw": {"space": "cosine"}}`).
- Embeddings are computed by our code (Sentence Transformers `all-MiniLM-L6-v2`, 384-dim, normalized) and passed explicitly; the collection is opened with `embedding_function=None`, so Chroma's default embedding function is never used.
- Collection-level metadata records `embedding_model` and `embedding_dimension`; opening the collection with a different embedder is refused (rebuild the index instead of mixing vectors).

### 6.2 Chunk id (deterministic)
`{document_id}:{revision}:p{pdf_page}:c{chunk_index}` — e.g. `HP-OPS-01:1.0:p2:c1`. Re-indexing the same revision upserts the same ids (idempotent); ids the new chunking no longer produces are deleted.

### 6.3 Metadata (every chunk) — implemented

| Key | Type | Example | Purpose |
|---|---|---|---|
| chunk_id | str | `HP-MNT-01:1.0:p3:c0` | traceability (same as the record id) |
| chunk_index | int | `0` | order within the page |
| machine_id | str | `M-HP200` | **mandatory retrieval filter** |
| document_id | str | `HP-MNT-01` | citation |
| revision | str | `1.0` | citation; revision management |
| source_filename | str | `HP200_Maintenance_Rev1_0.pdf` | citation / traceability |
| pdf_page | int | `3` | citation; original one-based PDF page |
| is_current | bool | `true` | retrieval filter; excludes superseded revisions |

The record's `document` is the full chunk text, exactly as chunked (no header prefix is embedded).
Not stored per chunk (corrected from the earlier design): `document_name` — kept in the relational
`Manual` row and joined by `document_id` + `revision` when rendering citations; `embedding_model` —
recorded once at collection level (§6.1).

### 6.4 Machine filter inside retrieval

```python
collection.query(
    query_embeddings=[q_vec], n_results=k,
    where={"$and": [{"machine_id": machine_id}, {"is_current": True}]},
)
```
The filter restricts the candidate set inside Chroma's search, so chunks from other machines are never returned, never ranked, and never sent to the LLM. A post-retrieval assertion (`all(m["machine_id"] == machine_id …)`) is kept as defence in depth only.

### 6.5 Multiple HP-200 manuals in one answer
HP-OPS-01 and HP-MNT-01 chunks both carry `machine_id = M-HP200`, so one query returns the best chunks across both documents. Each cited chunk keeps its own `document_id`/`revision`/`pdf_page`, producing separate Citation rows per document.

### 6.6 New revision without mixing
1. Upload `HP-OPS-01` rev `1.1` → new `Manual` row (`is_current=False`) → chunks indexed with ids `HP-OPS-01:1.1:…` and `is_current=False` (invisible to queries).
2. On success, `set_current_revision` first sets `is_current=False` on the other revisions' chunks of that machine + document, then `True` on rev 1.1 (two `collection.update` calls; never two current revisions, briefly possibly none); DB flips `Manual.is_current` in a transaction.
3. On failure, rev 1.1 chunks are deleted (`where={"$and":[{"document_id":"HP-OPS-01"},{"revision":"1.1"}]}`); rev 1.0 remains current.
4. Old revision chunks may be retained (for audit) or deleted; old citations keep working because they store passage snapshots and FK to the old `Manual` row/file.
Because ids embed the revision and queries require `is_current=True`, two revisions of the same document can never be retrieved together.

## 7. Data lifecycle

| Data | Created | Updated | Deleted |
|---|---|---|---|
| User | Admin/setup | Admin | Deactivate (`is_active=False`), not delete |
| Machine | Admin registration | Admin (name/description/is_active) | Not deleted once referenced (PROTECT) |
| Manual + PDF file | Admin upload | status, page_count, is_current only | Never while cited; superseded revisions retained |
| IngestionJob | Each indexing attempt | Until completion | Retained |
| Chroma chunks | Successful ingestion | `is_current` flips on new revision | On failed ingestion; full rebuild via management command |
| Question/Answer/Citation | Ask flow | Status transition PENDING → final only | Never through the app |

## 8. Django model outline (illustrative, not final code)

```python
class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN"; ENGINEER = "ENGINEER"; SUPERVISOR = "SUPERVISOR"
    role = models.CharField(max_length=20, choices=Role.choices, db_index=True)

class Machine(models.Model):
    machine_id = models.CharField(max_length=32, unique=True)
    model_code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Manual(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"; PROCESSING = "PROCESSING"; INDEXED = "INDEXED"; FAILED = "FAILED"
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="manuals")
    document_id = models.CharField(max_length=32)
    document_name = models.CharField(max_length=200)
    revision = models.CharField(max_length=16)
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=manual_upload_path)
    file_type = models.CharField(max_length=50, default="application/pdf")
    file_sha256 = models.CharField(max_length=64)
    page_count = models.PositiveIntegerField(null=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    is_current = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document_id", "revision"], name="uniq_manual_doc_rev"),
            models.UniqueConstraint(fields=["document_id"], condition=models.Q(is_current=True),
                                    name="uniq_current_revision"),  # PostgreSQL; service also enforces
        ]
        indexes = [models.Index(fields=["machine", "is_current", "status"])]

class IngestionJob(models.Model):
    manual = models.ForeignKey(Manual, on_delete=models.CASCADE, related_name="jobs")
    status = models.CharField(max_length=20)            # RUNNING / SUCCEEDED / FAILED
    pages_processed = models.PositiveIntegerField(default=0)
    chunks_indexed = models.PositiveIntegerField(default=0)
    embedding_model = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

class Question(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"; ANSWERED = "ANSWERED"
        INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"; FAILED = "FAILED"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="questions")
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="questions")
    text = models.TextField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"]),
                   models.Index(fields=["status", "-created_at"]),
                   models.Index(fields=["machine", "-created_at"])]

class Answer(models.Model):
    question = models.OneToOneField(Question, on_delete=models.CASCADE, related_name="answer")
    text = models.TextField()
    status = models.CharField(max_length=24)            # ANSWERED / INSUFFICIENT_EVIDENCE
    llm_model = models.CharField(max_length=100, blank=True)
    retrieved_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

class Citation(models.Model):
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name="citations")
    manual = models.ForeignKey(Manual, on_delete=models.PROTECT, related_name="citations")
    pdf_page = models.PositiveIntegerField()
    passage = models.TextField()
    chunk_id = models.CharField(max_length=128)
    rank = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["answer", "chunk_id"], name="uniq_citation_chunk")]
        ordering = ["rank"]
```

## 9. Seed data

The only seed data is what the challenge supplies (`challenge-pack/machine_manual_index.csv`):

| machine_id | model_code | name |
|---|---|---|
| M-HP200 | HP-200 | Hydraulic forming press |
| M-AC90 | AC-90 | Air compressor |

| document_id | revision | machine | original_filename | document_name (from PDF title) | pages |
|---|---|---|---|---|---|
| HP-OPS-01 | 1.0 | M-HP200 | HP200_Operations_Rev1_0.pdf | HP 200 Operations Manual | 3 |
| HP-MNT-01 | 1.0 | M-HP200 | HP200_Maintenance_Rev1_0.pdf | HP 200 Maintenance Manual | 3 |
| AC-OM-01 | 1.0 | M-AC90 | AC90_Operations_Maintenance_Rev1_0.pdf | AC 90 Operations and Maintenance Manual | 3 |

Per the brief's workflow, manuals are uploaded by an Admin through the application. A seeding command (proposed) may register machines from the CSV and upload manuals through the same service, for repeatable demos. No fabricated questions/answers are seeded.
