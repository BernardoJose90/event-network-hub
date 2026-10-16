"""PDF conversion through Gotenberg's LibreOffice route.

Gotenberg 8 (verified against v8.37.0 source, pkg/modules/libreoffice/routes.go):
  POST /forms/libreoffice/convert, multipart/form-data
  - file part(s): field "files"
  - "singlePageSheets": "true" renders each sheet onto exactly one page
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ConversionError(Exception):
    pass


class Converter(Protocol):
    def xlsx_to_pdf(self, data: bytes, *, filename: str) -> bytes: ...

    def html_to_pdf(self, html: str) -> bytes: ...


class GotenbergConverter:
    def __init__(self, base_url: str, *, timeout_seconds: float, client: httpx.Client | None = None):
        self._url = f"{base_url}/forms/libreoffice/convert"
        self._html_url = f"{base_url}/forms/chromium/convert/html"
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout_seconds))

    def close(self) -> None:
        self._client.close()

    def xlsx_to_pdf(self, data: bytes, *, filename: str) -> bytes:
        return self._convert(
            self._url, files={"files": (filename, data, XLSX_MEDIA_TYPE)}, data={"singlePageSheets": "true"}
        )

    def html_to_pdf(self, html: str) -> bytes:
        """Render a self-contained HTML page with Chromium.

        Gotenberg 8 (v8.37.0 pkg/modules/chromium/routes.go): POST
        /forms/chromium/convert/html with the page as "index.html"; the page's
        CSS @page rule sets the paper (preferCssPageSize) and backgrounds print.
        """
        return self._convert(
            self._html_url,
            files={"files": ("index.html", html.encode("utf-8"), "text/html")},
            data={"printBackground": "true", "preferCssPageSize": "true"},
        )

    def _convert(self, url: str, *, files: dict, data: dict) -> bytes:
        try:
            resp = self._client.post(url, files=files, data=data)
        except httpx.TimeoutException as exc:
            raise ConversionError("PDF conversion timed out") from exc
        except httpx.HTTPError as exc:
            raise ConversionError(f"PDF conversion service unavailable: {exc.__class__.__name__}") from exc

        if resp.status_code != 200:
            log.warning("gotenberg returned %s: %s", resp.status_code, resp.text[:500])
            raise ConversionError(f"PDF conversion failed (HTTP {resp.status_code})")
        if not resp.content.startswith(b"%PDF"):
            raise ConversionError("PDF conversion returned an unexpected response")
        return resp.content
