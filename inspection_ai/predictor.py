"""Public prediction entry point: a thin wrapper around the frozen model's own ``predict()``."""

from inspection_ai.model.inference import predict

RESULT_KEYS = ("predicted_class", "confidence", "defect_probability", "model_version")


class InferenceError(Exception):
    """Raised when model inference fails."""


def predict_image(path: str) -> dict:
    """Run the frozen classifier on an already-validated image file.

    Returns the model's own ``predicted_class``, ``confidence``, ``defect_probability`` and
    ``model_version``, unchanged. Raises ``InferenceError`` (original exception as ``__cause__``)
    on any failure; never returns a default prediction.
    """
    try:
        result = predict(path)
        return {key: result[key] for key in RESULT_KEYS}
    except Exception as exc:
        raise InferenceError(f"Inference failed for {path!r}: {exc}") from exc
