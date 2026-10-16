"""Smoke-test helper: read an .xlsx from stdin, print facts about it as JSON.

Usage: python -m app.inspect_xlsx < workbook.xlsx > facts.json

Output: {"visible_sheets": int, "sensitive_values": [str, ...]}

The output contains the sensitive (password column) values, so callers must
redirect it to a private file and never echo it.
"""

from __future__ import annotations

import json
import sys

from .xlsx import sensitive_values, visible_sheet_count


def main() -> int:
    data = sys.stdin.buffer.read()
    json.dump(
        {"visible_sheets": visible_sheet_count(data), "sensitive_values": sensitive_values(data)},
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
