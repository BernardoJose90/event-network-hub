from __future__ import annotations

import logging

from app.main import RedactAccessTokens


def test_access_tokens_are_redacted_from_access_logs() -> None:
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("172.19.0.5:1", "POST", "/wopi/files/abc/contents?access_token=SECRET.SIG&x=1", "1.1", 200),
        None,
    )
    RedactAccessTokens().filter(record)
    line = record.getMessage()
    assert "SECRET" not in line
    assert "access_token=[REDACTED]&x=1" in line
