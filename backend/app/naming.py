"""Upload file-name rules.

Accepted files are named ``<EVENT> <sep> Event_Network_Requirements <sep> V<n>.xlsx``.
Spaces, underscores and hyphens are interchangeable (``|`` also works after the
event code) and case is ignored. "Event Network Matrix" (the older workbook
title) works in place of "Event Network Requirements", the version may be in
brackets, and a browser's repeat-download suffix such as " (1)" is ignored, so
these all name V1 of the VAN26 document:

    VAN26 _ Event_Network_Requirements_V1.xlsx
    VAN26 _ Event Network Requirements - V1.xlsx
    VAN26 | Event-Network-Requirements V1.xlsx
    van26_event_network_requirements_v1.xlsx
    VAN26 Event Network Matrix [V1].xlsx
    VAN26 _ Event Network Requirements - V1 (1).xlsx

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
_ACCEPTED_NAMES = (REQUIRED_NAME, "Event_Network_Matrix")
_REQUIRED = "(?:" + "|".join(_SEP.join(re.escape(w) for w in name.split("_")) for name in _ACCEPTED_NAMES) + ")"
_DISPLAY_REQUIRED = REQUIRED_NAME.replace("_", " ")

# The name must not be glued to the event code ("LIS26Event..."); underscores
# count as word characters, so \b can't express this.
_NAME = re.compile(
    rf"^(?P<event>.*?)[\s|_-]*(?<![A-Za-z0-9]){_REQUIRED}(?P<rest>.*)$", re.IGNORECASE
)
# "V4", "[V4]" or "(V4)", optionally followed by a repeat-download suffix like " (1)".
_VERSION = re.compile(
    r"^[\s_-]*(?P<open>[\[(]?)\s*V\s*(?P<n>\d{1,4})\s*(?P<close>[\])]?)(?:\s*\(\d{1,3}\))?$", re.IGNORECASE
)
_BRACKETS = {"": "", "[": "]", "(": ")"}
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
            f"The file name must contain '{_DISPLAY_REQUIRED}' or 'Event Network Matrix' (words joined by spaces, _ or -), "
            f"for example '{EXAMPLE}'."
        )

    event = m.group("event").strip(" |_-")
    if not _EVENT.match(event):
        raise UploadRejected(
            f"The file name must start with an event code such as 'LIS26', for example '{EXAMPLE}'."
        )

    v = _VERSION.match(m.group("rest"))
    if v is None or _BRACKETS[v.group("open")] != v.group("close"):
        raise UploadRejected(
            f"The file name must end with a version number such as 'V1', for example '{EXAMPLE}'."
        )
    version = int(v.group("n"))
    if version < 1:
        raise UploadRejected("The version number must be 1 or higher.")

    return ParsedName(event=event.upper(), version=version)


def event_document_name(code: str | None) -> str:
    """The document name for a new event code such as 'LIS26', or raise UploadRejected."""
    event = (code or "").strip()
    if not _EVENT.match(event):
        raise UploadRejected("The event code must be 2 to 20 letters or digits, for example 'LIS26'.")
    return ParsedName(event=event.upper(), version=1).document_name
