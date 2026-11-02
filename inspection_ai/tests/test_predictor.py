"""predict_image(): passes the frozen model's output through unchanged and wraps failures."""

import os
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from inspection_ai import InferenceError, predict_image
from inspection_ai import predictor
from inspection_ai.model import inference

# The official pack is not committed; the real-model tests read two sample images straight from its ZIP.
PACK_ZIP = Path(os.environ.get("ROUND2_PACK_ZIP", Path.home() / "Downloads" / "Round 2 Student Pack.zip"))


def fake_result(predicted_class, confidence, defect_probability):
    return {"predicted_class": predicted_class, "confidence": confidence,
            "defect_probability": defect_probability, "model_version": "BIT-PLATE-CV-1.0"}


@pytest.mark.parametrize("result", [fake_result("Normal", 0.9283117, 0.0716883),
                                    fake_result("Defective", 0.6123456789, 0.6123456789)])
def test_returns_model_output_unchanged(result):
    with mock.patch.object(predictor, "predict", return_value=dict(result)) as model:
        assert predict_image("plate.png") == result
    model.assert_called_once_with("plate.png")


def test_returns_exactly_the_four_keys():
    with mock.patch.object(predictor, "predict", return_value={**fake_result("Normal", 0.9, 0.1), "extra": 1}):
        assert set(predict_image("plate.png")) == set(predictor.RESULT_KEYS)


@pytest.mark.parametrize("error", [OSError("cannot identify image file"), ValueError("bad array")])
def test_failure_raises_inference_error_with_original_cause(error):
    with mock.patch.object(predictor, "predict", side_effect=error):
        with pytest.raises(InferenceError) as raised:
            predict_image("plate.png")
    assert raised.value.__cause__ is error


def test_missing_result_key_is_an_inference_error_not_a_default():
    with mock.patch.object(predictor, "predict", return_value={"predicted_class": "Normal"}):
        with pytest.raises(InferenceError) as raised:
            predict_image("plate.png")
    assert isinstance(raised.value.__cause__, KeyError)


def test_calls_the_supplied_model_predict():
    assert predictor.predict is inference.predict
    with mock.patch.object(predictor, "predict", wraps=inference.predict) as spy:
        with pytest.raises(InferenceError):
            predict_image("does-not-exist.png")
    spy.assert_called_once_with("does-not-exist.png")


# --- real frozen model (needs Pillow and the official pack ZIP) -------------------------------------------

@pytest.fixture
def sample(tmp_path):
    pytest.importorskip("PIL")
    if not PACK_ZIP.is_file():
        pytest.skip(f"Round 2 pack not found at {PACK_ZIP} (set ROUND2_PACK_ZIP)")

    def extract(name):
        with zipfile.ZipFile(PACK_ZIP) as pack:
            return pack.extract(f"Samples/images/{name}", tmp_path)
    return extract


@pytest.mark.parametrize("name, expected_class", [("inspection_001.png", "Defective"),
                                                   ("inspection_002.jpg", "Normal")])
def test_real_model_prediction(sample, name, expected_class):
    path = sample(name)
    result = predict_image(path)
    assert result == inference.predict(path)  # identical to calling the supplied model directly
    assert result["predicted_class"] == expected_class
    assert result["model_version"] == inference.MODEL_VERSION == "BIT-PLATE-CV-1.0"
    p = result["defect_probability"]
    assert result["confidence"] == (p if expected_class == "Defective" else 1 - p)
