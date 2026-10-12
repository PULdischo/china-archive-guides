#!/usr/bin/env bash
# Full pipeline run, with image download and DashScope OCR overlapped:
# OCR passes repeat over whatever images exist until the download has finished and a pass
# finds nothing left to do. Then RapidOCR fallback, export, search records, site build.
# Usage (detached, survives logout):
#   setsid nohup pipeline/scripts/full-run.sh > data/state/full-run.log 2>&1 &
set -u
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
STATE=../data/state
log() { echo "[$(date '+%F %T')] $*"; }

log "harvest"
uv run cah harvest | grep -vE "^  unchanged"

log "images (background) -> $STATE/images.log"
uv run cah images > "$STATE/images.log" 2>&1 &
IMG=$!

pass=0
while true; do
  pass=$((pass + 1))
  log "dashscope pass $pass"
  uv run cah ocr --engine dashscope 2>&1 | tee "$STATE/ocr-pass.log" | grep --line-buffered -E "pages to OCR|00/|done:"
  if ! kill -0 "$IMG" 2>/dev/null && grep -q "dashscope: 0 pages to OCR" "$STATE/ocr-pass.log"; then
    break
  fi
  kill -0 "$IMG" 2>/dev/null && sleep 60
done
wait "$IMG"
log "images: $(tail -1 "$STATE/images.log")"

log "rapidocr fallback (blocked / failed pages)"
uv run cah ocr --engine rapidocr --fallback-only | tail -2
log "export"
uv run cah export | sed -n 3,9p
log "search records"
uv run cah search | tail -1
log "site build"
(cd ../site && npm run build 2>&1 | grep -E "Wrote|Indexed|rror")
log "DONE"
