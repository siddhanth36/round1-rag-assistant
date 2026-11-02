"""Score the frozen model on a labeled sample set: confusion matrix and defect recall.

Labels are read only for scoring; the model only ever receives an image path.
Usage: python -m inspection_ai.evaluation <samples_dir> <labels_csv>
"""

import argparse
import csv
import sys
from pathlib import Path

from inspection_ai import CLASS_NAMES, InferenceError, predict_image

REQUIRED_COLUMNS = {"image_id", "relative_path", "label"}


def evaluate(samples_dir, labels_csv) -> dict:
    """Predict every CSV row's image and score it. Defective is the positive class.

    ``matrix`` rows are actual labels, columns predicted labels, both in ``CLASS_NAMES`` order.
    Rows that cannot be scored (bad label, missing path or file, inference error) are listed in
    ``failed`` and still counted in ``total``. ``recall`` is None when no Defective row was scored.
    Raises ValueError if the CSV lacks the required columns.
    """
    with open(labels_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{labels_csv}: missing CSV columns {sorted(missing)}")
        rows = list(reader)

    matrix = [[0, 0], [0, 0]]
    failed = []
    for line, row in enumerate(rows, start=2):  # line 1 is the header
        label = (row["label"] or "").strip()
        relative_path = (row["relative_path"] or "").strip()
        path = Path(samples_dir) / relative_path

        def fail(reason):
            failed.append({"line": line, "image_id": row["image_id"], "relative_path": relative_path,
                           "label": label, "reason": reason})

        if label not in CLASS_NAMES:
            fail(f"invalid label {label!r}")
        elif not relative_path:
            fail("missing relative_path")
        elif not path.is_file():
            fail(f"image file not found: {path}")
        else:
            try:
                predicted = predict_image(str(path))["predicted_class"]
            except InferenceError as exc:
                fail(str(exc))
                continue
            matrix[CLASS_NAMES.index(label)][CLASS_NAMES.index(predicted)] += 1

    (tn, fp), (fn, tp) = matrix
    return {"matrix": matrix, "labels": list(CLASS_NAMES), "recall": tp / (tp + fn) if tp + fn else None,
            "tp": tp, "fn": fn, "fp": fp, "tn": tn, "failed": failed, "total": len(rows)}


def format_report(result: dict) -> str:
    """Human-readable report of an ``evaluate()`` result."""
    (tn, fp), (fn, tp) = result["matrix"]
    recall = result["recall"]
    lines = [
        f"Total samples:          {result['total']}",
        f"Successful predictions: {result['total'] - len(result['failed'])}",
        f"Failed predictions:     {len(result['failed'])}",
        "",
        "Confusion matrix (rows = actual, columns = predicted)",
        f"{'':18}{'Normal':>8}{'Defective':>11}",
        f"{'Actual Normal':18}{tn:>8}{fp:>11}",
        f"{'Actual Defective':18}{fn:>8}{tp:>11}",
        "",
        f"TN={tn}  FP={fp}  FN={fn}  TP={tp}",
        f"Defective recall: {recall:.4f} ({tp}/{tp + fn})" if recall is not None
        else "Defective recall: undefined (no Defective sample was scored)",
    ]
    if result["failed"]:
        lines += ["", "Failed samples:"]
        lines += [f"  line {f['line']} {f['image_id']} {f['relative_path']}: {f['reason']}" for f in result["failed"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    """CLI entry point. Exit 0 = every row scored, 1 = some rows failed, 2 = CSV unreadable."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("samples_dir")
    parser.add_argument("labels_csv")
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.samples_dir, args.labels_csv)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"Cannot read dataset: {exc}", file=sys.stderr)
        return 2
    print(format_report(result))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
