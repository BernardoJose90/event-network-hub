"""Collabora Online discovery: find the editor URL for .xlsx files.

Collabora publishes ``/hosting/discovery`` (WOPI discovery XML) listing, per
file type, the ``urlsrc`` the browser must load. The editor page is then
``urlsrc + "WOPISrc=<our WOPI file URL>"``.
"""

from __future__ import annotations

import threading
import xml.etree.ElementTree as ET  # discovery comes from our own Collabora container
from urllib.parse import quote

import httpx


class EditorUnavailable(Exception):
    pass


class CollaboraDiscovery:
    def __init__(self, collabora_url: str, *, timeout_seconds: float = 10) -> None:
        self._url = f"{collabora_url}/hosting/discovery"
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._xlsx_edit_urlsrc: str | None = None

    def _fetch(self) -> str:
        try:
            resp = httpx.get(self._url, timeout=self._timeout)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise EditorUnavailable("The spreadsheet editor is not reachable") from exc
        root = ET.fromstring(resp.text)
        for action in root.iter("action"):
            if action.get("ext") == "xlsx" and action.get("name") == "edit" and action.get("urlsrc"):
                return action.get("urlsrc")  # type: ignore[return-value]
        raise EditorUnavailable("The spreadsheet editor does not offer .xlsx editing")

    def xlsx_edit_urlsrc(self) -> str:
        with self._lock:
            if self._xlsx_edit_urlsrc is None:
                self._xlsx_edit_urlsrc = self._fetch()
            return self._xlsx_edit_urlsrc

    def editor_url(self, wopi_src: str) -> str:
        urlsrc = self.xlsx_edit_urlsrc()
        sep = "" if urlsrc.endswith(("?", "&")) else ("&" if "?" in urlsrc else "?")
        # closebutton=1 shows Collabora's own Close button, which posts UI_Close to our page.
        return f"{urlsrc}{sep}WOPISrc={quote(wopi_src, safe='')}&lang=en-GB&closebutton=1"
