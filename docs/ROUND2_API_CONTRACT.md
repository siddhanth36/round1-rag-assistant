# Round 2 API contract: INDUSTRIX AI visual inspection

The frontend (branch `feature/round2-frontend`) and the APIs (branch `feature/round2-product`) are built
in parallel against this document. If the code and this document disagree, that is a bug. Report it and
don't work around it. Any change to a request or response shape is agreed by both owners first and then
recorded here.

Source of truth for requirements: `Round2_Student_Challenge_Brief.pdf` and `Round2_Model_and_Data_Guide.pdf`.

| Status | Meaning |
|---|---|
| **Built** | Merged on `feature/round2-product` and covered by tests |
| **In progress** | Being built in R2-4. The shape below is final. |
| **Planned** | R2-5. The shape below is final; the endpoint may return 404 until it lands. |

---

## 1. File ownership

| Owner | Files |
|---|---|
| **Frontend teammate** (`feature/round2-frontend`) | `frontend/templates/inspections/**`, `frontend/static/js/inspect*.js`, `frontend/static/css/inspections.css`, `backend/inspections/pages.py` (page routes only) |
| **API owner** (`feature/round2-product`) | `backend/inspections/` models, migrations, serializers, views (API), services, tasks, validation, ai_adapter, admin, all tests; `backend/config/settings.py`, `backend/config/urls.py`, `backend/config/celery.py`; `requirements.txt` |
| **Shared, additions only, by the frontend teammate** | `frontend/templates/base.html` + `frontend/templates/_nav.html` (nav links), `frontend/static/css/tokens.css`, `frontend/static/js/api.js`, `frontend/static/js/ui.js`. Add new things; never change or remove existing behaviour that Round 1 pages use. |

**Paths:** templates and static files live under `frontend/`, not `backend/`. Django is configured with
`TEMPLATES["DIRS"] = frontend/templates` and `STATICFILES_DIRS = frontend/static`. Files under
`backend/templates/` or `backend/static/` would not be found.

**Page pattern:** see the stub `/inspect/`.
- `backend/inspections/pages.py` has a `PAGES` list with one line per page: url, template, title, script.
- Each page view only renders `frontend/templates/inspections/<template>.html`, and is wrapped in `@login_required` and `@ensure_csrf_cookie`.
- The template extends `base.html`, which loads `frontend/static/js/<script>.js` as an ES module.
- The include in `backend/config/urls.py` already exists. Adding a page means adding a line to `PAGES` plus a template and a script.
- Page views never enforce roles, except the entries in `ADMIN_PAGES` (`/products/`, Product Management), which return
  403 to every role but `ADMIN`. Hiding UI by role is cosmetic; the APIs below enforce everything.
- `backend/inspections/test_pages.py` checks every entry in `PAGES` (login required, renders for every role);
  `backend/inspections/test_products.py` checks the Admin-only `/products/` page and its nav link.

**`api.js`:** today it sends JSON bodies only. For multipart uploads, add `FormData` support. Don't set
`Content-Type`, so the browser adds the boundary, and still send `X-CSRFToken`. Alternatively, reuse the
XHR upload pattern in `manage.js`, which also gives upload progress.

---

## 2. Conventions

- **Auth:** session cookie (log in through `POST /api/auth/login/`, unchanged from Round 1). Every unsafe request
  sends the `X-CSRFToken` header; `GET /api/auth/csrf/` sets the cookie.
- **Roles:** `ADMIN`, `ENGINEER`, `SUPERVISOR`, read from the session user only. The body never decides a role
  or a submitter.
- **Errors**, DRF style. Show the messages verbatim (`errorLines()` in `api.js` already formats them):
  - `401 {"detail": "Authentication credentials were not provided."}`: not logged in; `api.js` redirects to login.
  - `403 {"detail": "You do not have permission to perform this action."}`: logged in, wrong role.
  - `404 {"detail": "No Inspection matches the given query."}`: doesn't exist **or isn't visible to you**.
    Another Engineer's inspection is a 404, never a 403, so its existence isn't leaked.
  - `400 {"<field>": ["<message>"]}` or `{"detail": "<message>"}`: invalid input.
