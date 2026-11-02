"""The ONLY module that touches the inspection model.

Source, in order: the teammate's ``inspection_ai`` package once feature/round2-ai is merged; until then the
supplied ``inference.py`` in settings.INSPECTION_MODEL_DIR, loaded by path and called unchanged. Its
preprocessing, weights and 0.5 threshold are never copied or re-implemented here. The model sees only the
saved image path: no filename, folder or CSV information is ever used to decide a label.
"""

import importlib.util
import logging
import math
from collections.abc import Callable
from functools import cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

CLASSES = ("Normal", "Defective")
KEYS = ("predicted_class", "confidence", "defect_probability", "model_version")


class InferenceError(Exception):
    """The model failed or returned an invalid result. ``str(exc)`` is safe to show to users."""


def predict_image(path: str) -> dict:
    """Run the frozen classifier on a validated, stored image and return exactly ``KEYS``."""
    try:
        result = _model_predict()(str(path))
    except Exception as exc:
        logger.exception("Model inference failed for %s", path)
        raise InferenceError("The model could not process this image.") from exc
    return _checked(result)


def _checked(result) -> dict:
    try:
        output = {key: result[key] for key in KEYS}
        output["confidence"] = float(output["confidence"])
        output["defect_probability"] = float(output["defect_probability"])
    except (KeyError, TypeError, ValueError):
        logger.error("Model returned an unexpected result: %r", result)
        raise InferenceError("The model returned an invalid result.") from None
    valid = (output["predicted_class"] in CLASSES
             and all(math.isfinite(output[k]) and 0 <= output[k] <= 1 for k in ("confidence", "defect_probability"))
             and isinstance(output["model_version"], str) and output["model_version"].strip())
    if not valid:
        logger.error("Model returned an out-of-range result: %r", result)
        raise InferenceError("The model returned an invalid result.")
    return output


@cache
def _model_predict() -> Callable[[str], dict]:
    try:
        from inspection_ai import predict_image as teammate_predict  # feature/round2-ai
    except ModuleNotFoundError as exc:
        if exc.name != "inspection_ai":
            raise  # the package exists but is broken: fail loudly, don't fall back silently
    else:
        return teammate_predict

    # ponytail: replace with inspection_ai once merged (delete this fallback and INSPECTION_MODEL_DIR).
    path = Path(settings.INSPECTION_MODEL_DIR) / "inference.py"
    spec = importlib.util.spec_from_file_location("bit_plate_cv_inference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # FileNotFoundError if the pack is missing -> InferenceError above
    return module.predict
