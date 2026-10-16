# portal-pdf-test

A small test harness that proves one feature: **upload an Excel workbook and download it as a PDF that looks like the spreadsheet**, with one page per sheet. Viewers and network admins both get the same, complete PDF; viewers are trusted, so nothing is redacted.

It is built to production standards but kept deliberately small, so the code can move into the production app (Python backend, React frontend, EKS).

## Run it

Requirements: Docker with Compose v2.

```bash
docker compose up --build
```

Then open <http://localhost:5173>.

| Service   | Exposed on                     | Notes                                       |
|-----------|--------------------------------|---------------------------------------------|
| frontend  | `127.0.0.1:5173`               | Vite dev server; proxies `/api` to backend  |
| backend   | `127.0.0.1:8000`               | FastAPI; data in the `appdata` volume       |
| gotenberg | not published                  | Reachable only from the backend             |

Configuration defaults live in `docker-compose.yml`; copy `.env.example` to `.env` to override them. Nothing in the repo is a secret.

To reset all data: `docker compose down -v`.

## Test it

**Unit tests** (no Gotenberg needed):

```bash
docker compose run --rm --build tests
# or locally:
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest
```

These cover upload validation, sha256 duplicate detection, version-number race retry, formula error detection and the download API.

**Smoke test** (needs the stack running, plus `jq` and poppler's `pdffonts` and `pdfinfo`, e.g. `brew install poppler jq`):

```bash
mkdir -p samples && cp /path/to/your/workbook.xlsx samples/
scripts/smoke.sh
```

For each `samples/*.xlsx`, the script creates a document, uploads the file, downloads the PDF as each role and prints PASS/FAIL for each check:

- **a)** viewer and network-admin receive the same PDF;
- **b)** `pdffonts` lists Overpass and does not list DejaVu;
- **c)** the page count equals the number of visible sheets.

> `samples/`, `*.xlsx` and `*.pdf` are git-ignored, because sample workbooks can contain credentials. The smoke script keeps its downloads in a private temp dir that is deleted on exit.

## Architecture

The React UI talks only to the FastAPI backend, through the Vite `/api` proxy.

On upload, the backend:

1. validates the file;
2. skips files identical to the latest version, compared by sha256;
3. records formula-error warnings;
4. sends the workbook to Gotenberg's LibreOffice route (`POST /forms/libreoffice/convert`, `singlePageSheets=true`);
5. stores `original.xlsx` and `full.pdf`.

Files go through a small `Storage` interface (`put`/`get`; local volume today). Metadata is in SQLite: `documents`, `document_versions` (unique on document and version, with conflicting inserts retried) and `download_log`. Downloads serve the latest `ready` version, or `?version=N`, to any known role, and every download is logged.

Gotenberg runs a custom image based on `gotenberg/gotenberg:8.37.0`. It adds Lato, Roboto, Carlito, Caladea and Overpass 3.0.5; Overpass is downloaded from Red Hat's GitHub release and checked against a pinned sha256. Gotenberg is not published to the host.

```
backend/app/
  config.py      settings + FORMULA_ERRORS constant
  validation.py  upload validation
  xlsx.py        formula error scan + sheet count (openpyxl)
  converter.py   Gotenberg client
  service.py     upload pipeline (dedup, version allocation, convert, store)
  storage.py     Storage protocol + LocalStorage
  db.py          SQLite schema and queries
  auth.py        TEST ONLY fake identity (X-Role / X-User headers)
  routes.py      HTTP API
```

### API

| Method | Path                                          | Notes                                                     |
|--------|-----------------------------------------------|-----------------------------------------------------------|
| GET    | `/api/documents`                              |                                                           |
| POST   | `/api/documents`                              | `{"name": "..."}`                                         |
| GET    | `/api/documents/{id}/versions`                |                                                           |
| POST   | `/api/documents/{id}/versions`                | multipart `file`. 201 created, 200 unchanged, 422 invalid, 502 conversion failed |
| GET    | `/api/documents/{id}/download[?version=N]`    | needs `X-Role` (`viewer` or `network-admin`); both get the full PDF |

### Behaviour notes

- **TEST ONLY identity.** `X-Role` and `X-User` are trusted as sent. A missing or unknown role gets 403.
- **Re-uploading after a failure.** Duplicate detection ignores `failed` versions, so re-uploading the same file retries a failed conversion.
- **No redaction.** Viewers are trusted, so every role sees the workbook as-is, passwords included. The role header is kept so real authorization can be added later.
- **Print gridlines are turned off for conversion.** With `singlePageSheets=true`, LibreOffice draws "print gridlines" at the wrong positions, so lines cut through the text. Seen with LibreOffice 26.8.0.3 (gotenberg 8.37.0) and 26.8.1.1 (gotenberg 8.38.1). `app/pdf_prep.py` removes the flag from the copy sent to Gotenberg; the stored original is untouched, and cell borders still render.
- **Formula errors may look different in the PDF.** LibreOffice recalculates formulas, so a broken formula such as `sum(#REF!)`, saved as `#REF!`, can appear as `#NAME?`. Both are errors in the source workbook and are listed as upload warnings.
- **Clipped text.** Text wider than its column is cut off in the PDF, exactly as Excel prints it. Widen the column in the source workbook if a value must be readable in full.

## What changes for production

- **Storage: S3 with presigned URLs.** Add an `S3Storage` implementing `put`/`get`, plus a `presign(key)` method. Downloads return a short-lived presigned GET URL, after the authorization check and the `download_log` write, instead of streaming bytes through the API. Use bucket encryption (SSE-KMS), block all public access, and add a lifecycle policy for old versions.
- **Real authentication.** Replace `auth.py` with OIDC/SSO token validation in the backend. Map IdP group claims to `viewer` and `network-admin`, and fail closed. Take `uploaded_by` and the download user from the verified identity, never from headers. Add authorization per document if needed.
- **Google Drive sync as a Kubernetes CronJob.** A CronJob polls the Drive folder, downloads changed workbooks and feeds them through the same upload pipeline: sha256 dedup makes it idempotent. It runs as a separate job, not inside the API pods.
- **Workload Identity Federation.** No long-lived keys anywhere:
  - pods get AWS access to S3 through IRSA or EKS Pod Identity;
  - the Drive CronJob authenticates to Google through Workload Identity Federation, exchanging its Kubernetes service-account token for short-lived Google credentials, so no service-account JSON key is involved.
- **Alerting and observability.**
  - Metrics: conversions by outcome, conversion latency, Gotenberg 5xx and timeouts, failed versions, and the Drive sync's last-success timestamp.
  - Alerts: a high conversion failure rate, Gotenberg unhealthy, or the sync stale.
  - Structured JSON logs with request IDs.
- **Database.** Move from SQLite to Postgres (RDS) with migrations (Alembic). The unique-constraint-and-retry logic carries over unchanged.
- **Conversion off the request path.** Run uploads as a queued job (for example SQS plus a worker), so the API returns immediately with status `processing`. Gotenberg becomes an internal-only Deployment, scaled horizontally, with a NetworkPolicy that allows only the backend or worker to reach it.
- **Hardening.**
  - Enforce the upload size limit at the ingress.
  - Run containers with a read-only root filesystem and drop all capabilities.
  - Scan images in CI.
  - Serve a production frontend build, static files from S3 and CloudFront or nginx, instead of the Vite dev server.
