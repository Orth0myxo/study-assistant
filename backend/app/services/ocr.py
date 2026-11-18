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
    def __init__(
        self,
        message: str,
        *,
        return_code: int | None = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.stderr = stderr


OcrRunner = Callable[[bytes, str, Settings], str]


def _run_tesseract(
    image_bytes: bytes,
    suffix: str,
    settings: Settings,
    output_config: str = "",
    page_segmentation: str = "3",
) -> str:
    executable = shutil.which(settings.ocr_tesseract_path)
    if not executable:
        raise OcrUnavailable("Tesseract executable not found.")

    fd, path = tempfile.mkstemp(suffix=suffix or ".png")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(image_bytes)

        command = [
            executable,
            path,
            "stdout",
            "-l",
            settings.ocr_language,
            "--psm",
            page_segmentation,
        ]
        if output_config:
            command.append(output_config)
        try:
            completed = subprocess.run(
                command,
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
            stderr = completed.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            raise OcrFailed(
                f"Tesseract exited with code {completed.returncode}.",
                return_code=completed.returncode,
                stderr=str(stderr or ""),
            )

        return completed.stdout.decode("utf-8", errors="replace")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def run_tesseract(image_bytes: bytes, suffix: str, settings: Settings) -> str:
    return _run_tesseract(image_bytes, suffix, settings)


def run_tesseract_tsv(image_bytes: bytes, suffix: str, settings: Settings) -> str:
    """Return Tesseract's word boxes for table geometry, not just plain text."""
    return _run_tesseract(
        image_bytes, suffix, settings, output_config="tsv", page_segmentation="6"
    )
