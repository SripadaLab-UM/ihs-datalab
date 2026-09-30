#!/bin/sh
# Records the desktop footage for 09-getting-started on a Mac: Finder
# unzipping the download, Terminal running the real installer, and the
# DataLab shortcut on the Desktop. Writes build/09-getting-started/takes/
# finder.mp4, term-paste.mp4, term-steps.mp4, term-done.mp4, desktop.mp4.
#
#   sh docs/videos/footage/record-mac-setup.sh
#
# Needs: ffmpeg, Screen Recording permission for the app running this (System
# Settings > Privacy & Security), Do Not Disturb on, DataLab already set up
# on this Mac with its keys saved (so the installer asks nothing secret), and
# the release ZIP from https://datalab.cap-study.com/ in ~/Downloads/DataLab-Mac.zip
# unzipped to ~/Downloads/DataLab-Mac. Don't touch the Mac while it runs
# (about two minutes): it moves windows.
#
# What's staged: Finder shows a folder named Downloads holding only the ZIP,
# so no one's files are filmed; Terminal's prompt is a plain "~ %", and the
# installer's output passes through a filter that shows the user's name in
# paths as "you" (the typed command is unchanged). With its output piped, the
# installer skips its two last questions (Show in Finder, Open now).
#
# The frames for each shot are cut from these recordings at times read off a
# contact sheet; re-check the cut points (the cut() lines) after a new run.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../build/09-getting-started/takes"
WORK="$(mktemp -d)"
mkdir -p "$OUT" "$WORK/stage/Downloads" "$WORK/zd"
cp "$HOME/Downloads/DataLab-Mac.zip" "$WORK/stage/Downloads/"

# The screen in points, as ffmpeg's avfoundation captures it (1920x1200 here).
record() { # seconds crop out
  ffmpeg -v error -y -f avfoundation -capture_cursor 0 -framerate 30 -pixel_format nv12 -i "1:none" \
    -t "$1" -vf "crop=$2,scale=1920:-2:flags=lanczos" -c:v libx264 -crf 16 -pix_fmt yuv420p -g 10 "$3"
}

# 1. Finder: list view, 1200x675 points at (160,120).
osascript <<EOF
tell application "Finder"
  activate
  set w to make new Finder window to (POSIX file "$WORK/stage/Downloads" as alias)
  set current view of w to list view
  set toolbar visible of w to true
  set pathbar visible of w to false
  set statusbar visible of w to false
  set bounds of w to {160, 120, 1360, 795}
  set text size of list view options of w to 16
  set icon size of list view options of w to large icon
  set selection to {}
end tell
EOF
sleep 1.2
record 9 1200:675:160:120 "$WORK/finder.mp4" & sleep 2.2
osascript -e "tell application \"Finder\" to select (POSIX file \"$WORK/stage/Downloads/DataLab-Mac.zip\" as alias)" >/dev/null
sleep 1.3
open "$WORK/stage/Downloads/DataLab-Mac.zip" # what a double-click does
sleep 1.6
osascript -e 'tell application "Finder" to activate' -e "tell application \"Finder\" to select (POSIX file \"$WORK/stage/Downloads/DataLab-Mac\" as alias)" >/dev/null
wait
osascript -e 'tell application "Finder" to close front window' >/dev/null

# 2. Terminal: a window with a plain prompt, and the name filter on sh.
cat > "$WORK/zd/redact.py" <<'EOF'
import os
old = ("/Users/" + os.environ["USER"]).encode(); new = b"/Users/you"; buf = b""
while (c := os.read(0, 1)):
    buf += c
    if old.startswith(buf):
        if buf == old: os.write(1, new); buf = b""
        continue
    out = buf.replace(old, new)
    keep = max((k for k in range(1, min(len(old), len(out)) + 1) if old.startswith(out[-k:])), default=0)
    os.write(1, out[:len(out) - keep]); buf = out[len(out) - keep:]
os.write(1, buf)
EOF
printf 'PROMPT="~ %%%% "\nunsetopt PROMPT_SP\nexport PATH="%s"\nsh() { command sh "$@" 2>&1 | python3 -u "%s/zd/redact.py"; }\nclear\n' "$PATH" "$WORK" > "$WORK/zd/.zshrc"
osascript <<EOF
tell application "Terminal"
  activate
  set t to do script "export ZDOTDIR='$WORK/zd'; exec zsh -i"
  delay 1.5
  set current settings of t to settings set "Basic"
  set font size of current settings of t to 17
  set bounds of front window to {160, 120, 1360, 795}
  do script "clear" in front window
end tell
EOF
sleep 1.2
# Below the title bar, which shows the user's name.
record 40 1200:640:160:155 "$WORK/terminal.mp4" & sleep 1.8
osascript -e 'tell application "Terminal" to do script "sh \"$HOME/Downloads/DataLab-Mac/Install DataLab.command\"" in front window' >/dev/null
answered=0
for _ in $(seq 1 120); do
  sleep 0.5
  c="$(osascript -e 'tell application "Terminal" to get contents of front window')"
  n="$(printf '%s' "$c" | grep -cE '\[Y/n\]' || true)"
  if [ "$n" -gt "$answered" ]; then sleep 1.2; osascript -e 'tell application "Terminal" to do script "n" in front window' >/dev/null; answered=$((answered + 1)); fi
  printf '%s' "$c" | grep -q "Or run:" && break
done
wait
osascript -e 'tell application "Terminal" to close front window' >/dev/null 2>&1 || true

cut() { # name from to
  ffmpeg -v error -y -ss "$2" -to "$3" -i "$WORK/terminal.mp4" -vf "pad=1920:1080:0:0:color=0x1a1a1a" \
    -c:v libx264 -crf 16 -pix_fmt yuv420p -g 10 -an "$OUT/$1.mp4"
}
cut term-paste 1.2 4.2
cut term-steps 3.0 7.2
cut term-done 7.0 11.0
ffmpeg -v error -y -ss 1.0 -i "$WORK/finder.mp4" -c:v libx264 -crf 16 -pix_fmt yuv420p -g 10 -an "$OUT/finder.mp4"

# 3. The Desktop shortcut: other apps hidden (not quit) for a moment.
osascript -e 'tell application "Finder" to activate' >/dev/null
osascript -l JavaScript -e 'ObjC.import("AppKit"); $.NSWorkspace.sharedWorkspace.hideOtherApplications; ""'
sleep 1.5
screencapture -x "$WORK/desktop.png"
# The shortcut sits at the right edge, below any other Desktop icons.
ffmpeg -v error -y -loop 1 -i "$WORK/desktop.png" -t 12 -r 30 -vf "crop=960:540:960:160,scale=1920:1080:flags=lanczos" \
  -c:v libx264 -crf 16 -pix_fmt yuv420p -g 10 "$OUT/desktop.mp4"

echo "Done. Check a contact sheet of $WORK/terminal.mp4 against the cut points, then render:"
echo "  node docs/videos/render.mjs 09-getting-started"
