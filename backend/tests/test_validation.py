from __future__ import annotations

import io
import zipfile

import pytest

from app.validation import UploadRejected, validate_upload

from .conftest import xlsx_bytes

MAX = 10 * 1024 * 1024


def test_accepts_valid_xlsx() -> None:
    validate_upload("report.xlsx", xlsx_bytes(), max_bytes=MAX)


def test_extension_is_case_insensitive() -> None:
    validate_upload("REPORT.XLSX", xlsx_bytes(), max_bytes=MAX)


def test_rejects_xlsm_with_clear_message() -> None:
    with pytest.raises(UploadRejected, match=r"Macro-enabled workbooks \(\.xlsm\)"):
        validate_upload("report.xlsm", xlsx_bytes(), max_bytes=MAX)


def test_rejects_xls_with_clear_message() -> None:
    with pytest.raises(UploadRejected, match=r"Legacy Excel 97-2003 files \(\.xls\)"):
        validate_upload("report.xls", b"\xd0\xcf\x11\xe0" + b"\0" * 100, max_bytes=MAX)


@pytest.mark.parametrize("name", ["report.csv", "report.pdf", "report", None, "report.xlsx.exe"])
def test_rejects_other_extensions(name: str | None) -> None:
    with pytest.raises(UploadRejected, match=r"Only Excel \.xlsx files"):
        validate_upload(name, xlsx_bytes(), max_bytes=MAX)


def test_rejects_oversized_file() -> None:
    with pytest.raises(UploadRejected, match="larger than the 10 MB limit"):
        validate_upload("big.xlsx", b"PK" + b"\0" * MAX, max_bytes=MAX)


def test_accepts_file_exactly_at_limit() -> None:
    data = xlsx_bytes()
    validate_upload("ok.xlsx", data, max_bytes=len(data))


def test_rejects_empty_file() -> None:
    with pytest.raises(UploadRejected, match="empty"):
        validate_upload("empty.xlsx", b"", max_bytes=MAX)


def test_rejects_missing_zip_magic() -> None:
    with pytest.raises(UploadRejected, match="not a valid .xlsx"):
        validate_upload("fake.xlsx", b"%PDF-1.7 not a spreadsheet", max_bytes=MAX)


def test_rejects_corrupt_zip_with_magic() -> None:
    with pytest.raises(UploadRejected, match="not a valid .xlsx"):
        validate_upload("broken.xlsx", b"PK\x03\x04garbage", max_bytes=MAX)


def test_rejects_zip_that_is_not_a_workbook() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "not excel")
    with pytest.raises(UploadRejected, match="could not be read"):
        validate_upload("other.xlsx", buf.getvalue(), max_bytes=MAX)


def test_rejects_renamed_macro_workbook() -> None:
    src = io.BytesIO(xlsx_bytes())
    out = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item))
        zout.writestr("xl/vbaProject.bin", b"\0macro")
    with pytest.raises(UploadRejected, match=r"\.xlsm"):
        validate_upload("sneaky.xlsx", out.getvalue(), max_bytes=MAX)
