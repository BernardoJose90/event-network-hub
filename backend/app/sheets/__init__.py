"""The "live sheet": an editable, in-app copy of a document's workbook.

model     -- the JSON shape stored in the database and edited by the web grid
importer  -- .xlsx -> model (first open, or "start again from Vn")
engine    -- calculated cells (section totals, grand totals, row formulas)
exporter  -- model -> .xlsx (downloads; the PDF is converted from it)
"""
