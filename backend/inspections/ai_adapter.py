"""The ONLY module that touches the inspection model: the frozen ``inspection_ai`` package, called unchanged.

Its preprocessing, weights and 0.5 threshold are never copied or re-implemented here. The model sees only the
saved image path: no filename, folder or CSV information is ever used to decide a label.
"""

import logging
import math

import inspection_ai

logger = logging.getLogger(__name__)

CLASSES = ("Normal", "Defective")
KEYS = ("predicted_class", "confidence", "defect_probability", "model_version")


class InferenceError(Exception):
    """The model failed or returned an invalid result. ``str(exc)`` is safe to show to users."""


def predict_image(path: str) -> dict:
    """Run the frozen classifier on a validated, stored image and return exactly ``KEYS``."""
    try:
        result = inspection_ai.predict_image(str(path))
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
