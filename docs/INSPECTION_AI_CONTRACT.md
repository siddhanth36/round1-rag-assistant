# Inspection AI Integration Contract — `inspection_ai/` ↔ Django

Owner: AI integration (`feature/round2-ai`). Consumer: Django backend developer.
Status: **implemented and tested** (`pytest inspection_ai`). Signatures below were read from the code.
Model scope and limits: `docs/MODEL_LIMITATIONS.md`.

## 1. Public interface

Import only these names; everything else in `inspection_ai/` is internal.

```python
from inspection_ai import (
    predict_image,
    InferenceError,
    MODEL_VERSION,
    CLASS_NAMES,
    verify_model_integrity,
)
```

| Name | Definition | Notes |
|---|---|---|
| `predict_image(path: str) -> dict` | Runs the supplied frozen model (`inspection_ai/model/inference.py::predict`) on one image file | Returns the model's own output unchanged (§2). Raises `InferenceError` on any failure; never returns a default prediction |
| `InferenceError(Exception)` | Raised when inference fails | The original exception is kept as `__cause__` |
| `MODEL_VERSION` | `"BIT-PLATE-CV-1.0"` | Same value as the model's `model_version` output |
| `CLASS_NAMES` | `["Normal", "Defective"]` | Index 0 = Normal, 1 = Defective |
| `verify_model_integrity() -> bool` | Checks the 4 model files against the official `inspection_ai/model/SHA256SUMS.txt` | `True` only if every file exists and matches; otherwise `False` and each problem is logged (logger `inspection_ai.integrity`). Never raises |

Dependencies: NumPy (already installed by the root `requirements.txt`) and `Pillow==12.3.0` (the version the
model pins). Install Pillow with:

```bash
pip install -r inspection_ai/requirements.txt
```

Importing `inspection_ai` without Pillow fails with `ModuleNotFoundError`.

## 2. Prediction output

Exactly these four keys:

```python
{
    "predicted_class": "Normal",           # "Normal" or "Defective"
    "confidence": 0.87,                    # probability of the predicted class, 0.5–1.0
    "defect_probability": 0.13,            # model score for Defective, 0–1
    "model_version": "BIT-PLATE-CV-1.0",
}
```

**The values above are illustrative.** Real values come from the frozen model and must be stored as returned.

- `predicted_class` is `"Defective"` when `defect_probability >= 0.5`, else `"Normal"`.
- `confidence` is already computed by the model (`defect_probability` for Defective, `1 - defect_probability`
  for Normal). Do not recompute it.
- Scores are uncalibrated model outputs, not verified probabilities (see `docs/MODEL_LIMITATIONS.md`).

## 3. Integration rules

1. **The backend owns file validation.** `inspection_ai` performs none.
2. Before calling `predict_image()`, validate the saved file's actual content, not its extension or the client's
   MIME type. Per the Round 2 brief: decoded JPEG or PNG only, at most 5 MiB, each dimension 64–4096 px
   inclusive, batches of 1–10 files.
3. Only valid JPG/PNG image paths reach the model. Invalid files are stored as failed attempts **without a
   prediction** and are never passed to `predict_image()`. (The model would happily classify a valid 32×32 PNG,
   so the dimension check really must happen first.)
4. `predict_image()` takes a path to a file on disk. Save the upload first, then pass its path.
5. A model prediction is an **immutable historical result**. Store all four fields once and never update them.
6. Supervisor confirmations/corrections are stored **separately** (reviewed class, reviewer, review timestamp,
   optional comment).
7. Never overwrite the original prediction with a reviewed result. A retry must not overwrite a completed
   prediction or remove review history.
8. Store `model_version` with every completed inspection, taken from the prediction result.
9. Batch processing calls `predict_image()` independently for each valid image (one call per item).
10. Catch `InferenceError` per item, mark that item failed with a readable reason, and continue with the others.
11. Never pass labels, file names used as labels, `labels.csv` or any evaluation data to `predict_image()`. The
    model decides from image pixels only.
12. `inspection_ai` does not import Django and has no settings, database or network access. It can be imported
    from Django views, services or Celery tasks. `predict_image()` is a plain function with no shared mutable
    state; it loads the 1.4 KB weights file on each call.

**Integrity check:** call `verify_model_integrity()` once at startup (for example in an `AppConfig.ready()` or
a management command) and refuse to run inspections, or log loudly, if it returns `False`.

## 4. Examples

Single prediction (after the backend has validated and saved the file):

```python
from inspection_ai import predict_image

result = predict_image(saved_path)          # e.g. "/srv/media/inspections/3f2a….png"
inspection.predicted_class = result["predicted_class"]
inspection.confidence = result["confidence"]
inspection.defect_probability = result["defect_probability"]
inspection.model_version = result["model_version"]
inspection.status = "COMPLETED"
inspection.save()
```

Handling an inference failure (one batch item; other items are unaffected):

```python
import logging

from inspection_ai import InferenceError, predict_image

logger = logging.getLogger(__name__)

try:
    result = predict_image(saved_path)
except InferenceError as exc:
    logger.warning("Inference failed for inspection %s: %s", inspection.pk, exc.__cause__ or exc)
    inspection.status = "FAILED"
    inspection.failure_reason = "The model could not process this image."
    inspection.save()                       # no prediction fields are set
else:
    ...                                     # store the four fields as in the single-prediction example
```

Model names (`inspection`, `status`, `failure_reason`) are illustrative; the backend defines the schema.

## 5. Evaluation (not part of the request path)

`python -m inspection_ai.evaluation <samples_dir> <labels_csv>` prints the confusion matrix and defect recall
over a labelled set. Exit code: 0 = all rows scored, 1 = some rows failed, 2 = dataset unreadable. Measured
result on the 60 official samples: TN=30, FP=0, FN=0, TP=30, recall 1.0000 (see `docs/MODEL_LIMITATIONS.md`).
