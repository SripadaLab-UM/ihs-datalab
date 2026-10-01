# Videos

Short videos for new users, made from scripts in this folder. They show
only the practice profile (synthetic data), because the narration is sent to
Amazon Polly and the videos are public.

| Video | Script and storyboard |
|---|---|
| 1. How DataLab works | [01-how-datalab-works.md](01-how-datalab-works.md) |
| 2. What DataLab can do | [02-what-datalab-can-do.md](02-what-datalab-can-do.md) |
| Getting started on a Mac | [09-getting-started.md](09-getting-started.md) |
| Getting started on Windows | [09-getting-started-windows.md](09-getting-started-windows.md) |
| Walkthroughs: Workspace (2), SQL Playground, Workflows, Help, the agent and the web, getting connected | `10-` to `17-*.md` |

## How a video is made

1. **Script.** `NN-name.md` holds the narration (the `> ` lines under each
   shot) and the storyboard. It is the only place the words live.
2. **Narration.** Amazon Polly (generative engine) reads each shot:

   ```bash
   python3 scripts/narrate-video.py docs/videos/01-how-datalab-works.md --voice Ruth
   ```

   This writes `build/NN-name/`: one MP3 per shot, `narration.wav`,
   `timing.json`, and `captions.srt`. Unchanged shots aren't re-synthesized.
   It uses the AWS CLI's configured profile (`default`, region `us-east-1`).
3. **Animation.** `animation/NN-name.html` draws any moment of the video from
   the time alone, keyed to when each phrase is spoken. To watch it with the
   narration, serve the repo root and open the page with `?preview`
   (add `&t=60` to start at 60 seconds):

   ```bash
   python3 -m http.server 8123 --bind 127.0.0.1
   ```

   then http://127.0.0.1:8123/docs/videos/animation/01-how-datalab-works.html?preview
4. **Render.** In this folder, after `npm install` once:

   ```bash
   node render.mjs 01-how-datalab-works
   ```

   It drives the installed Google Chrome frame by frame and encodes with
   `ffmpeg` (`brew install ffmpeg`), writing `build/NN-name/NN-name.mp4`.
   `--stills 10,60` saves single frames instead; `--from`/`--to` render a
   section. `--audit` checks the timing without rendering: never two
   footage windows at once, and never a window before the block it points
   at. Run it after any timing change.

`build/` is ignored by git: everything in it can be made again from the
script and the animation page.

## Filming the app

The footage windows are filmed on a practice DataLab of their own (port
8790, its own data folder), so a practice DataLab you're using is never
touched. Start it once, with the current UI built:

```bash
cd frontend && npx vite build --outDir /tmp/datalab-film-ui
D="$HOME/Library/Application Support/DataLab/practice-video"; mkdir -p "$D"
cd backend && DATALAB_PROFILE=practice DATALAB_DATA_DIR="$D" uv run datalab --profile practice catalog --from-database --out "$D/catalog"
printf 'port = 8790\ncatalog_dir = "%s"\n' "$D/catalog" > "$D/settings.toml"
DATALAB_PROFILE=practice DATALAB_DATA_DIR="$D" DATALAB_WEB_DIST=/tmp/datalab-film-ui uv run datalab --profile practice serve --no-browser > "$D/server.log" 2>&1
```

Then the takes (each writes `build/footage/<shot>-<name>.mp4`):

| Script | Shots | What it does |
|---|---|---|
| `footage/take-conversation.mjs` | 2.1, 2.6 | A real Analysis conversation: asks the question, edits and approves the plan, waits for the answer and review. `--plan-only` re-films just the plan in a fresh conversation. |
| `footage/take-helper.mjs` | 2.8 | A short conversation that asks the research helper; films the approval card. |
| `footage/take-finished.mjs` | 3.1–3.6 | History, the answer and its number check, rigor review, SQL and Queries, Export, and the Safety check, on the finished conversation. Name shots to re-take only those. |

The live take sends synthetic data to U-M GPT with the saved key, as any
practice conversation does; it takes a few minutes.

**Names are blurred.** The synthetic schema reuses the real table and column
names, which are internal. While filming, a layer over the page blurs every
catalog table and column name, the database's role names, and paths in your
home folder (form fields that contain one are covered whole). It doesn't
change the app's page. See `MASK` in `footage/app.mjs`.

## Still to do for video 1

- Composite the recorded takes into the footage windows (crop each take to
  its region, fade it in and out with its window).
- The lab's review of the wording (open items at the end of the script).

## Getting started (desktop footage)

`09-getting-started` mixes website takes (filmed like the walkthroughs, from
`walkthroughs/09-getting-started.mjs`, whose `site` names the public page)
with desktop footage recorded on a Mac by `footage/record-mac-setup.sh`:
Finder unzipping the download, Terminal running the real installer, and the
Desktop shortcut. Its header says what's staged. Those takes are listed in
`build/09-getting-started/takes.json` by hand (file, seconds, and the icon's
spotlight rect for the Desktop shot). The key prompt is a drawn illustration
(`- typed · "…"` lines in the script, shown in `animation/walkthrough.html`'s
terminal view, which also draws `- window · "…"` lines and replays
`terminal.json` from `terminal-rec.py`). Keys are never typed on camera.

### On Windows

`09-getting-started-windows` is made the same way on a Windows PC (Git Bash
for `publish.sh`; `python` for `python3`). Its desktop footage is recorded by
`footage/record-windows-setup.ps1` (File Explorer's Extract All, PowerShell
running the real installer a second time, so it asks nothing secret, and the
Start menu), then cut, cropped and blurred by `footage/cut-windows-setup.ps1`,
which adds those takes to `takes.json`; each script's header says what's
staged and what's blurred. The recorder also writes `terminal.json` (the
installer's output, user name shown as "you") for the drawn PowerShell in
shots 3.5 and 4.1. Shot 5.3 is DataLab's first screen on the real profile of
the PC just set up (no conversations, no VPN), filmed with the website shots
by `film-walkthrough.mjs` (`app: true`; see the walkthrough for the
`FILM_*` settings it needs).

## Publishing

`sh docs/videos/publish.sh` writes `build/publish/`: each video re-encoded for
the web (narration levelled to -16 LUFS), its captions as `.vtt`, and a
`.jpg` thumbnail. `python3 docs/videos/cue-stills.py <name>` makes a review
sheet of every spotlight cue in a walkthrough (`build/<name>/cues.png`).
