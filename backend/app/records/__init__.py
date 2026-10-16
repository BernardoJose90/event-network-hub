"""Structured network requirements for an event (the app as the master copy).

models   -- record types and validation
store    -- SQLite tables and queries
service  -- rules: list checks, revisions, change log, totals
importer -- workbook (.xlsx) -> records (migration of existing events)
publish  -- records -> Excel in the workbook layout + PDF report, as a version
routes   -- /api/events/{document_id}/...
"""