- **Timestamps:** ISO 8601 with time zone, UTC (`"2026-11-03T01:23:45.123456Z"`).
- **Scores:** floats in [0, 1], exactly as the model returned them. Display `confidence` as a percentage.
- **Visibility:** Engineers see only inspections and batches they submitted. Supervisors and Admins see all.

### Status values

| Field | Values | Meaning |
|---|---|---|
| `status` | `queued` | Valid image stored, waiting for a worker (batches only) |
| | `processing` | The model is running |
| | `completed` | Prediction saved. `original` is set and **never changes again**. |
| | `failed` | Invalid upload, model error or queue unavailable. `failure_reason` explains, and there is no prediction. |
| `review_status` | `unreviewed` / `confirmed` / `corrected` | Latest supervisor review; `unreviewed` if none |
| `predicted_class`, `reviewed_class` | `Normal` / `Defective` | |
| review `decision` | `confirmed` / `corrected` | |

### Inspection object (used by detail, single upload and the review response)

```json
{
  "id": 42,
  "product": "P-PLATE01",
  "batch": null,
  "original_filename": "inspection_001.png",
  "image_url": "/api/inspections/42/image/",
  "image_format": "PNG",
  "width": 256,
  "height": 256,
  "status": "completed",
  "failure_reason": "",
  "original": {
    "predicted_class": "Defective",
    "confidence": 1.0,
    "defect_probability": 1.0,
    "model_version": "BIT-PLATE-CV-1.0"
  },
  "review_status": "corrected",
  "current_review": {"id": 7, "reviewed_class": "Normal", "decision": "corrected", "comment": "Glare, not a scratch",
                     "reviewer": "supervisor", "created_at": "2026-11-03T02:10:00Z"},
  "reviews": [
    {"id": 7, "reviewed_class": "Normal", "decision": "corrected", "comment": "Glare, not a scratch",
     "reviewer": "supervisor", "created_at": "2026-11-03T02:10:00Z"}
  ],
  "submitted_by": "engineer",
  "created_at": "2026-11-03T01:23:45Z",
  "started_at": "2026-11-03T01:23:45Z",
  "completed_at": "2026-11-03T01:23:46Z"
}
```

- `original` is `null` unless `status` is `completed`.
- `image_url` is `null` for rejected uploads, because invalid files are never stored.
- `reviews` is newest first, and `current_review` is `reviews[0]` or `null`.
- `original` is the immutable model output. Always show it next to the review, never replaced by it.

---

## 3. Endpoints

### 3.1 Products: Built

**`GET /api/products/`**
- **Roles:** any logged-in user.
- **Returns:** active products only.

```json
200 [{"product_id": "P-PLATE01", "product_name": "Demonstration metal plate",
      "description": "Synthetic centered plate with scratches or pits", "is_active": true}]
```

**`POST /api/products/`**
- **Roles:** ADMIN only. Engineer and Supervisor get 403.
- **Request (JSON):** `{"product_id": "P-PLATE01", "product_name": "...", "description": "..."}`
- **Success:** `201`, returning the product object.
- **Errors:**
  - `400 {"product_id": ["product with this product id already exists."]}`
  - `400 {"product_id": ["Enter a valid value."]}`: must match `^[A-Z0-9-]+$`, at most 32 characters.

**`PATCH /api/products/<product_id>/`**
- **Roles:** ADMIN only (403 otherwise).
- **Request (JSON):** any of `product_name`, `description`, `is_active`. `product_id` can't be changed and is ignored.
- **Success:** `200`, returning the product object.
- **Errors:** `404` for an unknown product.
- Deactivating (`{"is_active": false}`) never deletes the product or its inspections; it only removes the product
  from `GET /api/products/`, so it can't be chosen for new inspections. `{"is_active": true}` makes it available again.

