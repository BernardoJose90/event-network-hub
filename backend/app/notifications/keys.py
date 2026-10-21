"""VAPID keys for browser push (RFC 8292).

Production: set VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY from the secret store (keep them stable:
changing them invalidates every device's subscription); they are required with STORAGE_BACKEND=s3,
where pods have no shared disk. Locally, a key pair is generated once and kept in
DATA_DIR/vapid.json (shared by the API and the worker) so subscriptions survive restarts.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
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
    if settings.storage_backend != "local":
        raise RuntimeError("Set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY: keys generated in a pod would differ "
                           "between pods and be lost on restart")
    path = Path(settings.data_dir) / "vapid.json"
    if not path.exists():
        vapid = Vapid()
        vapid.generate_keys()
        stored = {"private_pem": vapid.private_pem().decode(), "public_key": _public_b64(vapid)}
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".vapid-")  # created 0600
        try:
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(stored))
            os.link(tmp, path)  # atomic, and fails if the API or the worker got there first
        except FileExistsError:
            pass
        finally:
            os.unlink(tmp)
    stored = json.loads(path.read_text())
    return Vapid.from_pem(stored["private_pem"].encode()), stored["public_key"]
