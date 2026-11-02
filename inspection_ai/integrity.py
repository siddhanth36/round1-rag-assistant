"""SHA256 check of the frozen model files against the pack's official, unmodified SHA256SUMS.txt."""

import hashlib
import logging
import re
from pathlib import Path

MODEL_VERSION = "BIT-PLATE-CV-1.0"
CLASS_NAMES = ["Normal", "Defective"]

MODEL_DIR = Path(__file__).with_name("model")
REQUIRED_FILES = ("inference.py", "classifier.npz", "model_metadata.json", "requirements.txt")
# sha256sum format; the pack lists our model/ files as "Model/<name>". Anything else is not an entry.
_ENTRY = re.compile(r"([0-9a-fA-F]{64}) [ *]Model/(.+)")

logger = logging.getLogger(__name__)


def verify_model_integrity() -> bool:
    """True only if every required model file exists and matches its official SHA256 entry.

    Each problem (unreadable checksum file, missing entry, missing file, hash mismatch) is logged.
    """
    try:
        lines = (MODEL_DIR / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as exc:
        logger.error("Cannot read model checksums: %s", exc)
        return False
    expected = {m[2]: m[1].lower() for m in map(_ENTRY.fullmatch, map(str.strip, lines)) if m}

    ok = True
    for name in REQUIRED_FILES:
        if name not in expected:
            logger.error("No valid checksum entry for model file %s", name)
            ok = False
            continue
        try:
            with (MODEL_DIR / name).open("rb") as f:
                actual = hashlib.file_digest(f, "sha256").hexdigest()
        except OSError as exc:
            logger.error("Cannot read model file %s: %s", name, exc)
            ok = False
            continue
        if actual != expected[name]:
            logger.error("Checksum mismatch for model file %s", name)
            ok = False
    return ok
