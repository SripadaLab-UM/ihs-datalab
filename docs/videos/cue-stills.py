"""Review sheet for a walkthrough: one frame just after every spotlight cue.

    python3 cue-stills.py 12-sql-playground   # -> build/<name>/cues.png

Reads the cues from <name>.md and when their phrases are spoken from the
narration, renders each moment (render.mjs --stills), and tiles them with
the cue written under each.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
name = sys.argv[1]
build = HERE / "build" / name
timing = json.loads((build / "timing.json").read_text())
shots = {s["shot"]: s for s in timing["shots"]}


def seconds(stamp):
    h, m, s, ms = map(int, re.split("[:,]", stamp))
    return h * 3600 + m * 60 + s + ms / 1000


captions = []
for block in (build / "captions.srt").read_text(encoding="utf-8").strip().split("\n\n"):
    lines = block.split("\n")
    a, b = map(seconds, lines[1].split(" --> "))
    captions.append((a, b, " ".join(lines[2:])))


def spoken(shot, phrase):
    s = shots[shot]
    for a, b, text in captions:
        if a >= s["start"] - 0.01 and b <= s["end"] + 0.01 and phrase in text:
            return a + text.index(phrase) / len(text) * (b - a)
    raise SystemExit(f'"{phrase}" not spoken in {shot}')


cues, shot = [], None
for line in (HERE / f"{name}.md").read_text(encoding="utf-8").splitlines():
    if m := re.match(r"\*\*Shot (\d+\.\d+)\*\*", line):
        shot = m.group(1)
    elif (m := re.match(r'\s+- `([\w-]+)` · "(.+?)"', line)) and shot:
        cues.append((round(spoken(shot, m.group(2)) + 0.7, 1), f"{shot} {m.group(1)}"))

times = ",".join(str(t) for t, _ in cues)
subprocess.run(["node", "render.mjs", name, "--stills", times], cwd=HERE, check=True, capture_output=True)
inputs, labels = [], []
for i, (t, label) in enumerate(cues):
    inputs += ["-i", str(build / "stills" / f"t{t:06.1f}.png")]
    labels.append(f"[{i}]scale=640:-1,pad=640:390:0:0:white,drawbox=x=0:y=360:w=640:h=30:color=white:t=fill[v{i}]")
# ffmpeg here has no drawtext, so the labels go in a text file beside the sheet.
cols = 4
layout = "|".join(f"{(i % cols) * 640}_{(i // cols) * 390}" for i in range(len(cues)))
graph = ";".join(labels) + ";" + "".join(f"[v{i}]" for i in range(len(cues))) + f"xstack=inputs={len(cues)}:layout={layout}:fill=gray"
subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, str(build / "cues.png")], check=True)
(build / "cues.txt").write_text("\n".join(f"{i + 1:2d}. {t:6.1f}s  {label}" for i, (t, label) in enumerate(cues)) + "\n")
print(build / "cues.png")
print((build / "cues.txt").read_text())
