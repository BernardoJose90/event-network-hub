"""Application settings and domain constants.

Runtime settings come from environment variables (see .env.example). Domain
constants that define behaviour (sensitive headers, formula errors) live here
so there is exactly one place to change them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Header texts (compared trimmed and case-insensitively) whose column values
# must be replaced in the redacted copy.
SENSITIVE_HEADERS: frozenset[str] = frozenset({"password value"})

# Only the first N rows of each sheet are searched for sensitive headers.
HEADER_SEARCH_ROWS = 5

REDACTED_PLACEHOLDER = "REDACTED"

# Spreadsheet error values surfaced to users as warnings.
FORMULA_ERRORS: frozenset[str] = frozenset({"#REF!", "#NAME?", "#DIV/0!", "#VALUE!", "#N/A"})


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    gotenberg_url: str
    gotenberg_timeout_seconds: float
    max_upload_bytes: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "portal.sqlite3"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        gotenberg_url=os.environ.get("GOTENBERG_URL", "http://gotenberg:3000").rstrip("/"),
        gotenberg_timeout_seconds=float(os.environ.get("GOTENBERG_TIMEOUT_SECONDS", "90")),
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
    )
