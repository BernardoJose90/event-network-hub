# Event Network Hub

Web Summit's event network requirements in one place: Wi-Fi networks, Ethernet requirements and partner orders, kept as structured records per event. Events start empty or from a template (LIS, QATAR, RIO, VAN), and **Publish** creates a numbered version as an Excel workbook and a PDF report. It started as a test harness for one feature, uploading an Excel workbook and downloading it as a PDF that looks like the spreadsheet, and that path is still available under Legacy. Viewers and network admins both see the same complete content; viewers are trusted, so nothing is redacted.

(Internal names such as the Docker Compose project `portal-pdf-test` are unchanged, because renaming the project would point the app at a new, empty data volume.)

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
| collabora | `127.0.0.1:9980`               | In-browser spreadsheet editor (Collabora Online, CODE edition); the browser loads it directly |
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

For each `samples/*.xlsx`, the script uploads the file, downloads the PDF as each role and prints PASS/FAIL for each check:

- **a)** viewer and network-admin receive the same PDF;
- **b)** `pdffonts` lists Overpass and does not list DejaVu;
- **c)** the page count equals the number of visible sheets;
- **d)** the Excel download is byte-identical to the uploaded file;
- **e)** uploading the same version number again is rejected;
- **f)** the in-browser editor opens: Collabora's page loads and it can read the file through WOPI;
- **g)** the live sheet: it's created, an edit saved through the API appears in its PDF, and a stale save is refused;
- **h)** requirements: the workbook imports into records, publishing gives the next version (PDF report in Overpass, no DejaVu), and re-importing the published Excel gives identical records and totals;
- **i)** deleting is refused until the document is archived; then it works and removes its files. This also cleans up, so smoke runs leave no documents behind.

The smoke test uploads your file under a throwaway `SMOKE<n>` event code, so it never touches your real documents.

> `samples/`, `*.xlsx` and `*.pdf` are git-ignored, because sample workbooks can contain credentials. The smoke script keeps its downloads in a private temp dir that is deleted on exit.

## Architecture

The React UI talks only to the FastAPI backend, through the Vite `/api` proxy.

On upload, the backend:

1. validates the file;
2. checks the file name, then finds or creates the event's document and refuses a version number that already exists;
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
| POST   | `/api/documents/upload`                       | multipart `file` named `<EVENT> _ Event_Network_Requirements_V<n>.xlsx`. Needs `X-Role: network-admin`. Creates the event's document on first upload, otherwise adds V<n> to it. 409 if V<n> already exists, 422 for a bad name or file |
| DELETE | `/api/documents/{id}`                         | Needs `X-Role: network-admin`. Removes the document, all versions, stored files and download log rows. 204; 409 if the event isn't archived |
| POST   | `/api/documents/{id}/archive`, `/restore`      | Needs `X-Role: network-admin`. Archived events are read-only; restore undoes it |
| GET    | `/api/documents/{id}/versions`                |                                                           |
| GET    | `/api/documents/{id}/download[?version=N][&format=pdf\|xlsx]` | needs `X-Role` (`viewer` or `network-admin`). `pdf` (default) is the converted PDF, `xlsx` the original upload |

### Behaviour notes

- **TEST ONLY identity.** `X-Role` and `X-User` are trusted as sent. A missing or unknown role gets 403.
- **File names drive documents and versions.** Uploads must be named like `LIS26 _ Event_Network_Requirements_V1.xlsx`. Spaces, underscores and hyphens are interchangeable, so `VAN26 _ Event Network Requirements - V1.xlsx` also works. `|` also works after the event code, and matching ignores case. The older title "Event Network Matrix" is accepted in place of "Event Network Requirements", the version may be bracketed (`[V4]`, `(V4)`), and a browser's repeat-download suffix such as ` (1)` is ignored. Every spelling maps to the same document and version, so a version can't be uploaded twice under a differently written name. The event code (`LIS26`) picks the document, named `LIS26 | Event_Network_Requirements`, which is created on the first upload. `V<n>` is the version number. Each version number can be uploaded once; a different file under an existing number is rejected. A version whose conversion failed can be uploaded again to retry. Versions may arrive out of order, and View/Download use the highest ready version. The rule lives in `app/naming.py`, with the required text in `config.REQUIRED_NAME`.
- **No redaction.** Viewers are trusted, so every role sees the workbook as-is, passwords included. The role header is kept so real authorization can be added later.
- **Print gridlines are turned off for conversion.** With `singlePageSheets=true`, LibreOffice draws "print gridlines" at the wrong positions, so lines cut through the text. Seen with LibreOffice 26.8.0.3 (gotenberg 8.37.0) and 26.8.1.1 (gotenberg 8.38.1). `app/pdf_prep.py` removes the flag from the copy sent to Gotenberg; the stored original is untouched, and cell borders still render.
- **Formula errors may look different in the PDF.** LibreOffice recalculates formulas, so a broken formula such as `sum(#REF!)`, saved as `#REF!`, can appear as `#NAME?`. Both are errors in the source workbook and are listed as upload warnings.
- **Clipped text.** Text wider than its column is cut off in the PDF, exactly as Excel prints it. Widen the column in the source workbook if a value must be readable in full.

