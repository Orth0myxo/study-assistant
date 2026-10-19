from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable

from app.config import Settings


class OcrError(Exception):
    pass


class OcrUnavailable(OcrError):
    pass


class OcrTimeout(OcrError):
    pass


class OcrFailed(OcrError):
    pass


OcrRunner = Callable[[bytes, str, Settings], str]


def run_tesseract(image_bytes: bytes, suffix: str, settings: Settings) -> str:
    executable = shutil.which(settings.ocr_tesseract_path)
    if not executable:
        raise OcrUnavailable("Tesseract executable not found.")

    fd, path = tempfile.mkstemp(suffix=suffix or ".png")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(image_bytes)

        try:
            completed = subprocess.run(
                [
                    executable,
                    path,
                    "stdout",
                    "-l",
                    settings.ocr_language,
                    "--psm",
                    "3",
                ],
                capture_output=True,
                timeout=settings.ocr_timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrTimeout("Tesseract timed out.") from exc
        except FileNotFoundError as exc:
            raise OcrUnavailable("Tesseract executable not found.") from exc
        except OSError as exc:
            raise OcrFailed("Tesseract could not be started.") from exc

        if completed.returncode != 0:
            raise OcrFailed(f"Tesseract exited with code {completed.returncode}.")

        return completed.stdout.decode("utf-8", errors="replace")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
