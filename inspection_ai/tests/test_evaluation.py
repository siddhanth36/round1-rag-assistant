"""evaluate(): unit tests with a mocked model, plus the real frozen model on the official 60 samples."""

import subprocess
import sys
import zipfile
from unittest import mock

import pytest

from inspection_ai import InferenceError, evaluation
from inspection_ai.evaluation import evaluate, main
from inspection_ai.tests.test_predictor import PACK_ZIP

HEADER = "image_id,relative_path,product_id,label\n"


def make_dataset(tmp_path, rows, header=HEADER, create_images=True):
    """rows: (image_id, relative_path, label). Images are empty files; the model is mocked."""
    (tmp_path / "images").mkdir()
    for _, rel, _ in rows:
        if create_images:
            (tmp_path / rel).touch()
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text(header + "".join(f"{i},{rel},P-PLATE01,{label}\n" for i, rel, label in rows))
    return tmp_path, csv_path


def fake_model(predictions):
    """predict_image stand-in keyed on file name (test-side only); 'error' raises InferenceError."""
    def predict(path):
        predicted = predictions[path.rsplit("/", 1)[-1]]
        if predicted == "error":
            raise InferenceError(f"Inference failed for {path!r}")
        return {"predicted_class": predicted, "confidence": 0.9, "defect_probability": 0.5,
                "model_version": "BIT-PLATE-CV-1.0"}
    return mock.patch.object(evaluation, "predict_image", side_effect=predict)


# actual label -> predicted: 2 TN, 1 FP, 1 FN, 3 TP
MIXED = [("a", "images/a.png", "Normal", "Normal"), ("b", "images/b.png", "Normal", "Normal"),
         ("c", "images/c.png", "Normal", "Defective"), ("d", "images/d.png", "Defective", "Normal"),
         ("e", "images/e.png", "Defective", "Defective"), ("f", "images/f.jpg", "Defective", "Defective"),
         ("g", "images/g.jpg", "Defective", "Defective")]


def test_confusion_matrix_counts_and_recall(tmp_path):
    samples, csv_path = make_dataset(tmp_path, [r[:3] for r in MIXED])
    with fake_model({r[1].split("/")[1]: r[3] for r in MIXED}):
        result = evaluate(samples, csv_path)
    assert result == {"matrix": [[2, 1], [1, 3]], "labels": ["Normal", "Defective"], "recall": 0.75,
                      "tp": 3, "fn": 1, "fp": 1, "tn": 2, "failed": [], "total": 7}


def test_model_receives_only_the_image_path(tmp_path):
    samples, csv_path = make_dataset(tmp_path, [r[:3] for r in MIXED])
    with fake_model({r[1].split("/")[1]: r[3] for r in MIXED}) as model:
        evaluate(samples, csv_path)
    assert [c.args for c in model.call_args_list] == [(str(samples / r[1]),) for r in MIXED]
    assert all(not c.kwargs for c in model.call_args_list)


def test_failed_prediction_is_recorded_and_others_continue(tmp_path):
    samples, csv_path = make_dataset(tmp_path, [("a", "images/a.png", "Normal"), ("b", "images/b.png", "Defective"),
                                                ("c", "images/c.png", "Defective")])
    with fake_model({"a.png": "Normal", "b.png": "error", "c.png": "Defective"}) as model:
        result = evaluate(samples, csv_path)
    assert model.call_count == 3
    assert (result["tn"], result["tp"], result["fn"], result["total"]) == (1, 1, 0, 3)
    assert [(f["image_id"], f["line"]) for f in result["failed"]] == [("b", 3)]
    assert "Inference failed" in result["failed"][0]["reason"]


def test_missing_image_file_is_a_failure_not_a_prediction(tmp_path):
    samples, csv_path = make_dataset(tmp_path, [("a", "images/a.png", "Normal")], create_images=False)
    with fake_model({}) as model:
        result = evaluate(samples, csv_path)
    model.assert_not_called()
    assert result["total"] == 1 and result["matrix"] == [[0, 0], [0, 0]]
    assert "image file not found" in result["failed"][0]["reason"]


