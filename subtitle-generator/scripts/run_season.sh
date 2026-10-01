#!/usr/bin/env bash
# Generate subtitles (transcript, then English via subtitle-translate) for EVERY video in a folder, one episode at a time.
# Run this on your own computer (no runtime caps), e.g.:
#   bash run_season.sh "/path/to/Power Stone/Season 01"
#
# Requirements: python3, ffmpeg, and: pip install faster-whisper
# Optional: set MODEL=medium for higher quality (slower).

set -euo pipefail
DIR="${1:?Usage: bash run_season.sh /path/to/folder [model]}"
MODEL="${2:-${MODEL:-small}}"
HERE="$(cd "$(dirname "$0")" && pwd)"

shopt -s nullglob
vids=("$DIR"/*.mp4 "$DIR"/*.mkv "$DIR"/*.avi)
echo "Found ${#vids[@]} videos in: $DIR   (model: $MODEL)"

i=0
failed=0
for v in "${vids[@]}"; do
  i=$((i+1))
  base="${v%.*}"
  if [[ -s "$base.en.srt" ]]; then
    echo "[$i/${#vids[@]}] SKIP (has subs): $(basename "$v")"
    continue
  fi
  echo "[$i/${#vids[@]}] $(basename "$v")"
  if ! python3 "$HERE/gen_subs.py" "$v" --model "$MODEL"; then
    echo "FAILED: $(basename "$v") (the others continue; run this again for it)"
    failed=$((failed+1))
  fi
done
if [[ $failed -gt 0 ]]; then
  echo "Done with $failed failed."
  exit 1
fi
echo "All done."
