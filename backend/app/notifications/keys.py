"""VAPID keys for browser push (RFC 8292).

Production: set VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY from the secret store (keep them stable:
changing them invalidates every device's subscription). Locally, a key pair is generated once
and kept in DATA_DIR/vapid.json so subscriptions survive restarts.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid


def _public_b64(vapid: Vapid) -> str:
    raw = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def load(settings) -> tuple[Vapid, str]:
    """(private key for signing, public key for the browser)."""
    if settings.vapid_private_key:
        vapid = Vapid.from_pem(settings.vapid_private_key.encode())
        return vapid, settings.vapid_public_key or _public_b64(vapid)
    path = Path(settings.data_dir) / "vapid.json"
    if path.exists():
        stored = json.loads(path.read_text())
        vapid = Vapid.from_pem(stored["private_pem"].encode())
        return vapid, stored["public_key"]
    vapid = Vapid()
    vapid.generate_keys()
    pem = vapid.private_pem().decode()
    public = _public_b64(vapid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"private_pem": pem, "public_key": public}))
    os.chmod(path, 0o600)
    return vapid, public