## Requirements (the app as the master copy)

**Open Network Requirements** on an event opens `/event/<document id>`. There, an event's network requirements are kept as structured records instead of spreadsheet cells:

- **Overview:** totals per venue and section, counts, list values to review, unpublished changes, and **Publish V<n>** (admins).
- **Wi-Fi networks, Ethernet requirements, Partner orders:**
  - venue tabs, with section rows showing live subtotals;
  - click a cell to edit; VLAN and location come from the Setup lists; numbers are checked;
  - **+ Add** per section, move up and down, search.
  - **Delete** removes an item after a confirmation, with **Undo** for 6 seconds (the delete is only sent when that time is up, or straight away when you leave the page). History keeps the deleted values, except passwords, and published versions keep their snapshot.
  - Wi-Fi passwords are masked until **Reveal**, and every reveal is logged.
  - Wi-Fi **Band** is a dropdown: "5 GHz & 6 GHz" or "5 GHz". Other spellings ("5Ghz", "5 ghz") are normalised; an older value outside the options (e.g. "6 GHz") is kept until someone changes it.
- **Setup:** venues, Ethernet sections, VLANs, locations, buildings, bandwidths. (Wi-Fi groups such as Core Networks and Partners come with the event or template and aren't edited here.) A value in use can't be removed, and renaming a value updates every record that uses it.
- **History:** every change is recorded automatically (who, when, old → new), so nobody keeps a change log by hand. Password values are never logged. An imported workbook's old "Changes - Log" appears here as legacy entries.

**Archiving events.** **Archive event** on the Events page moves an event under **Archived** in the sidebar (visible to everyone). An archived event is read-only for all roles: no edits, imports, publishing, uploads or editor sessions until a network admin clicks **Restore**. Its versions can still be viewed and downloaded, and it can still be saved as a template. **Delete event** is only offered for archived events, so deleting is always a deliberate second step; the API refuses to delete an event that isn't archived. Templates aren't archived; they're deleted directly.

Viewers see the same pages read-only. Editing and publishing are for network admins. Each record has a revision, so concurrent edits get "X changed this record, reload" instead of overwriting each other.

**Getting started:** network admins click **+ New event** on the Events page and enter an event code (e.g. `WEB26`). No workbook is needed. **Start from**:
- **Empty:** one venue ("Main venue"), the usual Wi-Fi groups and Ethernet sections, and empty lists; or
- **a template** (e.g. LIS, QATAR, RIO, VAN): the new event gets a copy of everything in it. Later changes to the template don't affect events already created.

**Templates** are made with **Save as template** on an event's requirements page. A template keeps the event's venues, sections, lists, Wi-Fi networks and Ethernet items, with **every Ethernet point set to 0**, **Ethernet notes empty**, **no Wi-Fi passwords**, **no partner orders**, and **nothing in partner sections** (the Wi-Fi "Partners" group and Ethernet sections with "partner" in the name, such as PARTNER MEETING ROOMS or STANDARD PARTNER/STARTUP STANDS; the section headings are kept). In a template, Ethernet points and notes can't be changed (they're set on the event). Templates are stored in the app (documents with `kind = 'template'`), not in `samples/`. They aren't in the Events list, have no versions and can't be published. Admins open or delete them under **Templates** in the sidebar, and edit them on the same requirements page.

The event has no versions until the first **Publish**, which creates V1. Uploading a workbook for the same event code later adds a version to the same document.

For an existing workbook, use **Legacy: upload a workbook** in the sidebar, then click Overview → **Import from V<n>**. Two layouts are recognised:
- per-venue sheets (`<Venue> - Wi-Fi Req`, `<Venue> Ethernet Req`), e.g. LIS25;
- single-venue sheets (`Wi-Fi Requirements`, `Ethernet Requirements`), e.g. VAN26; these go into "Main venue", which you can rename under Lists.

The importer also reads Partners Custom Orders, the Setup or settings lists, and the old Changes - Log. An "Extending Vlans" sheet is skipped with a note, because VLAN extensions are no longer tracked. Anything it can't place is reported as an import note: unrecognised sheets, columns with no matching field, and non-numeric points such as "TBC".

