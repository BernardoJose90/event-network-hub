"""Upload validation. Pure functions so they are easy to unit test."""

from __future__ import annotations

import io
import zipfile
from pathlib import PurePath

from openpyxl import load_workbook

ZIP_MAGIC = b"PK"


class UploadRejected(ValueError):
    """The uploaded file is not acceptable. ``str(exc)`` is safe to show to users."""


_EXTENSION_MESSAGES = {
    ".xlsm": "Macro-enabled workbooks (.xlsm) are not accepted. Save the file as .xlsx and try again.",
    ".xls": "Legacy Excel 97-2003 files (.xls) are not accepted. Save the file as .xlsx and try again.",
}


def validate_upload(filename: str | None, data: bytes, *, max_bytes: int) -> None:
    """Raise UploadRejected unless ``data`` looks like a genuine .xlsx workbook."""
    suffix = PurePath(filename or "").suffix.lower()
    if suffix in _EXTENSION_MESSAGES:
        raise UploadRejected(_EXTENSION_MESSAGES[suffix])
    if suffix != ".xlsx":
        raise UploadRejected("Only Excel .xlsx files are accepted.")

    if len(data) == 0:
        raise UploadRejected("The file is empty.")
    if len(data) > max_bytes:
        raise UploadRejected(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")

    if not data.startswith(ZIP_MAGIC):
        raise UploadRejected("The file is not a valid .xlsx workbook.")

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile as exc:
        raise UploadRejected("The file is not a valid .xlsx workbook.") from exc

    # A renamed .xlsm still carries its macro project.
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise UploadRejected(_EXTENSION_MESSAGES[".xlsm"])

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True)
        wb.close()
    except Exception as exc:  # openpyxl raises a variety of types for malformed input
        raise UploadRejected("The file could not be read as an Excel workbook.") from exc
