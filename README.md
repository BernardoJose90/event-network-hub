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

These cover upload validation, sha256 duplicate detection, version-number race retry and the download API.

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
- **f)** requirements: the workbook imports into records, publishing gives the next version (PDF report in Overpass, no DejaVu), and re-importing the published Excel gives identical records and totals;
- **g)** deleting is refused until the document is archived; then it works and removes its files. This also cleans up, so smoke runs leave no documents behind.

The smoke test uploads your file under a throwaway `SMOKE<n>` event code, so it never touches your real documents.

> `samples/`, `*.xlsx` and `*.pdf` are git-ignored, because sample workbooks can contain credentials. The smoke script keeps its downloads in a private temp dir that is deleted on exit.

## Architecture

The React UI talks only to the FastAPI backend, through the Vite `/api` proxy.

On upload, the backend:

1. validates the file;
2. checks the file name, then finds or creates the event's document and refuses a version number that already exists;
3. sends the workbook to Gotenberg's LibreOffice route (`POST /forms/libreoffice/convert`, `singlePageSheets=true`);
4. stores `original.xlsx` and `full.pdf`.

Files go through a small `Storage` interface (`put`/`get`; local volume today). Metadata is in SQLite: `documents`, `document_versions` (unique on document and version, with conflicting inserts retried) and `download_log`. Downloads serve the latest `ready` version, or `?version=N`, to any known role, and every download is logged.

Gotenberg runs a custom image based on `gotenberg/gotenberg:8.37.0`. It adds Lato, Roboto, Carlito, Caladea and Overpass 3.0.5; Overpass is downloaded from Red Hat's GitHub release and checked against a pinned sha256. Gotenberg is not published to the host.

