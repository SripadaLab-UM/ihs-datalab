"""Turns macOS `script -r` recordings into a terminal timeline for a video.

    python3 terminal-rec.py <name> <recording> [<recording> ...]

Writes build/<name>/terminal.json: {"events": [[seconds, text], ...]}, the
output of each recording in order (later recordings start where the last
one ended, less a second), with the terminal's control codes reduced to
text, bold (\x01 … \x02) and line breaks. Only output is kept: typed input
isn't recorded here, and nothing typed is ever shown.
"""

import json
import re
import struct
import sys
from pathlib import Path

name, *recordings = sys.argv[1:]
events, offset = [], 0.0
for rec in recordings:
    data = Path(rec).read_bytes()
    i, start, last = 0, None, 0.0
    while i + 24 <= len(data):
        length, sec, usec, direction = struct.unpack("<QQII", data[i:i + 24])
        payload = data[i + 24:i + 24 + length]
        i += 24 + length
        t = sec + usec / 1e6
        start = t if start is None else start
        if chr(direction) != "o" or not payload:
            continue
        text = payload.decode("utf-8", "replace")
        text = text.replace("\x1b[1m", "\x01").replace("\x1b[0m", "\x02")
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).replace("\r\n", "\n")
        last = t - start
        events.append([round(offset + last, 3), text])
    offset += last + 1.0
out = Path(__file__).parent / "build" / name / "terminal.json"
out.write_text(json.dumps({"events": events}, indent=1))
print(out, len(events), "events,", round(offset, 1), "s")
