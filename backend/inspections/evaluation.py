"""Model evaluation over the labeled samples, through the same validation and model path as an upload.

``inspection_ai.evaluation.evaluate`` calls the model directly and skips the backend's upload validation,
so it can't be used here as is (contract: "through the same validation and model path as uploads").
This module keeps its CSV columns, class order and matrix layout, and reuses its ``format_report``.
Labels are read only for scoring: the model only ever receives the image path.
"""

import csv
import json
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files import File

from backend.inspections import ai_adapter
from backend.inspections.validation import ImageRejected, validate_image
from inspection_ai import CLASS_NAMES, MODEL_VERSION
from inspection_ai.evaluation import REQUIRED_COLUMNS

LIMITATIONS = [  # verbatim from docs/ROUND2_API_CONTRACT.md §3.7
    "No false negatives on these 60 synthetic samples; a sample this small cannot establish real-world reliability.",
    "Fixed framing only: rotated or displaced products, other backgrounds and unfamiliar defects are out of scope.",
    "Scores are uncalibrated model outputs, not verified probabilities of product safety.",
]


class MetricsUnavailable(Exception):
    """No metrics report has been generated yet."""


def evaluate_samples(samples_dir: Path, labels_csv: Path) -> dict:
    """Score every CSV row. Rows without a prediction are listed in ``failed`` and still count in ``total``.

    ``matrix`` rows are actual labels, columns predicted labels, in ``CLASS_NAMES`` order (Normal, Defective);
    Defective is the positive class. ``recall`` is None when no Defective sample was scored.
    Raises ValueError if the CSV lacks the required columns, OSError if it can't be read.
    """
    with open(labels_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{labels_csv}: missing CSV columns {sorted(missing)}")
        rows = list(reader)

    matrix = [[0, 0], [0, 0]]
    failed, versions = [], set()
    for line, row in enumerate(rows, start=2):  # line 1 is the header
        label = (row["label"] or "").strip()
        relative_path = (row["relative_path"] or "").strip()
        reason, predicted = None, None
        if label not in CLASS_NAMES:
            reason = f"invalid label {label!r}"
        elif not relative_path or not (Path(samples_dir) / relative_path).is_file():
            reason = "file missing"
        else:
            try:
                prediction = _predict_like_an_upload(Path(samples_dir) / relative_path)
            except (ImageRejected, ai_adapter.InferenceError) as exc:
                reason = str(exc)
            else:
                predicted = prediction["predicted_class"]
                versions.add(prediction["model_version"])
        if reason is not None:
            failed.append({"image_id": row["image_id"], "file": relative_path, "reason": reason, "line": line})
            continue
        matrix[CLASS_NAMES.index(label)][CLASS_NAMES.index(predicted)] += 1

    (tn, fp), (fn, tp) = matrix
    return {"labels": list(CLASS_NAMES), "matrix": matrix, "tn": tn, "fp": fp, "fn": fn, "tp": tp,
            "recall": tp / (tp + fn) if tp + fn else None, "total": len(rows), "failed": failed,
            "model_version": versions.pop() if len(versions) == 1 else MODEL_VERSION}


def _predict_like_an_upload(path: Path) -> dict:
    with path.open("rb") as f:
        validate_image(File(f, name=path.name))  # the same checks as POST /api/inspections/
    return ai_adapter.predict_image(str(path))


def save_metrics(result: dict) -> Path:
    """Write the report atomically (temp file + rename), so a reader never sees a half-written file."""
    target = Path(settings.INSPECTION_METRICS_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".metrics-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def load_metrics() -> dict:
    """The stored report in the API shape (contract §3.7). Raises MetricsUnavailable if none exists."""
    try:
        stored = json.loads(Path(settings.INSPECTION_METRICS_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MetricsUnavailable from None
    data = {key: stored[key] for key in ("labels", "matrix", "tn", "fp", "fn", "tp", "recall", "total")}
    data["failed"] = [{k: f[k] for k in ("image_id", "file", "reason")} for f in stored["failed"]]
    return data | {"model_version": stored["model_version"], "limitations": LIMITATIONS}
