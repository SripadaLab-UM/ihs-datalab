#!/bin/sh
# Makes the files to publish, in build/publish/, from the rendered videos:
# each video re-encoded smaller for the web with its narration levelled to
# -16 LUFS, its captions as WebVTT, and a thumbnail (the title and agenda,
# just before the first shot starts).
#
#   sh docs/videos/publish.sh
set -e
cd "$(dirname "$0")"
OUT=build/publish
mkdir -p "$OUT"
# On Windows (Git Bash), python3 is a Microsoft Store placeholder.
PY=python3; "$PY" -c "" 2>/dev/null || PY=python
for v in 09-getting-started 09-getting-started-windows 01-how-datalab-works 02-what-datalab-can-do 10-workspace-ask 11-workspace-answer \
         12-sql-playground 13-workflows 15-help 16-research-helper 17-connections; do
  src="build/$v/$v.mp4"
  [ -f "$src" ] || { echo "missing $src"; exit 1; }
  ffmpeg -v error -y -i "$src" \
    -c:v libx264 -preset slow -crf 28 -pix_fmt yuv420p \
    -af loudnorm=I=-16:TP=-1.5:LRA=11 -ar 48000 -c:a aac -b:a 128k \
    -movflags +faststart "$OUT/$v.mp4"
  ffmpeg -v error -y -i "build/$v/captions.srt" "$OUT/$v.vtt"
  # The thumbnail: just after the opening's narration, agenda complete, no caption.
  at=$("$PY" -c "import json;s=json.load(open('build/$v/timing.json'))['shots'];print(s[0]['end']+0.18)")
  ffmpeg -v error -y -ss "$at" -i "$src" -frames:v 1 -vf scale=1280:-1 "$OUT/$v.jpg"
  echo "$v  $(du -h "$src" | cut -f1) -> $(du -h "$OUT/$v.mp4" | cut -f1)"
done
