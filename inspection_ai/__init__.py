"""Inspection AI: integration of the frozen Round 2 classifier (model/ is supplied, never edited)."""

from inspection_ai.integrity import CLASS_NAMES, MODEL_VERSION, verify_model_integrity
from inspection_ai.predictor import InferenceError, predict_image

__all__ = ["CLASS_NAMES", "MODEL_VERSION", "InferenceError", "predict_image", "verify_model_integrity"]