**`GET /api/products/manage/`**: Product Management listing.
- **Roles:** ADMIN only. Engineer and Supervisor get 403; anonymous gets 401. GET only (other methods: 405).
- **Returns:** every product, active **and** inactive, ordered by `product_id`. Same object as above plus the
  read-only `created_at`. `GET /api/products/` is unchanged (active only, no `created_at`).

```json
200 [{"product_id": "P-PLATE01", "product_name": "Demonstration metal plate",
      "description": "Synthetic centered plate with scratches or pits", "is_active": true,
      "created_at": "2026-11-03T01:23:45.123456Z"}]
```

The Admin page `/products/` (Product Management) uses this endpoint plus the POST and PATCH above.

Django admin (`/admin/`) also manages products. The brief allows this.

### 3.2 Single inspection: Built

**`POST /api/inspections/`**
- **Roles:** ENGINEER, ADMIN. Supervisor gets 403.
- **Request (multipart):** `product_id` (an active product), `image` (one file).
- **Processing:** runs the model **synchronously**.
- **Success:** `201`, returning the inspection object with `status: "completed"`.

**Errors**

| Status | Body | When |
|---|---|---|
| 400 | `{"id": 43, "status": "failed", "failure_reason": "<reason>"}` | The file failed validation. It was **saved as a failed attempt** (visible in history) and **never sent to the model**. |
| 400 | `{"product_id": ["Select an active product."]}` | Unknown or inactive product. Nothing saved. |
| 400 | `{"image": ["No image file was submitted."]}` | No file. Nothing saved. |
| 502 | inspection object with `status: "failed"`, `failure_reason: "The model could not process this image."` | Model error. Nothing is faked. |

**Validation rules and the exact `failure_reason` texts:**

| Rule | `failure_reason` |
|---|---|
| empty file | `The file is empty.` |
| more than 5 MiB (5,242,880 bytes) | `The file is 6,000,000 bytes; the limit is 5 MiB (5,242,880 bytes).` |
| content isn't JPEG or PNG (the extension is ignored) | `The file content is not a JPEG or PNG image.` |
| damaged or truncated JPEG/PNG | `The image data is corrupt or truncated.` |
| either side outside 64–4096 px | `The image is 32×32 px; each side must be 64–4096 px.` |
| absurd pixel count | `The image has too many pixels; each side must be 64–4096 px.` |

### 3.3 Inspection detail and image: Built

**`GET /api/inspections/<id>/`**
- **Roles:** the owner Engineer, any Supervisor, any Admin. Another Engineer gets 404.
- **Success:** `200`, returning the inspection object.

**`GET /api/inspections/<id>/image/`**
- **Roles:** the same as detail.
- **Success:** `200` with the image bytes; `Content-Type` is `image/png` or `image/jpeg`.
- **Errors:** `404` for failed attempts, which have no stored image.
- **Use:** put it straight into `<img src>`; the session cookie authenticates it. Images are never served from a public `/media/` URL.

### 3.4 Batch upload: Built

**`POST /api/inspections/batches/`**
- **Roles:** ENGINEER, ADMIN. Supervisor gets 403.
- **Request (multipart):** `product_id`, `images` (the field repeated once per file, 1–10 files).
- **Order of checks:**
  1. The file count is checked **first**. With 0 files or more than 10, nothing is saved:
     `400 {"images": ["A batch must contain 1 to 10 images; 11 received."]}`
  2. The product: `400 {"product_id": ["Select an active product."]}`, nothing saved.
- **Each file, in upload order:**
  - Invalid: becomes a `failed` item with a `failure_reason` from §3.2 and is never queued.
  - Valid: stored and `queued`. One Celery task per item runs after the database commit.
- **If Redis is down:** the request still succeeds. Items that couldn't be queued become `failed` with
  `"Background worker queue unavailable (Redis not reachable)."`
