"""Smoke-test helper: read an .xlsx from stdin, print facts about it as JSON.

Usage: python -m app.inspect_xlsx < workbook.xlsx

Output: {"visible_sheets": int}
"""

from __future__ import annotations

import json
import sys

from .xlsx import visible_sheet_count


def main() -> int:
    data = sys.stdin.buffer.read()
    json.dump({"visible_sheets": visible_sheet_count(data)}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
