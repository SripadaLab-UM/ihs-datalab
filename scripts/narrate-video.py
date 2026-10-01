"""Narrate a video script in docs/videos/ with Amazon Polly (generative engine).

The narration is read from the script itself: every "> " line under a
"**Shot X.Y**" heading. Each shot is synthesized separately, so the timing
file gives each shot's exact start and end for the animation to follow.

    python3 scripts/narrate-video.py docs/videos/01-how-datalab-works.md --audition
    python3 scripts/narrate-video.py docs/videos/01-how-datalab-works.md --voice Ruth

Output goes to docs/videos/build/<script name>/ (ignored by git): one MP3 per
shot, narration.wav, timing.json, and captions.srt. Unchanged shots aren't
re-synthesized. Uses the AWS CLI's configured credentials; nothing here
reads or prints them. Needs ffmpeg to decode the MP3s.

Polly receives only the narration text, which must never contain study data.
"""

import argparse
import array
import hashlib
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

RATE = 24000
USD_PER_CHARACTER = 30 / 1_000_000  # generative engine, before credits and tax
LEAD_IN, SHOT_GAP, CHAPTER_GAP, TAIL = 0.8, 0.5, 1.2, 1.0
CAPTION_MAX = 90
AUDITION_SHOTS = ("1.1", "2.5")
AUDITION_VOICES = ("Matthew", "Stephen", "Ruth", "Danielle", "Joanna")


def read_shots(script: Path) -> list[dict]:
    shots, chapter = [], 0
    for line in script.read_text(encoding="utf-8").splitlines():
        if heading := re.match(r"### Chapter (\d+)", line):
            chapter = int(heading.group(1))
        elif shot := re.match(r"\*\*Shot (\d+\.\d+)\*\*", line):
            shots.append({"shot": shot.group(1), "chapter": chapter, "lines": []})
        elif shots and (spoken := re.match(r"\s*> (.*)", line)):
            shots[-1]["lines"].append(spoken.group(1).strip())
    for shot in shots:
        shot["text"] = " ".join(shot.pop("lines"))
    return [shot for shot in shots if shot["text"]]


