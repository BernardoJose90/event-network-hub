"""Upload file-name rules.

Accepted files are named ``<EVENT> _ Event_Network_Requirements_V<n>.xlsx``, for
example ``LIS25 _ Event_Network_Requirements_V5.xlsx`` or
``LIS26 | Event_Network_Requirements_V1.xlsx``:

- the words of ``Event_Network_Requirements`` and the ``_V<n>`` suffix must be
  joined with underscores (no spaces or hyphens);
- the event code is separated from the name by ``_`` or ``|`` (spaces around
  the separator are allowed);
- case is ignored.

The event code identifies the document; ``V<n>`` is the version number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

from .config import REQUIRED_NAME
from .validation import UploadRejected

EXAMPLE = f"LIS26 _ {REQUIRED_NAME}_V1.xlsx"

_NAME = re.compile(rf"^(?P<event>.*?)\s*[_|]?\s*{re.escape(REQUIRED_NAME)}(?P<rest>.*)$", re.IGNORECASE)
_VERSION = re.compile(r"^_V(?P<n>\d{1,4})$", re.IGNORECASE)
_EVENT = re.compile(r"^[A-Za-z0-9]{2,20}$")

# The same words joined by spaces or hyphens, to explain why such a name is refused.
_LOOSE_NAME = re.compile(r"[\s_-]+".join(REQUIRED_NAME.split("_")), re.IGNORECASE)


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
        if _LOOSE_NAME.search(stem):
            raise UploadRejected(
                f"Use underscores in the file name: '{REQUIRED_NAME}_V1', not spaces or hyphens. "
                f"For example '{EXAMPLE}'."
            )
        raise UploadRejected(f"The file name must contain '{REQUIRED_NAME}', for example '{EXAMPLE}'.")

    event = m.group("event").strip()
    if not _EVENT.match(event):
        raise UploadRejected(
            f"The file name must start with an event code such as 'LIS26', for example '{EXAMPLE}'."
        )

    v = _VERSION.match(m.group("rest"))
    if v is None:
        raise UploadRejected(
            f"The file name must end with '_V' and a version number, such as '_V1' "
            f"(underscore, no spaces), for example '{EXAMPLE}'."
        )
    version = int(v.group("n"))
    if version < 1:
        raise UploadRejected("The version number must be 1 or higher.")

    return ParsedName(event=event.upper(), version=version)
