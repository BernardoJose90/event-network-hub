#!/usr/bin/env bash
# End-to-end smoke test against a running stack (docker compose up).
#
# For every samples/*.xlsx (directory overridable with SAMPLES_DIR): create a
# document, upload the file, download the redacted and full PDFs, then check
#   a) the redacted PDF contains none of the password-column values
#   b) pdffonts lists Overpass and does not list DejaVu
#   c) the PDF page count equals the number of visible sheets
#
# Sample files contain real credentials. This script never prints them:
# matches are shown masked, and all intermediate files live in a private
# temp dir that is deleted on exit.
#
# Requires: curl, jq, docker, pdftotext/pdffonts/pdfinfo (poppler-utils).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="${API_URL:-http://localhost:8000/api}"
SAMPLES_DIR="${SAMPLES_DIR:-$ROOT/samples}"
XLSX_TYPE="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

for tool in curl jq docker pdftotext pdffonts pdfinfo; do
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
count() { grep -oF -- "$1" "$2" | wc -l | tr -d ' ' || true; }  # occurrences of $1 in file $2
mask() {  # reveal at most 2 leading chars, and only for long values
  local v="$1"
  if [ ${#v} -ge 8 ]; then printf '%s***(%d chars)' "${v:0:2}" "${#v}"; else printf '***(%d chars)' "${#v}"; fi
}

download() { # download <doc_id> <role> <out>
  curl -sS -o "$3" -w '%{http_code}' -H "X-Role: $2" -H "X-User: smoke-test" \
    "$API/documents/$1/download"
}

for sample in "${samples[@]}"; do
  base="$(basename "$sample")"
  echo "== $base"
  work="$TMP/${base%.xlsx}"
  mkdir -p "$work"

  # --- create document + upload ----------------------------------------------
  name="smoke ${base%.xlsx} $(date -u +%Y%m%dT%H%M%SZ)-$$"
  doc_id="$(curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "$(jq -n --arg n "$name" '{name: $n}')" "$API/documents" | jq -r '.id')"

  code="$(curl -sS -o "$work/upload.json" -w '%{http_code}' -H "X-User: smoke-test" \
    -F "file=@$sample;type=$XLSX_TYPE" "$API/documents/$doc_id/versions")"
  if [ "$code" != "201" ]; then
    fail "upload (HTTP $code): $(jq -r '.message // .detail // "unknown error"' "$work/upload.json" 2>/dev/null)"
    continue
  fi
  version="$(jq -r '.version.version' "$work/upload.json")"
  warnings="$(jq -r '.version.warnings | length' "$work/upload.json")"
  echo "  uploaded as document $doc_id version $version ($warnings formula warning(s))"

  code_r="$(download "$doc_id" viewer "$work/redacted.pdf")"
  code_f="$(download "$doc_id" network-admin "$work/full.pdf")"
  if [ "$code_r" != "200" ] || [ "$code_f" != "200" ]; then
    fail "download (viewer HTTP $code_r, network-admin HTTP $code_f)"
    continue
  fi

  # Facts from the workbook itself (computed in the backend container, which has openpyxl).
  docker compose -f "$ROOT/docker-compose.yml" exec -T backend \
    python -m app.inspect_xlsx < "$sample" > "$work/facts.json"
  jq -r '.sensitive_values[]' "$work/facts.json" > "$work/values.txt"
  sheets="$(jq -r '.visible_sheets' "$work/facts.json")"
  nvalues="$(grep -c . "$work/values.txt" || true)"

  pdftotext -layout "$work/redacted.pdf" "$work/redacted.txt"
  pdftotext -layout "$work/full.pdf" "$work/full.txt"

  # --- a) redaction ------------------------------------------------------------
  # Text wider than its column is clipped in the PDF exactly as in Excel, so a
  # value may only appear as a prefix. For each value we find its visible form
  # in the full PDF (longest prefix of >= 3 chars) and require it to occur
  # strictly fewer times in the redacted PDF; comparing counts cancels out
  # coincidental matches elsewhere on the sheet.
  if [ "$nvalues" -eq 0 ]; then
    fail "a) no 'Password Value' column values found in the workbook, so redaction cannot be verified"
  else
    full_hits="$(grep -cF -f "$work/values.txt" "$work/full.txt" || true)"
    leaked=(); verified=0; unverifiable=0
    while IFS= read -r v; do
      visible=""
      for ((len = ${#v}; len >= 3; len--)); do
        if grep -qF -- "${v:0:len}" "$work/full.txt"; then visible="${v:0:len}"; break; fi
      done
      if [ -z "$visible" ]; then unverifiable=$((unverifiable + 1)); continue; fi
      n_full="$(count "$visible" "$work/full.txt")"
      n_red="$(count "$visible" "$work/redacted.txt")"
      if [ "$n_red" -ge "$n_full" ]; then leaked+=("$v"); else verified=$((verified + 1)); fi
    done < "$work/values.txt"

    if [ ${#leaked[@]} -gt 0 ]; then
      fail "a) redacted PDF still shows ${#leaked[@]} password value(s):"
      for v in "${leaked[@]}"; do printf '          %s\n' "$(mask "$v")"; done
    elif [ "$verified" -eq 0 ]; then
      fail "a) none of the $nvalues password value(s) are visible in the full PDF, so redaction cannot be verified"
    else
      pass "a) redacted PDF shows none of the $verified password value(s) visible in the full PDF"
    fi
    if [ "$unverifiable" -gt 0 ]; then
      echo "        note: $unverifiable value(s) not visible in the full PDF either (hidden, or under 3 chars)"
    fi
    if [ "$full_hits" -eq 0 ]; then
      echo "        note: no value appears untruncated in the full PDF (column too narrow); matched visible prefixes"
    fi
    if grep -qF -f "$work/values.txt" "$work/redacted.txt"; then
      fail "a) a complete password value appears verbatim in the redacted PDF"
    fi
  fi

  # --- b) fonts ----------------------------------------------------------------
  for kind in full redacted; do
    pdffonts "$work/$kind.pdf" > "$work/$kind.fonts"
    has_overpass=no; has_dejavu=no
    grep -qi 'overpass' "$work/$kind.fonts" && has_overpass=yes
    grep -qi 'dejavu' "$work/$kind.fonts" && has_dejavu=yes
    if [ "$has_overpass" = yes ] && [ "$has_dejavu" = no ]; then
      pass "b) $kind.pdf embeds Overpass and no DejaVu"
    else
      fail "b) $kind.pdf: Overpass=$has_overpass DejaVu=$has_dejavu. Fonts:"
      awk 'NR > 2 {print "          " $1}' "$work/$kind.fonts" | sort -u
    fi
  done

  # --- c) page count -----------------------------------------------------------
  for kind in full redacted; do
    pages="$(pdfinfo "$work/$kind.pdf" | awk '/^Pages:/ {print $2}')"
    if [ "$pages" = "$sheets" ]; then
      pass "c) $kind.pdf has $pages page(s) = $sheets visible sheet(s)"
    else
      fail "c) $kind.pdf has $pages page(s) but the workbook has $sheets visible sheet(s)"
    fi
  done
done

echo
if [ "$failures" -eq 0 ]; then
  echo "SMOKE TEST: PASS"
else
  echo "SMOKE TEST: FAIL ($failures check(s) failed)"
  exit 1
fi
