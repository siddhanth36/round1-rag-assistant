"""Upload validation for inspection images. Runs before any file is stored or any model call is made."""

import hashlib
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MAX_BYTES = 5 * 1024 * 1024  # 5 MiB per file (brief)
MIN_SIDE, MAX_SIDE = 64, 4096  # each dimension, inclusive (brief)
EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png"}  # the only accepted decoded formats
SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")  # PNG / JPEG magic bytes, only to word the error


class ImageRejected(Exception):
    """The upload is not an acceptable image. ``str(exc)`` is a readable reason for the user."""


@dataclass(frozen=True)
class ValidatedImage:
    format: str  # "JPEG" or "PNG", from the decoded content
    width: int
    height: int
    size: int
    sha256: str

    @property
    def extension(self) -> str:
        return EXTENSIONS[self.format]


def validate_image(upload) -> ValidatedImage:
    """Validate an uploaded file by size, decoded format, dimensions and full decode.

    The client's filename, extension and Content-Type are ignored. Dimensions are read from the header
    and checked before the pixel data is decoded, which also stops decompression bombs.
    """
    size = upload.size
    if not size:
        raise ImageRejected("The file is empty.")
    if size > MAX_BYTES:  # checked before Pillow reads anything
        raise ImageRejected(f"The file is {size:,} bytes; the limit is 5 MiB ({MAX_BYTES:,} bytes).")

    upload.seek(0)
    head = upload.read(8)
    try:
        upload.seek(0)
        with Image.open(upload, formats=list(EXTENSIONS)) as img:
            fmt, (width, height) = img.format, img.size
            if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
                raise ImageRejected(f"The image is {width}×{height} px; each side must be "
                                    f"{MIN_SIDE}–{MAX_SIDE} px.")
            img.verify()  # structure and checksums
        upload.seek(0)
        with Image.open(upload, formats=list(EXTENSIONS)) as img:
            img.load()  # full decode: catches truncated pixel data that verify() can miss
    except ImageRejected:
        raise
    except Image.DecompressionBombError:
        raise ImageRejected("The image has too many pixels; each side must be "
                            f"{MIN_SIDE}–{MAX_SIDE} px.") from None
    except UnidentifiedImageError:
        if head.startswith(SIGNATURES):
            raise ImageRejected("The image data is corrupt or truncated.") from None
        raise ImageRejected("The file content is not a JPEG or PNG image.") from None
    except Exception:  # Pillow raises OSError/SyntaxError/ValueError/... for damaged data
        raise ImageRejected("The image data is corrupt or truncated.") from None

    upload.seek(0)
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return ValidatedImage(format=fmt, width=width, height=height, size=size, sha256=digest.hexdigest())
