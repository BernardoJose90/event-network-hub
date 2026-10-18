#!/usr/bin/env bash
# End-to-end smoke test against a running stack (docker compose up).
#
# For every samples/*.xlsx (directory overridable with SAMPLES_DIR): upload it
# as V1 of a throwaway SMOKE<n> event, download the PDF as viewer and as network-admin,
# then check
#   a) both roles receive the same PDF
#   b) pdffonts lists Overpass and does not list DejaVu
#   c) the PDF page count equals the number of visible sheets
#   d) the Excel download is byte-identical to the uploaded file
#   e) uploading the same version number twice is rejected
#   f) requirements: import the workbook into records, publish the next
#      version (PDF report in Overpass, no DejaVu), and re-importing the
#      published Excel gives the same records and totals
#   g) deleting the document works and removes its files (this also cleans up)
#
# Downloaded PDFs live in a private temp dir that is deleted on exit.
#
# Requires: curl, jq, python3, docker, pdffonts/pdfinfo/pdftotext (poppler-utils).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="${API_URL:-http://localhost:8000/api}"
SAMPLES_DIR="${SAMPLES_DIR:-$ROOT/samples}"
XLSX_TYPE="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

for tool in curl jq python3 docker pdffonts pdfinfo pdftotext; do
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

  # --- upload -------------------------------------------------------------------
  # Uploaded under a unique event code so it never touches real documents.
  upload_name="SMOKE$$${RANDOM} _ Event_Network_Requirements_V1.xlsx"
  upload() {
    curl -sS -o "$1" -w '%{http_code}' -H "X-Role: network-admin" -H "X-User: smoke-test" \
      -F "file=@$sample;type=$XLSX_TYPE;filename=$upload_name" "$API/documents/upload"
  }
  code="$(upload "$work/upload.json")"
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

  # --- e) same version twice ------------------------------------------------------
  code_dup="$(upload "$work/dup.json")"
  if [ "$code_dup" = "409" ]; then
    pass "e) uploading V1 again is rejected"
  else
    fail "e) uploading V1 again returned HTTP $code_dup (expected 409)"
  fi

  # --- f) structured requirements: import -> publish -> re-import round trip ----------
  ev="$API/events/$doc_id"
  admin=(-H "X-Role: network-admin" -H "X-User: smoke-test")
  code_imp="$(curl -sS -o "$work/import.json" -w '%{http_code}' -X POST "${admin[@]}" \
    -H 'Content-Type: application/json' -d '{}' "$ev/import")"
  code_pub="$(curl -sS -o "$work/publish.json" -w '%{http_code}' -X POST "${admin[@]}" "$ev/publish")"
  pub_version="$(jq -r '.version.version // empty' "$work/publish.json" 2>/dev/null)"
  h_ok=no; h_detail="import HTTP $code_imp, publish HTTP $code_pub"
  if [ "$code_imp" = "200" ] && [ "$code_pub" = "201" ] && [ -n "$pub_version" ]; then
    curl -sS -o "$work/report.pdf" "${admin[@]}" "$API/documents/$doc_id/download?version=$pub_version"
    curl -sS -o "$work/published.xlsx" "${admin[@]}" "$API/documents/$doc_id/download?version=$pub_version&format=xlsx"
    pdftotext "$work/report.pdf" "$work/report.txt" 2>/dev/null || true
    pdffonts "$work/report.pdf" > "$work/report.fonts" 2>/dev/null || true
    # Re-import the published Excel into a throwaway event and compare.
    rt_name="SMOKERT$$${RANDOM} _ Event_Network_Requirements_V1.xlsx"
    rt_id="$(curl -sS "${admin[@]}" -F "file=@$work/published.xlsx;type=$XLSX_TYPE;filename=$rt_name" \
      "$API/documents/upload" | jq -r '.document.id // empty')"
    if [ -n "$rt_id" ]; then
      curl -sS -o /dev/null -X POST "${admin[@]}" -H 'Content-Type: application/json' -d '{}' "$API/events/$rt_id/import"
      summary='{counts, totals: [.totals.ethernet[] | {venue, total, sections: [.sections[].ports]}]}'
      a="$(curl -sS "${admin[@]}" "$ev/overview" | jq -c "$summary")"
      b="$(curl -sS "${admin[@]}" "$API/events/$rt_id/overview" | jq -c "$summary")"
      curl -sS -o /dev/null -X POST "${admin[@]}" "$API/documents/$rt_id/archive"
      curl -sS -o /dev/null -X DELETE "${admin[@]}" "$API/documents/$rt_id"
    fi
    records="$(jq -r '[.counts[]] | add // 0' "$work/import.json")"
    if [ "${records:-0}" -gt 0 ] && [ -s "$work/report.txt" ] && grep -qi overpass "$work/report.fonts" \
       && ! grep -qi dejavu "$work/report.fonts" && [ -n "${a:-}" ] && [ "$a" = "${b:-}" ]; then
      h_ok=yes
    fi
    h_detail="$records records, published V$pub_version, report fonts: $(awk 'NR > 2 {sub(/^[A-Z]+\+/, "", $1); print $1}' "$work/report.fonts" | sort -u | tr '\n' ' ')round trip $([ "${a:-}" = "${b:-x}" ] && echo identical || echo DIFFERENT)"
  fi
  if [ "$h_ok" = yes ]; then pass "f) requirements: $h_detail"; else fail "f) requirements: $h_detail"; fi

  # --- g) archive, then delete (also cleans up the smoke document) -------------
  code_early="$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE -H "X-Role: network-admin" \
    "$API/documents/$doc_id")"
  code_arch="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "X-Role: network-admin" \
    "$API/documents/$doc_id/archive")"
  code_del="$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE -H "X-Role: network-admin" \
    "$API/documents/$doc_id")"
  code_after="$(download "$doc_id" network-admin "$work/after.pdf")"
  if [ "$code_early" = "409" ] && [ "$code_arch" = "200" ] && [ "$code_del" = "204" ] && [ "$code_after" = "404" ]; then
    pass "g) delete refused until archived; then deleted and its files are no longer downloadable"
  else
    fail "g) delete before archive HTTP $code_early, archive HTTP $code_arch, delete HTTP $code_del, download afterwards HTTP $code_after (expected 409, 200, 204, 404)"
  fi
done

echo
if [ "$failures" -eq 0 ]; then
  echo "SMOKE TEST: PASS"
else
  echo "SMOKE TEST: FAIL ($failures check(s) failed)"
  exit 1
fi
