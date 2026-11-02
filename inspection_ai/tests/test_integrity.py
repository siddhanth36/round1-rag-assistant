"""verify_model_integrity(): destructive cases run on a temporary copy of model/, never the real files."""

import hashlib
import shutil

import pytest

from inspection_ai import CLASS_NAMES, MODEL_VERSION, integrity, verify_model_integrity
from inspection_ai.model import inference

# sha256 of SHA256SUMS.txt exactly as shipped in "Round 2 Student Pack.zip".
OFFICIAL_SUMS_SHA256 = "5ff4c33a3861c2d3cc73b0e4566c267d83c945f20e3a1b1ed113554dc2cad838"


@pytest.fixture
def model_copy(tmp_path, monkeypatch):
    copy = tmp_path / "model"
    shutil.copytree(integrity.MODEL_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(integrity, "MODEL_DIR", copy)
    return copy


def test_supplied_model_files_pass():
    assert verify_model_integrity() is True


def test_untouched_copy_passes(model_copy):
    assert verify_model_integrity() is True


@pytest.mark.parametrize("name", integrity.REQUIRED_FILES)
def test_modified_file_fails(model_copy, name):
    with (model_copy / name).open("ab") as f:
        f.write(b"\0")
    assert verify_model_integrity() is False


@pytest.mark.parametrize("name", integrity.REQUIRED_FILES)
def test_missing_file_fails(model_copy, name):
    (model_copy / name).unlink()
    assert verify_model_integrity() is False


def test_missing_checksum_entry_fails(model_copy):
    sums = model_copy / "SHA256SUMS.txt"
    lines = sums.read_text().splitlines(keepends=True)
    sums.write_text("".join(line for line in lines if not line.endswith("Model/classifier.npz\n")))
    assert verify_model_integrity() is False


@pytest.mark.parametrize("content", [b"", b"not a checksum file\n", b"\xff\xfe\x00garbage",
                                     b"d05a524ac10d  Model/classifier.npz\n"])
def test_malformed_checksum_file_fails_safely(model_copy, content):
    (model_copy / "SHA256SUMS.txt").write_bytes(content)
    assert verify_model_integrity() is False


def test_missing_checksum_file_fails(model_copy):
    (model_copy / "SHA256SUMS.txt").unlink()
    assert verify_model_integrity() is False


def test_official_checksum_file_unchanged():
    data = (integrity.MODEL_DIR / "SHA256SUMS.txt").read_bytes()
    assert hashlib.sha256(data).hexdigest() == OFFICIAL_SUMS_SHA256


def test_constants_match_the_supplied_model():
    assert MODEL_VERSION == "BIT-PLATE-CV-1.0" == inference.MODEL_VERSION
    assert CLASS_NAMES == ["Normal", "Defective"] == list(inference.CLASS_NAMES)
