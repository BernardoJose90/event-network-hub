#!/usr/bin/env bash
# End-to-end smoke test against a running stack (docker compose up).
#
# For every samples/*.xlsx (directory overridable with SAMPLES_DIR): create a
# document from the file, download the PDF as viewer and as network-admin,
# then check
#   a) both roles receive the same PDF
#   b) pdffonts lists Overpass and does not list DejaVu
#   c) the PDF page count equals the number of visible sheets
#   d) the Excel download is byte-identical to the uploaded file
#   e) deleting the document works and removes its files (this also cleans up)
#
# Downloaded PDFs live in a private temp dir that is deleted on exit.
#
# Requires: curl, jq, docker, pdffonts/pdfinfo (poppler-utils).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="${API_URL:-http://localhost:8000/api}"
SAMPLES_DIR="${SAMPLES_DIR:-$ROOT/samples}"
XLSX_TYPE="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

for tool in curl jq docker pdffonts pdfinfo; do
  command -v "$tool" >/dev/null || { echo "ERROR: '$tool' is required but not installed" >&2; exit 2; }
done

shopt -s nullglob
samples=("$SAMPLES_DIR"/*.xlsx)
if [ ${#samples[@]} -eq 0 ]; then
  echo "ERROR: no .xlsx files in $SAMPLES_DIR. Copy your sample workbook into $ROOT/samples/" >&2
  exit 2
fi

if ! curl -fsS "$API/health" >/dev/null; then
  echo "ERROR: backend not reachable at $API. Start the stack with: docker compose up --build" >&2
  exit 2
fi

umask 077
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

failures=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; failures=$((failures + 1)); }

download() { # download <doc_id> <role> <out> [format]
  curl -sS -o "$3" -w '%{http_code}' -H "X-Role: $2" -H "X-User: smoke-test" \
    "$API/documents/$1/download?format=${4:-pdf}"
}

for sample in "${samples[@]}"; do
  base="$(basename "$sample")"
  echo "== $base"
  work="$TMP/${base%.xlsx}"
  mkdir -p "$work"

  # --- upload as a new document ---------------------------------------------
  # A unique name keeps repeated runs from colliding with earlier documents.
  name="smoke ${base%.xlsx} $(date -u +%Y%m%dT%H%M%SZ)-$$"
  code="$(curl -sS -o "$work/upload.json" -w '%{http_code}' \
    -H "X-Role: network-admin" -H "X-User: smoke-test" \
    -F "file=@$sample;type=$XLSX_TYPE" -F "name=$name" "$API/documents/upload")"
  if [ "$code" != "201" ]; then
    fail "upload (HTTP $code): $(jq -r '.message // .detail // "unknown error"' "$work/upload.json" 2>/dev/null)"
    continue
  fi
  doc_id="$(jq -r '.document.id' "$work/upload.json")"
  version="$(jq -r '.version.version' "$work/upload.json")"
  warnings="$(jq -r '.version.warnings | length' "$work/upload.json")"
  echo "  uploaded as document $doc_id version $version ($warnings formula warning(s))"

  code_v="$(download "$doc_id" viewer "$work/viewer.pdf")"
  code_a="$(download "$doc_id" network-admin "$work/admin.pdf")"
  if [ "$code_v" != "200" ] || [ "$code_a" != "200" ]; then
    fail "download (viewer HTTP $code_v, network-admin HTTP $code_a)"
    continue
  fi

  # Visible sheet count from the workbook itself (computed in the backend container, which has openpyxl).
  sheets="$(docker compose -f "$ROOT/docker-compose.yml" exec -T backend \
    python -m app.inspect_xlsx < "$sample" | jq -r '.visible_sheets')"

  # --- a) same PDF for every role ---------------------------------------------
  if cmp -s "$work/viewer.pdf" "$work/admin.pdf"; then
    pass "a) viewer and network-admin receive the same PDF"
  else
    fail "a) viewer and network-admin received different PDFs"
  fi

  # --- b) fonts ----------------------------------------------------------------
  pdffonts "$work/admin.pdf" > "$work/fonts.txt"
  has_overpass=no; has_dejavu=no
  grep -qi 'overpass' "$work/fonts.txt" && has_overpass=yes
  grep -qi 'dejavu' "$work/fonts.txt" && has_dejavu=yes
  if [ "$has_overpass" = yes ] && [ "$has_dejavu" = no ]; then
    pass "b) PDF embeds Overpass and no DejaVu"
  else
    fail "b) Overpass=$has_overpass DejaVu=$has_dejavu. Fonts:"
    awk 'NR > 2 {print "          " $1}' "$work/fonts.txt" | sort -u
  fi

  # --- c) page count -----------------------------------------------------------
  pages="$(pdfinfo "$work/admin.pdf" | awk '/^Pages:/ {print $2}')"
  if [ "$pages" = "$sheets" ]; then
    pass "c) PDF has $pages page(s) = $sheets visible sheet(s)"
  else
    fail "c) PDF has $pages page(s) but the workbook has $sheets visible sheet(s)"
  fi

  # --- d) original Excel download ------------------------------------------------
  code_x="$(download "$doc_id" viewer "$work/download.xlsx" xlsx)"
  if [ "$code_x" = "200" ] && cmp -s "$sample" "$work/download.xlsx"; then
    pass "d) Excel download is identical to the uploaded file"
  else
    fail "d) Excel download (HTTP $code_x) differs from the uploaded file"
  fi

  # --- e) delete (also cleans up the smoke document) ---------------------------
  code_del="$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE -H "X-Role: network-admin" \
    "$API/documents/$doc_id")"
  code_after="$(download "$doc_id" network-admin "$work/after.pdf")"
  if [ "$code_del" = "204" ] && [ "$code_after" = "404" ]; then
    pass "e) document deleted; its files are no longer downloadable"
  else
    fail "e) delete returned HTTP $code_del, download afterwards HTTP $code_after (expected 204, 404)"
  fi
done

echo
if [ "$failures" -eq 0 ]; then
  echo "SMOKE TEST: PASS"
else
  echo "SMOKE TEST: FAIL ($failures check(s) failed)"
  exit 1
fi