@pytest.mark.parametrize("label", ["defective", "Scratched", ""])
def test_invalid_label_is_a_failure(tmp_path, label):
    samples, csv_path = make_dataset(tmp_path, [("a", "images/a.png", label), ("b", "images/b.png", "Normal")])
    with fake_model({"b.png": "Normal"}) as model:
        result = evaluate(samples, csv_path)
    model.assert_called_once()
    assert result["tn"] == 1 and result["total"] == 2
    assert result["failed"][0]["image_id"] == "a" and "invalid label" in result["failed"][0]["reason"]


def test_short_row_is_a_failure(tmp_path):
    samples, csv_path = make_dataset(tmp_path, [("b", "images/b.png", "Normal")])
    with csv_path.open("a") as f:
        f.write("R-short,images/x.png\n")  # no product_id / label
    with fake_model({"b.png": "Normal"}):
        result = evaluate(samples, csv_path)
    assert result["total"] == 2 and result["failed"][0]["image_id"] == "R-short"


@pytest.mark.parametrize("header", ["", "id,path,label\n"])
def test_malformed_csv_raises(tmp_path, header):
    samples, csv_path = make_dataset(tmp_path, [("a", "images/a.png", "Normal")], header=header)
    with pytest.raises(ValueError, match="missing CSV columns"):
        evaluate(samples, csv_path)


def test_no_positive_samples_gives_undefined_recall(tmp_path):
    samples, csv_path = make_dataset(tmp_path, [("a", "images/a.png", "Normal")])
    with fake_model({"a.png": "Defective"}):
        result = evaluate(samples, csv_path)
    assert result["recall"] is None and result["fp"] == 1
    assert "undefined" in evaluation.format_report(result)


def test_cli_success_report_and_exit_0(tmp_path, capsys):
    samples, csv_path = make_dataset(tmp_path, [r[:3] for r in MIXED])
    with fake_model({r[1].split("/")[1]: r[3] for r in MIXED}):
        assert main([str(samples), str(csv_path)]) == 0
    out = capsys.readouterr().out
    for text in ("Total samples:          7", "Successful predictions: 7", "Failed predictions:     0",
                 "Actual Normal            2          1", "Actual Defective         1          3",
                 "TN=2  FP=1  FN=1  TP=3", "Defective recall: 0.7500 (3/4)"):
        assert text in out


def test_cli_failures_listed_and_exit_1(tmp_path, capsys):
    samples, csv_path = make_dataset(tmp_path, [("a", "images/a.png", "Normal"), ("b", "images/b.png", "Bad")])
    with fake_model({"a.png": "Normal"}):
        assert main([str(samples), str(csv_path)]) == 1
    out = capsys.readouterr().out
    assert "Failed predictions:     1" in out and "line 3 b images/b.png: invalid label 'Bad'" in out


def test_cli_unreadable_dataset_exit_2(tmp_path, capsys):
    assert main([str(tmp_path), str(tmp_path / "missing.csv")]) == 2
    assert "Cannot read dataset" in capsys.readouterr().err


# --- real frozen model on the official 60-image sample set ------------------------------------------------

@pytest.fixture(scope="module")
def official_samples(tmp_path_factory):
    pytest.importorskip("PIL")
    if not PACK_ZIP.is_file():
        pytest.skip(f"Round 2 pack not found at {PACK_ZIP} (set ROUND2_PACK_ZIP)")
    root = tmp_path_factory.mktemp("pack")
    with zipfile.ZipFile(PACK_ZIP) as pack:
        pack.extractall(root, [n for n in pack.namelist() if n.startswith("Samples/")])
    return root / "Samples"


def test_official_samples_measured_result(official_samples):
    result = evaluate(official_samples, official_samples / "labels.csv")
    assert result["total"] == 60 and result["failed"] == []
    # Measured with the frozen model (BIT-PLATE-CV-1.0), not assumed.
    assert result["matrix"] == [[30, 0], [0, 30]] and result["recall"] == 1.0


def test_official_samples_cli(official_samples):
    run = subprocess.run([sys.executable, "-m", "inspection_ai.evaluation", str(official_samples),
                          str(official_samples / "labels.csv")], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert "Total samples:          60" in run.stdout and "Defective recall: 1.0000 (30/30)" in run.stdout