- **Success:** `202` with a `Location: /api/inspections/batches/<id>/` header. The body has the same shape as
  the batch detail below. Statuses are whatever they are at that moment, usually `queued`, plus `failed` for invalid files.

**`GET /api/inspections/batches/`**
- **Roles:** any logged-in user. Engineers get their own batches; Supervisors and Admins get all.
- **Order:** newest first; not paginated.

```json
200 [{"batch_id": 5, "product": "P-PLATE01", "submitted_by": "engineer", "created_at": "2026-11-03T01:30:00Z",
      "counts": {"queued": 0, "processing": 1, "completed": 3, "failed": 1}, "done": false}]
```

**`GET /api/inspections/batches/<id>/`**
- **Roles:** the owner Engineer, Supervisor, Admin. Another Engineer gets 404.
- **Use:** the tracker polls this every 1–2 s until `done` is true, then stops.

```json
200 {
  "batch_id": 5, "product": "P-PLATE01", "submitted_by": "engineer", "created_at": "2026-11-03T01:30:00Z",
  "counts": {"queued": 1, "processing": 1, "completed": 2, "failed": 1},
  "done": false,
  "worker_online": false,
  "warning": "No inspection worker is running: queued images will wait until one starts.",
  "items": [
    {"id": 50, "original_filename": "inspection_001.png", "status": "completed", "failure_reason": "",
     "original": {"predicted_class": "Defective", "confidence": 1.0, "defect_probability": 1.0,
                  "model_version": "BIT-PLATE-CV-1.0"},
     "image_url": "/api/inspections/50/image/", "started_at": "...", "completed_at": "..."},
    {"id": 51, "original_filename": "too_small.png", "status": "failed",
     "failure_reason": "The image is 32×32 px; each side must be 64–4096 px.",
     "original": null, "image_url": null, "started_at": null, "completed_at": "..."}
  ]
}
```

- `items` are in upload order. The prediction key is `original`, matching the detail endpoint.
- `done` is true when nothing is `queued` or `processing`.
- `worker_online`:
  - `true` or `false` from a cached (10 s) Celery ping with a 1 s timeout.
  - `null` once `done`, because no ping is needed then.
- `warning` is a readable string when `worker_online` is false and items are still queued; otherwise `null`. Show it prominently.

### 3.5 Inspection history: Built

**`GET /api/inspections/?prediction=&review_status=&status=&product=&from=&to=&page=`**
- **Roles:** any logged-in user. Engineers get their own inspections; Supervisors and Admins get all.
- **Filters** are all optional and combine with AND:

| Param | Values |
|---|---|
| `prediction` | `Normal` / `Defective` (the original model prediction) |
| `review_status` | `unreviewed` / `confirmed` / `corrected` |
| `status` | `queued` / `processing` / `completed` / `failed` |
| `product` | product id, e.g. `P-PLATE01` |
| `from`, `to` | `YYYY-MM-DD`, inclusive, on `created_at` (UTC) |
| `page` | 1-based; 25 per page (matches `pager()` in `ui.js`) |

- **Errors:** an invalid filter value returns 400 and is never silently ignored, e.g.
  `400 {"prediction": ["Select a valid choice. Scratched is not one of the available choices."]}` or
  `400 {"from": ["Enter a valid date."]}`.
- **Order:** newest first.

```json
200 {"count": 61, "next": "/api/inspections/?page=2", "previous": null,
     "results": [ <inspection object without "reviews"; with "current_review"> ]}
```

### 3.6 Supervisor review: Built

**`POST /api/inspections/<id>/review/`**
- **Roles:** **SUPERVISOR only.** Engineer and Admin get `403` (checked before the body is read). No review
  is created, and the original prediction never changes.
- **Request (JSON):** `{"decision": "confirmed" | "corrected", "reviewed_class": "Normal" | "Defective", "comment": "optional, ≤ 1000 chars"}`
- **Rules:**
  - `confirmed` requires `reviewed_class` to equal the original `predicted_class`.
  - `corrected` requires it to differ.