```
backend/app/
  config.py      settings + FORMULA_ERRORS constant
  validation.py  upload validation
  xlsx.py        visible sheet count (openpyxl)
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
- **Formula errors may look different in the PDF.** LibreOffice recalculates formulas, so a broken formula such as `sum(#REF!)`, saved as `#REF!`, can appear as `#NAME?`. Both are errors in the source workbook.
- **Clipped text.** Text wider than its column is cut off in the PDF, exactly as Excel prints it. Widen the column in the source workbook if a value must be readable in full.

## Requirements (the app as the master copy)

**Open Network Requirements** on an event opens `/event/<document id>`. There, an event's network requirements are kept as structured records instead of spreadsheet cells:

- **Overview:** totals per venue and section, counts, list values to review, unpublished changes, and **Publish V<n>** (admins).
- **IDs:** the ID column is the row number: 1, 2, 3… in display order, per venue for Wi-Fi networks and Ethernet requirements (as each venue tab shows them), and down the list for partner orders. Adding a row continues the count, and removing or moving a row renumbers the rows below it. IDs aren't stored or edited; IDs in imported workbooks are ignored. Published Excel and PDF files show the same numbers.
- **Wi-Fi networks, Ethernet requirements, Partner orders:**
  - venue tabs, with section rows showing live subtotals;
  - click a cell to edit; VLAN and location come from the Setup lists; numbers are checked;
  - **+ Add** per section, move up and down, search.
  - **Delete** removes an item after a confirmation, with **Undo** for 6 seconds (the delete is only sent when that time is up, or straight away when you leave the page). History keeps the deleted values, except passwords, and published versions keep their snapshot.
  - Wi-Fi passwords are masked until **Reveal**, and every reveal is logged.
  - Wi-Fi **Band** is a dropdown: "5 GHz & 6 GHz" or "5 GHz". Other spellings ("5Ghz", "5 ghz") are normalised; an older value outside the options (e.g. "6 GHz") is kept until someone changes it.
- **Setup:** venues, Ethernet sections, Wi-Fi sections (such as Core Networks, Lounges and Partners), VLANs, locations, buildings, bandwidths. A value in use can't be removed, and renaming a value updates every record that uses it.
- **History:** every change is recorded automatically (who, when, old → new), so nobody keeps a change log by hand. Password values are never logged. An imported workbook's old "Changes - Log" appears here as legacy entries.

**Archiving events.** **Archive event** on the Events page moves an event under **Archived** in the sidebar (visible to everyone). An archived event is read-only for all roles: no edits, imports, publishing, uploads or editor sessions until a network admin clicks **Restore**. Its versions can still be viewed and downloaded, and it can still be saved as a template. **Delete event** asks you to type the event code to confirm, because it can't be undone. On an active event the app archives it first and then deletes it; the API itself refuses to delete an event that isn't archived. Templates aren't archived; they're deleted directly.

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

**Changelog per version.** Each published version has an item-level changelog, built by comparing its snapshot with the previous published version's:
- **Added**, **Removed** and **Changed** items, grouped by Wi-Fi networks, Ethernet requirements and partner orders, with old → new values per field. A move between sections shows as "Moved".
- **Ethernet point totals** per venue, before and after.
- An optional **note** written when publishing, shown at the top.
- It shows the **net result**: five edits to one item become one line, and a change that was undone doesn't appear. Wi-Fi password values never appear, only "password changed".

Where to see it:
- **Compare versions…** on the Events page, for any two ready versions (the latest two by default), for example V3 → V6. Every new version is added automatically, whether it was published or uploaded (or edited in the spreadsheet editor, before it was removed):
  - published versions use the item list saved when they were published;
  - uploaded and edited versions are read from their Excel file the first time they're compared, then cached (table `workbook_snapshots`). The comparison says so in a note;
  - items read from a workbook have no record IDs, so they're matched on what identifies them (Wi-Fi: venue and SSID; Ethernet: venue, section, purpose, location and room; partner orders: partner and stand). Changing one of those fields shows as removed plus added.
- The publish dialog, before you publish.
- Overview → "Not yet published".
- Inside the published PDF ("Changes in this version") and Excel (the "Changes - Log" sheet).

Uploaded or Excel-edited versions have no item list, so they can't be compared. API: `GET /api/events/{id}/changelog?to=<version|draft>[&from=<version>]`.

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
| GET/PUT/POST | `/api/events/{id}/ict-requests`, `…/settings`, `…/links`, `…/{rid}/approve`, `…/needs-info`, `…/decline` | ICT request review |
| GET/POST/PUT | `/api/request-links/{token}`, `…/requests[/{rid}]` | public request form (the link is the credential) |
| POST | `/api/events/{id}/publish` | `{note?}`; next version (Excel + PDF report) |
| GET | `/api/events/{id}/changelog?to=<version|draft>&from=<version>` | item-level changelog |

## Editing

Changes are made only in the requirements (**Open Network Requirements**), then **Publish** creates the next version. Versions themselves can't be edited: they can be viewed, downloaded and compared. The in-browser spreadsheet editor (Collabora) and the live sheet were removed; versions they created earlier (marked "edited from V<n>") are kept in the history and can still be viewed and compared.

## Paused areas

The **Help desk** and **Reports** are paused: `FEATURE_HELPDESK` and `FEATURE_REPORTS` default to `false` in `docker-compose.yml`. While an area is paused:

- its link is hidden from the top bar, and its page (`/helpdesk`, `/reports`) says it's paused;
- its API returns 404, including the WhatsApp bot intake (`/api/bot/...`), and no SLA checks or bot updates run;
- help desk alert types are hidden from the bell's Settings, and each person's choices are kept;
- all its data (tickets, history, settings) is kept.

To switch one back on, set it to `true` in `.env` and run `docker compose up -d backend`. `GET /api/features` reports which areas are on.

## Help desk (ICT tickets for live events)

`/helpdesk` (the **Help desk** link in the top bar) is the system of record for ICT issues during build, live days and breakdown. It covers Wi-Fi, wired connectivity, network hardware, partner stand connectivity and venue-wide problems. Every issue becomes a ticket with an owner, severity, timestamps and a full audit trail. Tickets are never deleted, and they stay in the history across events, even if the event is deleted later.

- **Dashboard:** live, refreshing every 10 seconds, with a **Big screen** mode.
  - Counts: open, waiting to be acknowledged, overdue SLA, open P1/P2, partner stands, and per team.
  - Lists: "Needs acknowledging" (oldest first), "Overdue SLA" and "Recently resolved".
  - New unacknowledged tickets are announced to screen readers.
- **Tickets:** a queue with saved views (All open, Needs acknowledging, My tickets, Unassigned, Partner stands, Overdue SLA, Resolved & closed, All).
  - Search covers the title, area, partner, reporter and `#number`.
  - Filters: severity, team and category.
  - Clicking a ticket opens its detail. Admins (the ICT team) can:
    - **Acknowledge**, **Start work**, mark **Waiting**, **Resolve…**, **Close…** or **Reopen**;
    - **Assign to me**, **Assign…** (owner and team), **Edit…** or **Merge…** (merging closes the ticket as a duplicate);
    - add an internal note, or reply to a WhatsApp reporter.
  - Everyone else sees the same pages read-only.
- **Raise ticket:** the manual route, for issues reported in person, by radio or by phone. Location and partner suggestions come from the event's Setup lists and partner orders.
- **Reports:** per event or across all events.
  - Share of tickets acknowledged within 5 minutes (goal 90%) and the median.
  - Coverage, after you enter how many issues were handled outside the system (goal 90%).
  - Share closed with a root cause and a fix (goal 90%).
  - Partner-impacting issues, with mean time to resolve.
  - Recurrence: an earlier ticket in the same category and area, in any event.
  - Resolution against SLA per severity, tickets per category, and the busiest areas.
  - **Export CSV / Excel**, ready for the post-event report. Text that could run as a spreadsheet formula is neutralised.
- **Recurring issues:** problems seen more than once in the same category and area, across events, with the root causes and fixes recorded. This is the knowledge base.

**Lifecycle:** New → Acknowledged → In progress ⇄ Waiting → Resolved → Closed. Reopening is allowed.
- **Acknowledge** stamps the time and, if the ticket has no owner, assigns it to you. Moving a ticket on from New also counts as acknowledging it.
- **Resolve** and **Close** require a root cause and a resolution, which feed the closure-quality KPI and the recurring-issues list.
- **Merge** closes a duplicate as "Duplicate of #n". Its WhatsApp reporter then gets the main ticket's updates.
- Each ticket has a revision, so two people changing it at once get "changed by someone else, reload" instead of overwriting each other.

**Default severity and SLA matrix.** Change it in `backend/app/tickets/models.py` (`SEVERITIES`). Times are measured from creation.

| Severity | Meaning | Acknowledge | Resolve |
|---|---|---|---|
| P1 Critical | Venue-wide, or a keynote, stage or registration outage | 5 min | 15 min |
| P2 High | A partner stand or critical area is down | 5 min | 30 min |
| P3 Medium | Degraded service in one area or stand | 5 min | 2 h |
| P4 Low | A single user or device, or a request | 5 min | 8 h |

**Teams:** Network team and IT Support. New tickets go to a team by category: Network hardware goes to IT Support, everything else to the Network team. They can be reassigned. These defaults are `TEAMS` and `DEFAULT_TEAM` in the same file.

**Live event.** WhatsApp reports that don't name an event are filed under the live event. Set it on the help desk page: choose an event, then **Make this the live event**.

### WhatsApp bot integration (for the bot developer)

The integration is off until it's configured. Set the values in `.env` from a secret store; never commit them:

| Variable | Purpose |
|---|---|
| `BOT_API_TOKEN` | The bot sends `Authorization: Bearer <token>` |
| `BOT_CALLBACK_URL` | Where status updates for reporters are POSTed |
| `BOT_CALLBACK_SECRET` | HMAC key for the `X-Signature` header (required with the URL) |
| `APP_PUBLIC_URL` | Base of the ticket links returned to the bot |

**Create a ticket:** `POST /api/bot/tickets`

```json
{
  "external_id": "wamid.HBgM...",
  "message": "No internet on stand A12",
  "event_code": "LIS26",
  "category": "partner_stand",
  "severity": "P2",
  "location": "Hall 2",
  "stand": "A12",
  "partner": "IBM",
  "reporter_name": "Ana",
  "reporter_phone": "+351900000000"
}
```

- Only `external_id` (the bot's own id for the report) and `message` are required.
- `category` is one of `wifi`, `wired`, `hardware`, `partner_stand`, `venue`. If it's left out, the ticket is "Not yet categorised", unless `partner` is given, which makes it `partner_stand`.
- `severity` is `P1` to `P4`; P3 by default.
- The first line of `message` becomes the title.
- The reply is `201 {"ticket": 12, "status": "new", "status_label": "New", "url": ".../helpdesk?ticket=12", "created": true}`.
- Sending the same `external_id` again returns the existing ticket with `200` and `"created": false`, so retries are safe.
- Errors: `401` for a bad token, `503` if the integration isn't configured, `422` for invalid fields.

**Check status:** `GET /api/bot/tickets/{external_id}` returns the same shape.

**Updates to the reporter.** On acknowledge, status changes, merges and "Reply to reporter" notes, the app POSTs JSON to `BOT_CALLBACK_URL`:

```json
{
  "type": "ticket.update",
  "ticket": 12,
  "reported_as": 12,
  "external_id": "wamid...",
  "reporter_phone": "+351...",
  "status": "acknowledged",
  "status_label": "Acknowledged",
  "message": "Ticket #12: alice from the ICT team is on it."
}
```

- `reported_as` differs from `ticket` when the report was merged into another ticket.
- Verify the `X-Signature: sha256=<hex>` header: an HMAC-SHA256 of the raw body with `BOT_CALLBACK_SECRET`, compared in constant time.
- Answer with any 2xx. Failures are retried with backoff (10 s, 20 s, 40 s … up to 30 min) and marked failed after 10 attempts. Delivery counts are in `GET /api/tickets/meta` (`bot.outbox`).

Code: `backend/app/tickets/` (models, store, service, report, outbox, routes) and `frontend/src/helpdesk/`.

## ICT requests (wired and private Wi-Fi)

Internal teams (AV, Photographers & Videographers, Engineering, CCTV) request connectivity per workspace using a standard form, instead of ICT guessing cable numbers. It follows the ITIL request catalogue: standard request models, plus GOV.UK form guidance.

- **Links.** On **ICT requests**, an admin creates a link per team, then copies or turns it off. The link is the credential: anyone with it can submit for that team and see that team's requests. It holds a long random token, can be revoked, and stops working when the event is archived.
- **Deadline and instructions.** Set per event. Requests after the deadline still arrive but are marked late, which feeds the "unknown requirements" measure.
- **Public form** (`/request/<token>`): one or more workspaces, each either:
  - a **wired connection**: area from the event's Setup lists, room, purpose, device (with suggestions from the team's template), number of cables, PoE (power over the cable), speed, and needed-by date; or
  - a **private Wi-Fi network**: preferred name, number of devices, coverage, and password or open.

  "Not sure yet" is a valid answer and shows as a gap to follow up. Errors appear in a summary at the top that links to each field, then next to the field. After sending, a confirmation shows the request numbers. Teams follow progress on the same page, and answer ICT's questions there.
- **Review.**
  - Per-team totals: requests, items to review, ports requested, "not sure" answers, late requests.
  - Actions: **Approve…**, **Ask…** (needs more information), or **Decline…** with a reason the requester sees.
  - **Approving creates the Ethernet item or Wi-Fi network** in the event's requirements. You choose the venue, section or group, VLAN and location; for a wired request where the team wasn't sure, you also enter the number of points.
  - The new row's notes cite the request: "ICT request #n (team, requester)". It goes out with the next published version.
- **Unpublished changes.** Submissions and reviews don't count as changes; approvals do, because they add rows.

## Reports (cross-event)

**Reports** in the top bar shows how each event did against the ICT objectives, with events side by side. By default it shows all active events; archived events can be ticked in for comparison. Everyone can read it, and the data can be downloaded as Excel or CSV.

1. **Cable planning:**
   - Ethernet points and items per event, with a breakdown per venue and section;
   - points that came through ICT requests;
   - items and points added after the request deadline;
   - wired help desk tickets on site.

   Together these measure fewer unused cables and fewer "unknown" requirements. It measures points *planned*, because ports actually used after the event aren't recorded.
2. **ICT request adoption:** requests per event and per team, teams using the form, the share submitted on time (goal 90%), the share with "not sure" answers, review time, and requests still waiting.
3. **Live operations:** the help desk KPIs per event: acknowledged within 5 minutes (goal 90%), closure quality (goal 90%), time to resolve, partner-impacting tickets, and recurrence.
4. **Change and publishing:** versions and published versions, first and last publish dates, the changelog from the first to the last published version (including the Ethernet point change), and unpublished changes.

**Presentation** follows the UK Government Analysis Function guidance on [charts](https://analysisfunction.civilservice.gov.uk/policy-store/data-visualisation-charts/) and [dashboards](https://analysisfunction.civilservice.gov.uk/policy-store/data-visualisation-building-and-managing-dashboards/), and the W3C guidance on [complex images](https://www.w3.org/WAI/tutorials/images/complex/):
- **At a glance** comes first: the most recent selected event, compared with the one before it.
- **Before you compare** lists data gaps that affect the figures, for example items with 0 points or no request deadline.
- **Charts:**
  - statement titles ("QATAR27 plans the most Ethernet points (200)") with a subtitle saying what the data is;
  - bars ranked by value and starting at zero, with the goal marked on percentage charts;
  - a written takeaway below each chart, which points to the data table, so the chart itself is decorative to screen readers.
- **Goals** are shown as text ("meets goal" or "goal 90%"), never by colour alone.
- **Formatting:** numbers have thousands separators; events are shown oldest first, so "event over event" reads left to right.
- **Metadata:** the generated time, a source line for every section, and **Sources and definitions** explaining how each measure is calculated.
- **Downloads:** Excel (with an "About" sheet holding the sources, definitions and data notes) and CSV.
- **Sharing:** the selected events are kept in the URL, so a view can be bookmarked or shared.
- **Print** gives a clean report without navigation or controls, with the details expanded.

API: `GET /api/reports[?events=1,2]` and `GET /api/reports/export[?events=…][&format=xlsx|csv]`. Code: `backend/app/reports.py` and `frontend/src/reports/`.

## Comments

Viewers and network admins can comment on an event's requirements. Network admins reply and resolve.

- **What viewers see:** viewers see the **latest published version**, with a banner such as "Showing V3, the latest published version (18 Oct 2026)", so their comments are about what was released. Network admins keep working on the draft. Before anything is published, viewers see the draft with a "Not published yet" banner.
  - Passwords revealed by a viewer are the published ones, and each reveal is logged.
- **Where to comment:**
  - on an item, with the comment button pinned to the right of each row in Wi-Fi networks, Ethernet requirements and Partner orders (it shows the number of open comments);
  - in general, with **Add general comment** on the **Comments** page.
- **Comments page:** the event's left menu, with the number of open comments. Filter by Open, Resolved or All, then Reply, Resolve or Reopen. Only network admins resolve and reopen.
- **Raised on a version:** each comment is marked with the version it was raised on ("On V3"). Open comments stay open across later versions.
- **Kept after changes:** the item's name and place are copied onto the comment, so it still reads correctly after the item is changed or deleted.
- **Not changes:** comments never change the requirements and don't count as unpublished changes. They appear in History under "Comments".
- **Notifications:**
  - a new comment notifies network admins in the bell (browser push if they turn on "New comments");
  - replies and resolutions notify the person who started the thread.
- **Rules:**
  - a name is required (the TEST ONLY name box);
  - up to 2000 characters;
  - archived events can be read but not commented on;
  - templates don't take comments.

API: `GET /api/events/{id}/comments[?state=open|resolved|all&record_id=]`, `POST …/comments {body, record_id?}`, `POST …/comments/{cid}/replies {body}`, `POST …/comments/{cid}/resolve` and `/reopen` (network admins). Code: `backend/app/records/comments.py`, `backend/app/records/published.py` and `frontend/src/event/comments.jsx`.

## Notifications

Every notification lands in the **bell** (top bar and event pages): an unread count, the latest 30 items, and "Mark all as read". Read state is kept per person. Live updates arrive over server-sent events, and if the stream is blocked the bell polls every 30 s. Other channels are added only where they help:

| What happens | In app | Browser push | Slack | Email |
|---|---|---|---|---|
| New ticket | network admins | P1/P2 (on by default), all (opt-in) | P1/P2 | |
| Ticket assigned | the assignee | yes (on by default) | | |
| P1/P2 not acknowledged after 4 min | network admins | yes | | |
| P1/P2 misses the 5-min acknowledge SLA | network admins | yes | yes | `ESCALATION_EMAILS` |
| P3/P4 misses its SLA | network admins | | | |
| ICT request submitted | network admins | opt-in | | |
| Request approved, needs info, or declined | | | | the requester |
| New comment on an event | network admins | opt-in | | |
| Reply to a comment, or comment resolved | the comment's author (a reply by the author goes to network admins) | | | |
| Request link emailed, deadline in 7 days and 1 day | | | | the link's contact |
| New version published | everyone | | | the event's followers |

- **Browser push** uses the Web Push standard with VAPID keys. Permission is asked only when someone clicks **Turn on alerts** in the bell's Settings, never on page load. Each person chooses which alert types they get, and can send a test alert. Urgent alerts stay on screen until dismissed. On iPhone and iPad, push works only after the app is added to the Home Screen (iOS 16.4 or later); the Settings tab says so.
- **Slack** posts to one channel through an incoming webhook, with a button that opens the ticket.
- **Email** goes through Web Summit's existing provider over SMTP with STARTTLS, and carries `Auto-Submitted: auto-generated` so auto-replies are suppressed.
- **Followers:** on an event's Overview, admins add the email addresses to notify when a version is published. The email includes the change summary.
- **Email link:** on the ICT requests page, **Email link…** sends a team its form link and records the contact for deadline reminders.
- **Delivery** is through an outbox. Failures are retried with exponential backoff, up to 8 attempts. Push subscriptions the browser has dropped (404/410) are removed. SLA warnings and reminders are de-duplicated, so each is sent once.

Setup (all in `.env`, see `.env.example`; an empty value turns a channel off):

- `SLACK_WEBHOOK_URL`: create an incoming webhook for the ICT channel in Slack.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`: from the email provider. The From domain needs SPF, DKIM and DMARC, or messages will land in spam.
- `ESCALATION_EMAILS`: comma-separated addresses for SLA breaches.
- `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`: locally a key pair is generated once into `DATA_DIR/vapid.json`. In production set them from the secret store and keep them stable, because changing them invalidates every device's subscription.

API: `GET /api/notifications`, `POST /api/notifications/read`, `GET /api/notifications/stream`, `GET|PUT /api/notifications/settings`, `POST /api/notifications/push/subscribe|unsubscribe`, `POST /api/notifications/test`, and `GET|POST|DELETE /api/events/{id}/watchers`. Code: `backend/app/notifications/`, `frontend/src/ui/Notifications.jsx` and `frontend/public/sw.js`.

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

Shared pieces live in `frontend/src/ui/` (`Dialog.jsx`, `Notice.jsx`) and `frontend/src/event/grid.js`.

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
- **Hardening.**
  - Enforce the upload size limit at the ingress.
  - Run containers with a read-only root filesystem and drop all capabilities.
  - Scan images in CI.
  - Serve a production frontend build, static files from S3 and CloudFront or nginx, instead of the Vite dev server.
- **Help desk.**
  - Owners come from real accounts (SSO groups for the Network team and IT Support) instead of free-text names.
  - Keep `BOT_API_TOKEN` and `BOT_CALLBACK_SECRET` in a secret store and rotate them; serve the bot endpoints over HTTPS only.
  - Run the outbox worker in exactly one replica, or move it to a queue such as SQS, so updates aren't sent twice.
  - Optionally push dashboard updates over server-sent events instead of polling every 10 s.
- **Notifications.**
  - Browser push needs HTTPS (it works on `localhost` only for development).
  - The event stream takes the identity from query parameters, because `EventSource` can't send headers (TEST ONLY). With real authentication, use the session cookie instead.
  - Run the notification worker in exactly one replica, like the help desk outbox.
  - Recipients come from SSO groups instead of the role header.