def synthesize(text: str, voice: str, dest: Path, profile: str, region: str) -> int:
    """Write text as speech to dest (MP3). Returns the characters billed (0 if cached)."""
    request = {"engine": "generative", "voice": voice, "region": region, "text": text}
    record = dest.with_suffix(".request.json")
    if dest.exists() and record.exists() and json.loads(record.read_text(encoding="utf-8")) == request:
        return 0
    result = subprocess.run(
        ["aws", "--profile", profile, "--region", region, "--no-cli-pager", "--output", "json",
         "polly", "synthesize-speech", "--engine", "generative", "--language-code", "en-US",
         "--voice-id", voice, "--output-format", "mp3", "--sample-rate", str(RATE),
         "--text-type", "text", "--text", text, str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode:
        error = re.search(r"\(([^)]+)\)", result.stderr)
        sys.exit("Polly failed: " + (error.group(1) if error else "connection or configuration error"))
    record.write_text(json.dumps(request, indent=2), encoding="utf-8")
    return json.loads(result.stdout).get("RequestCharacters", len(text))


def decode(mp3: Path) -> array.array:
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp3), "-ac", "1", "-ar", str(RATE),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    samples = array.array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def captions(text: str) -> list[str]:
    """Sentences, with long ones split at the punctuation nearest their middle."""
    pieces = re.split(r"(?<=[.?!])\s+", text)
    out = []
    while pieces:
        piece = pieces.pop(0)
        cuts = [m.end() for m in re.finditer(r"[,:;]\s", piece)]
        if len(piece) <= CAPTION_MAX or not cuts:
            out.append(piece)
            continue
        cut = min(cuts, key=lambda c: abs(c - len(piece) / 2))
        pieces[:0] = [piece[:cut].strip(), piece[cut:].strip()]
    return out


def split_times(samples: array.array, texts: list[str]) -> list[tuple[float, float]]:
    """Place caption breaks at the quietest moment near where the text says they fall."""
    frame = RATE // 50  # 20 ms
    energy = [sum(abs(s) for s in samples[i:i + frame]) for i in range(0, len(samples), frame)]
    duration = len(samples) / RATE
    total = sum(len(t) for t in texts)
    breaks, done = [], 0
    for text in texts[:-1]:
        done += len(text)
        expected = int(done / total * len(energy))
        window = range(max(1, expected - 30), min(len(energy) - 6, expected + 30))
        best = min(window, key=lambda i: sum(energy[i:i + 6]), default=expected)
        breaks.append((best + 3) * frame / RATE)
    edges = [0.0, *breaks, duration]
    return list(zip(edges, edges[1:]))


def stamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("script", type=Path)
    parser.add_argument("--voice", default="Matthew")
    parser.add_argument("--audition", action="store_true", help="one short sample per candidate voice")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    shots = read_shots(args.script)
    out = args.script.parent / "build" / args.script.stem
    billed = 0

    if args.audition:
        (out / "auditions").mkdir(parents=True, exist_ok=True)
        sample = " ".join(s["text"] for s in shots if s["shot"] in AUDITION_SHOTS)
        for voice in AUDITION_VOICES:
            billed += synthesize(sample, voice, out / "auditions" / f"{voice.lower()}.mp3",
                                 args.profile, args.region)
            print(f"{voice}: auditions/{voice.lower()}.mp3", flush=True)
    else:
        (out / "shots").mkdir(parents=True, exist_ok=True)
        track = array.array("h", [0] * int(LEAD_IN * RATE))
        timing, subtitles, chapter = [], [], shots[0]["chapter"]
        for shot in shots:
            if shot["chapter"] != chapter:
                track.extend([0] * int((CHAPTER_GAP - SHOT_GAP) * RATE))
                chapter = shot["chapter"]
            mp3 = out / "shots" / f"shot-{shot['shot']}-{args.voice.lower()}.mp3"
            billed += synthesize(shot["text"], args.voice, mp3, args.profile, args.region)
            speech = decode(mp3)
            start = len(track) / RATE
            texts = captions(shot["text"])
            for text, (a, b) in zip(texts, split_times(speech, texts)):
                subtitles.append((start + a, start + b, text))
            track.extend(speech)
            timing.append({"shot": shot["shot"], "chapter": shot["chapter"], "start": round(start, 3),
                           "end": round(len(track) / RATE, 3), "text": shot["text"]})
            print(f"Shot {shot['shot']}: {start:6.2f}s  {len(speech) / RATE:5.2f}s", flush=True)
            track.extend([0] * int(SHOT_GAP * RATE))
        track.extend([0] * int((TAIL - SHOT_GAP) * RATE))
        with wave.Wave_write(str(out / "narration.wav")) as wav:
            wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            if sys.byteorder != "little":
                track.byteswap()
            wav.writeframes(track.tobytes())
        duration = len(track) / RATE
        (out / "timing.json").write_text(json.dumps({
            "script": args.script.name,
            "script_sha256": hashlib.sha256(args.script.read_bytes()).hexdigest(),
            "voice": args.voice, "engine": "generative", "duration": round(duration, 3),
            "caption_timing": "shot boundaries exact; breaks within a shot placed at pauses",
            "shots": timing,
        }, indent=2), encoding="utf-8")
        (out / "captions.srt").write_bytes(("\n\n".join(
            f"{i}\n{stamp(a)} --> {stamp(b)}\n{text}" for i, (a, b, text) in enumerate(subtitles, 1)) + "\n").encode())
        print(f"Total {duration // 60:.0f}:{duration % 60:04.1f}  ->  {out}/narration.wav")

    print(f"New characters billed: {billed} (about ${billed * USD_PER_CHARACTER:.2f})")


if __name__ == "__main__":
    main()