- **Effect:** each review **adds** a row. Reviews are append-only, so reviewing again keeps the history.
  `review_status` follows the newest review.
- **Success:** `201`, returning the **inspection object**: the unchanged `original`, the updated `review_status`, `current_review`, and the full `reviews` list.

**Errors**

| Status | Body |
|---|---|
| 403 | `{"detail": "You do not have permission to perform this action."}` |
| 404 | `{"detail": "No Inspection matches the given query."}` |
| 409 | `{"detail": "Only completed inspections can be reviewed."}` |
| 400 | `{"decision": ["\"approve\" is not a valid choice."]}` |
| 400 | `{"reviewed_class": ["A confirmation must keep the predicted class (Defective)."]}` |
| 400 | `{"reviewed_class": ["A correction must change the predicted class (Defective)."]}` |
| 400 | `{"comment": ["Ensure this field has no more than 1000 characters."]}` |

The demo includes "attempt a review as Engineer". The UI may show the review action to Engineers so the
server's 403 can be demonstrated. On a 403, show the message and reload the inspection to prove nothing changed.

### 3.7 Model metrics: Built

**`GET /api/inspections/metrics/`**
- **Roles:** any logged-in user (read-only).
- **Source:** computed by `python manage.py evaluate_samples` over all 60 labeled samples, through the same
  validation and model path as uploads. Labels are used only for scoring.
- **Errors:** `404 {"detail": "Metrics have not been generated yet."}` until that command has run.

```json
200 {
  "labels": ["Normal", "Defective"],
  "matrix": [[30, 0], [0, 30]],
  "tn": 30, "fp": 0, "fn": 0, "tp": 30,
  "recall": 1.0,
  "total": 60,
  "failed": [],
  "model_version": "BIT-PLATE-CV-1.0",
  "limitations": [
    "No false negatives on these 60 synthetic samples; a sample this small cannot establish real-world reliability.",
    "Fixed framing only: rotated or displaced products, other backgrounds and unfamiliar defects are out of scope.",
    "Scores are uncalibrated model outputs, not verified probabilities of product safety."
  ]
}
```

- `matrix` rows are the **actual** labels and columns the **predicted** labels, both in `labels` order
  (Normal, Defective). So `[[TN, FP], [FN, TP]]`, with Defective as the positive class.
- `recall` = TP / (TP + FN), or `null` if there are no Defective samples.
- `total` is the number of labeled samples (60).
- `failed` lists every sample with no prediction: `[{"image_id": "R2-017", "file": "images/inspection_017.png",
  "reason": "file missing" | "<validation or model reason>"}]`. Failed and missing samples are reported here,
  never silently dropped.
- The numbers above are the real values measured on the supplied model and samples.

---

## 4. Backend notes (additions; no request or response shape changed)

- **History:** an unknown query parameter is a 400 (`{"predction": ["Unknown filter."]}`), so a misspelt filter
  never silently returns everything. `from` after `to` is `400 {"to": ["The end date is before the start date."]}`.
  An unknown `product` is `400 {"product": ["Select a valid choice. That choice is not one of the available choices."]}`.
  `page` must be a whole number ≥ 1 (400 otherwise); a page past the end is DRF's `404 {"detail": "Invalid page."}`.
  `next`/`previous` are relative links; DRF orders their query parameters alphabetically (`?page=2&status=completed`).
- **Batch item failure reasons** besides §3.2 and the Redis message: `The stored image file is missing.` (the file
  disappeared before the worker ran) and `The inspection could not be processed.` (unexpected worker error).
  Model errors keep `The model could not process this image.`
- **Redis down:** queuing stops at the first broker failure and every item not yet sent is marked failed, so the
  request returns quickly (broker connect timeout 1 s).
- **Metrics storage:** `evaluate_samples` writes a JSON report file (`INSPECTION_METRICS_FILE`, default
  `media/metrics/inspection_metrics.json`), not database rows; the endpoint adds the three limitations above.
