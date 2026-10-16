"""Signed, expiring access tokens for WOPI requests from the editor.

A token is ``base64url(json payload) "." base64url(HMAC-SHA256(payload))``. It
names one edit session, the user and an expiry, so it grants access to exactly
one file for a limited time. Only the backend can mint or verify tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class EditGrant:
    session_id: str
    document_id: int
    user: str
    expires_at: int  # unix seconds


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())


def issue(secret: str, grant: EditGrant) -> str:
    payload = _b64(json.dumps(asdict(grant), separators=(",", ":")).encode())
    return f"{payload}.{_sign(secret, payload)}"


def verify(secret: str, token: str, *, now: float | None = None) -> EditGrant:
    try:
        payload, signature = token.split(".", 1)
    except ValueError as exc:
        raise InvalidToken("malformed token") from exc
    if not hmac.compare_digest(signature, _sign(secret, payload)):
        raise InvalidToken("bad signature")
    try:
        grant = EditGrant(**json.loads(_unb64(payload)))
    except (ValueError, TypeError) as exc:
        raise InvalidToken("bad payload") from exc
    if grant.expires_at < (time.time() if now is None else now):
        raise InvalidToken("expired")
    return grant
