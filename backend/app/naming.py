"""Upload file-name rules.

Accepted files are named ``<EVENT> <sep> Event_Network_Requirements <sep> V<n>.xlsx``.
Spaces, underscores and hyphens are interchangeable (``|`` also works after the
event code) and case is ignored, so these all name V1 of the VAN26 document:

    VAN26 _ Event_Network_Requirements_V1.xlsx
    VAN26 _ Event Network Requirements - V1.xlsx
    VAN26 | Event-Network-Requirements V1.xlsx
    van26_event_network_requirements_v1.xlsx

The event code identifies the document; ``V<n>`` is the version number. Every
spelling maps to the same document name and version, so the same version can't
be uploaded twice under a differently written file name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

from .config import REQUIRED_NAME
from .validation import UploadRejected

EXAMPLE = f"LIS26 _ {REQUIRED_NAME}_V1.xlsx"

_SEP = r"[\s_-]+"
# "Event_Network_Requirements" -> Event[\s_-]+Network[\s_-]+Requirements
_REQUIRED = _SEP.join(re.escape(word) for word in REQUIRED_NAME.split("_"))
_DISPLAY_REQUIRED = REQUIRED_NAME.replace("_", " ")

# The name must not be glued to the event code ("LIS26Event..."); underscores
# count as word characters, so \b can't express this.
_NAME = re.compile(
    rf"^(?P<event>.*?)[\s|_-]*(?<![A-Za-z0-9]){_REQUIRED}(?P<rest>.*)$", re.IGNORECASE
)
_VERSION = re.compile(r"^[\s_-]*V\s*(?P<n>\d{1,4})$", re.IGNORECASE)
_EVENT = re.compile(r"^[A-Za-z0-9]{2,20}$")


@dataclass(frozen=True)
class ParsedName:
    event: str
    version: int

    @property
    def document_name(self) -> str:
        return f"{self.event} | {REQUIRED_NAME}"


def parse_upload_name(filename: str | None) -> ParsedName:
    """Return the event code and version from an upload's file name, or raise UploadRejected."""
    stem = PurePath(filename or "").stem.strip()

    m = _NAME.match(stem)
    if m is None:
        raise UploadRejected(
            f"The file name must contain '{_DISPLAY_REQUIRED}' (words joined by spaces, _ or -), "
            f"for example '{EXAMPLE}'."
        )

    event = m.group("event").strip(" |_-")
    if not _EVENT.match(event):
        raise UploadRejected(
            f"The file name must start with an event code such as 'LIS26', for example '{EXAMPLE}'."
        )

    v = _VERSION.match(m.group("rest"))
    if v is None:
        raise UploadRejected(
            f"The file name must end with a version number such as 'V1', for example '{EXAMPLE}'."
        )
    version = int(v.group("n"))
    if version < 1:
        raise UploadRejected("The version number must be 1 or higher.")

    return ParsedName(event=event.upper(), version=version)