**Publishing** creates the next version:
- an **Excel file in the familiar workbook layout**, with real `SUM` totals and dropdowns, which re-imports to the same records;
- a **PDF report** rendered by Gotenberg's Chromium route: A4 landscape, a cover page, section subtotals, and the changes since the previous version.

Versions show whether they were uploaded, edited or published.

Code: `backend/app/records/` (models, store, service, importer, publish, routes) and `frontend/src/event/`.

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/events` | `{event_code, template_id?}`; admin; 409 if the event exists |
| GET/POST | `/api/templates` | POST `{name, event_id}`: save an event as a template; admin |
| DELETE | `/api/templates/{id}` | admin; events created from it are kept |
| GET | `/api/events/{id}/overview` | totals, counts, lists, unpublished changes |
| POST | `/api/events/{id}/import` | `{replace}`; from the latest ready version |
| GET/POST | `/api/events/{id}/records/{type}` | type: `wifi`, `ethernet`, `partner` |
| PATCH/DELETE | `/api/events/{id}/records/{type}/{rid}` | revision checked |
| POST | `/api/events/{id}/records/{type}/{rid}/move` | |
| POST | `/api/events/{id}/records/wifi/{rid}/reveal` | logged |
| POST/PATCH/DELETE | `/api/events/{id}/lists/…`, `/venues/…`, `/groups/…` | |
| GET | `/api/events/{id}/history`, `/changes` | |
| POST | `/api/events/{id}/publish` | next version (Excel + PDF report) |

## Editing in the browser

Network admins can click **Edit** on any ready version. That opens a full-screen editor page, `/edit/<document id>/<version>`, in the same tab. The workbook appears in **Collabora Online**, a LibreOffice-based office suite, with every sheet, the formatting, formulas and an Excel-style toolbar. **Saving creates a new version** numbered after the newest one, e.g. V6 becomes V7. It's marked "edited from V6", converted to PDF like any upload, and the opened version stays unchanged. Later saves in the same session, including autosaves, update that same new version, so one editing session produces one version. **Back to documents** saves unsaved changes first, then returns to that document (`/?doc=<id>`), where the new version is listed.

How it fits together, using the standard WOPI protocol:

1. The page calls `POST /api/documents/{id}/versions/{n}/edit`. The backend records an edit session and signs a short-lived access token (HMAC; it names the session, the user and an expiry). It returns the editor URL, found from Collabora's `/hosting/discovery`.
2. The page POSTs the token into an iframe pointing at Collabora (`localhost:9980`).
3. Collabora calls the backend's WOPI endpoints on the compose network (`http://backend:8000/wopi/files/{session}`): `CheckFileInfo`, `GetFile` and `PutFile`. `PutFile` runs `service.save_edit`. Collabora only accepts that WOPI host (`aliasgroup1`), and only our page may frame it.
4. Access tokens are masked in the access logs.

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/documents/{id}/versions/{n}/edit` | Needs `X-Role: network-admin`. Returns `editor_url`, `access_token`, `access_token_ttl` |
| GET  | `/wopi/files/{session}` | WOPI CheckFileInfo (called by Collabora) |
| GET  | `/wopi/files/{session}/contents` | WOPI GetFile |
| POST | `/wopi/files/{session}/contents` | WOPI PutFile; saves as the session's new version |

## Live sheet (edit in the app, not Excel)

**Open live sheet** on a document (network admins) opens `/sheet/<document id>`: an editable web grid that looks like the workbook. It has one tab per sheet, the same fills, fonts and column widths, and frozen header rows.

- **Editing:** click a cell and type; Enter, Tab and the arrow keys move; Delete clears; double-click edits.
- **Dropdowns and tick-boxes:** cells that had dropdowns offer the same lists, taken from the Setup sheet or a fixed list such as Yes/No/TBC. TRUE/FALSE cells are tick-boxes.
- **Rows and columns:** right-click a row number, column letter or cell to insert or delete. A new row copies the dropdowns, tick-boxes and look of its neighbours.
- **Automatic totals:**
  - each section total adds every row in its section, including rows added later;
  - the overall total adds the section totals;
  - per-row formulas such as Change Qty (`V6 - V5`) recalculate as you type.
  The original workbook's totals don't always match these:
  - **Altice Arena – Ethernet Req:** INTERNAL WORKSPACES was `=SUM(H3:H22)` and missed three rows (108 → 232). Four totals were broken `sum(#REF!)`.
  - **FIL 1-5 Ethernet Req:** Registration Areas missed one row (15 → 19), and its overall total left out INTERNAL WORKSPACES.
- **Saving:** automatic, about 1.5 s after the last change, with undo and redo (Ctrl+Z / Ctrl+Shift+Z). Each save carries a revision number. If someone else saved in the meantime, the save is refused (409) and the page offers to reload, so edits are never silently overwritten.
- **Separate from versions:** the live sheet starts from the latest version. Later uploads don't touch it; the page shows "V8 uploaded since. Start again from V8".
- **Downloads:** Excel and PDF are generated from the live sheet on download. Totals are written as real `SUM` formulas over their current ranges.
- **Not supported yet:** writing new formulas, changing colours or bold, multi-cell copy and paste, charts and images. Formulas it can't represent keep their last value and are listed as import notes.

Code: `backend/app/sheets/` (model, importer, engine, exporter) and `backend/app/sheet_routes.py`; `frontend/src/SheetPage.jsx` and `frontend/src/sheet/` (grid, ops, engine). The calculation rules exist twice, in `sheets/engine.py` for downloads and `sheet/engine.js` for the page, and must stay in step.

| Method | Path | Notes |
|--------|------|-------|
| GET  | `/api/documents/{id}/sheet` | 404 until created |
| POST | `/api/documents/{id}/sheet` | create, or start again, from the latest ready version |
| PUT  | `/api/documents/{id}/sheet` | `{data, revision}`; 409 if someone else saved first |
| GET  | `/api/documents/{id}/sheet/download?format=xlsx\|pdf` | generated on demand |

## UI and accessibility

The interface follows these published standards:

- **[WCAG 2.2](https://www.w3.org/WAI/standards-guidelines/wcag/new-in-22/) level AA:**
  - text contrast of at least 4.5:1, and 3:1 for controls and focus rings;
  - a visible focus ring everywhere;
  - pointer targets of at least 24×24px;
  - a skip link and landmarks, so keyboard users can jump to the content;
  - page titles that name the event and the view;
  - status messages announced to screen readers ([SC 4.1.3](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html)): `role="status"` for results, `role="alert"` for errors;
  - support for reduced motion and Windows forced colours.
- **[WAI-ARIA Authoring Practices](https://www.w3.org/WAI/ARIA/apg/):**
  - **Data grid** for the requirement tables. Tab enters the grid once. Arrow keys, Home/End, Ctrl+Home/End and PageUp/PageDown move between cells. Enter, F2 or typing starts editing. Enter saves, Escape cancels and Tab saves and moves on, and focus returns to the cell afterwards.
  - **Tabs** for venues: arrow keys, Home and End.
  - **Modal and alert dialogs** in place of the browser's `confirm()` and `prompt()`, using native `<dialog>`. Focus moves into the dialog, onto Cancel for destructive steps, and back to the button that opened it. Prompts show inline errors linked to the field.
- **[GOV.UK Design System](https://design-system.service.gov.uk/components/notification-banner/):** messages carry an icon and a text label ("Success", "Note", "Error"), so meaning never depends on colour alone.
- **[Nielsen Norman Group's heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/):**
  - feedback on every action, plus loading skeletons;
  - Undo after deleting (user control);
  - confirmations before hard-to-reverse steps (error prevention);
  - plain-language errors that say how to recover.

Shared pieces live in `frontend/src/ui/` (`Dialog.jsx`, `Notice.jsx`) and `frontend/src/event/grid.js`. The browser's built-in pop-ups remain only on the legacy Excel editor and live sheet pages.

**Accessibility audit.** It runs axe-core 4.10.2, pinned as a dev dependency, against every page of an event and a template, using headless Chrome. It only views pages and exits 1 on any violation:

```bash
(cd frontend && npm ci)
node scripts/a11y.mjs <event id> [<template id>]   # APP_URL and CHROME can be overridden
```

Automated checks find only part of the problems. Keyboard use and screen-reader announcements were also checked by hand. Before a release, test the requirement tables and dialogs with a screen reader (VoiceOver or NVDA).

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
- **Spreadsheet editor (Collabora).**
  - Licensing: CODE is the development edition, for testing and small teams. Production needs a Collabora Online subscription and its enterprise image.
  - Transport: serve it over HTTPS behind the ingress, set `server_name` to its public hostname, restrict `aliasgroup1` to the backend's internal service and set `frame_ancestors` to the portal's origin.
  - Secrets: set `WOPI_TOKEN_SECRET` from the secret store. Locally a random one is generated per start, which ends open editor sessions.
  - Authentication: issue editor tokens from the real authenticated user.
  - Integrity: validate Collabora's WOPI proof-key signatures (`X-WOPI-Proof`). The local container has no proof key and the backend doesn't check them yet.
  - Scaling: Collabora pins each document to one instance, so use sticky routing by WOPISrc when running more than one.
- **Hardening.**
  - Enforce the upload size limit at the ingress.
  - Run containers with a read-only root filesystem and drop all capabilities.
  - Scan images in CI.
  - Serve a production frontend build, static files from S3 and CloudFront or nginx, instead of the Vite dev server.
