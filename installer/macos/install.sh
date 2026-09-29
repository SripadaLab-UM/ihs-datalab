#!/bin/sh
# DataLab installer for macOS.
#
#   sh install.sh --package <datalab .whl file or URL> [--settings <lab settings file>]
#                 [--requirements <requirements.txt or URL>] [--profile real|practice]
#                 [--no-github] [--install-docker]
#
# requirements.txt comes with each release: every dependency pinned by version
# and hash, and the package by its checksum. It's found automatically if it
# sits next to a local package file. Nothing is installed that it doesn't name.
#
# What it does:
#   1. Checks that Docker Desktop is installed and running, and starts it if it
#      isn't. If it's missing, offers to download Docker's official Docker Desktop
#      for this Mac and checks it's signed by Docker Inc. On an administrator
#      account, Docker's own installer puts it in /Applications (you type your
#      password once, for sudo); otherwise it's copied to Applications. Then it
#      opens it (its first-run window asks you to accept Docker's agreement) and
#      waits until it's running. --install-docker answers yes to that offer.
#   2. Installs uv (a Python installer) for you, if it isn't there already.
#   3. Installs DataLab, with its own Python, in your user account (no admin rights).
#      Each version gets its own folder, so an update installs beside the one in use
#      and the previous version is kept (docs/DISTRIBUTION.md).
#   4. Downloads the pinned container images (for practice, Oracle Database Free too).
#   5. Saves the lab's settings and asks for your U-M GPT key and database password,
#      which go into your macOS Keychain. Practice asks for no password, and the key
#      is optional there.
#   6. Offers the GitHub sign-in for the lab's knowledge base and pipelines, then
#      downloads both. For practice, instead: sets up the practice database (a
#      container on this computer only, with made-up data), keeping any data it
#      already has.
#   7. Adds the DataLab app ("DataLab (practice)" for practice, with its own icon) to
#      /Applications, or to ~/Applications if you can't add to /Applications without
#      sudo, and a shortcut to it on your Desktop. At the end it says where everything
#      went and offers to show the app in Finder and open it.
#
# DATALAB_SYSTEM_APPLICATIONS (for tests) stands in for /Applications.
set -eu

# Everything goes in your own user account: never run this as root.
[ "$(id -u)" -ne 0 ] || { echo "Run this without sudo."; exit 1; }
# Nothing from the environment may steer uv or pip (another index, checks
# turned off) or Python.
for name in $(env | sed -n 's/^\(UV_[A-Za-z0-9_]*\)=.*/\1/p; s/^\(PIP_[A-Za-z0-9_]*\)=.*/\1/p'); do
  unset "$name"
done
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP VIRTUAL_ENV

UV_VERSION=0.12.19
PACKAGE=""
SETTINGS=""
REQUIREMENTS=""
PROFILE="real"
GITHUB="ask"
INSTALL_DOCKER="ask"
while [ $# -gt 0 ]; do
  case "$1" in
    --package) PACKAGE="$2"; shift 2 ;;
    --settings) SETTINGS="$2"; shift 2 ;;
    --requirements) REQUIREMENTS="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --no-github) GITHUB="no"; shift ;;
    --install-docker) INSTALL_DOCKER="yes"; shift ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done
if [ -z "$PACKAGE" ]; then
  echo "Usage: sh install.sh --package <datalab .whl file or URL> [--settings <file>]"
  exit 2
fi
case "$PROFILE" in
  real|practice) ;;
  *) echo "--profile must be real or practice, not $PROFILE"; exit 2 ;;
esac

# /Applications (DATALAB_SYSTEM_APPLICATIONS in tests), and your own.
SYSTEM_APPS="${DATALAB_SYSTEM_APPLICATIONS:-/Applications}"
USER_APPS="$HOME/Applications"
# Cleans up on the way out, however it ends: Docker's disk image is detached
# a half-made copy of it removed, and the download folder removed (a partial
# download is kept, to resume).
STAGE=""
DOCKER_MOUNT=""
DOCKER_PARTIAL=""
# The half-made copy of Docker Desktop, if there is one (only ever that name).
remove_partial_copy() {
  case "$DOCKER_PARTIAL" in
    */.Docker.app.datalab-partial) rm -rf "$DOCKER_PARTIAL" 2>/dev/null || true ;;
  esac
}
cleanup() {
  if [ -n "$DOCKER_MOUNT" ]; then
    hdiutil detach -quiet -force "$DOCKER_MOUNT" >/dev/null 2>&1 || true
    rmdir "$DOCKER_MOUNT" 2>/dev/null || true
  fi
  remove_partial_copy
  if [ -n "$STAGE" ]; then rm -rf "$STAGE"; fi
}
trap cleanup EXIT
trap 'echo; echo "Stopped. Run this installer again when you are ready: it carries on from where it"; echo "stopped (a partly downloaded Docker Desktop is resumed; a half-made copy was removed)."; exit 130' INT TERM HUP

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
# Questions are read from the terminal, even when this script arrives on a pipe.
ask() {
  printf '%s ' "$1"
  answer=""
  if [ -r /dev/tty ]; then read -r answer < /dev/tty || answer=""; fi
  case "$answer" in [nN]*) return 1 ;; *) return 0 ;; esac
}

# ------------------------------------------------------------ Docker Desktop
# Docker Desktop is found where it's installed (/Applications or
# ~/Applications), and its `docker` command even when it isn't on PATH (the
# /usr/local/bin link missing, or linking to a Docker.app elsewhere): then
# the command inside the app is used, by its full path, and its folder goes
# last on PATH for the rest of this install (it holds Docker's credential
# helpers too). DataLab does the same each time it starts
# (datalab/docker_path.py).
#
# If Docker Desktop is missing, it offers to download Docker's official one
# for this Mac, checks it's signed and notarized by Docker Inc before running
# anything from it, and installs it. An existing Docker Desktop is never
# reinstalled, upgraded, reset or reconfigured, and its containers, images,
# volumes and settings are never touched.
#
# For tests: DATALAB_DOCKER_WAIT_SECONDS and DATALAB_DOCKER_POLL_SECONDS set
# how long and how often it waits for Docker; DATALAB_INSTALL_STOP_AFTER_DOCKER=1
# stops once Docker is ready, before anything is installed.
DOCKER_TEAM_ID=9BNSXJN65R
DOCKER_BUNDLE_ID=com.docker.docker
# Signed by Docker Inc's Developer ID; for the app, also its bundle id.
DOCKER_SIGNER="anchor apple generic and certificate leaf[subject.OU] = \"$DOCKER_TEAM_ID\""
DOCKER_TERMS=https://www.docker.com/legal/docker-subscription-service-agreement/
DOCKER_PAGE=https://docs.docker.com/desktop/setup/install/mac-install/
# Docker Desktop supports the current and two previous major macOS releases:
# 14 (Sonoma) or newer as of Docker Desktop 4.93. The downloaded app's own
# minimum is checked as well, before installing it.
DOCKER_MIN_MACOS=14.0
# The download (about 0.6 GB), the app (about 2 GB) and room for its first start.
DOCKER_NEED_GB=6
DOCKER_POLL="${DATALAB_DOCKER_POLL_SECONDS:-5}"
DOCKER_CACHE="$HOME/Library/Caches/DataLab/docker-desktop"
DOCKER=""
DOCKER_APP=""
DOCKER_ADDED_TO_PATH=0

# Whether $1 is a Docker Desktop app bundle (by its bundle id).
is_docker_bundle() {
  [ -d "$1" ] && [ ! -L "$1" ] || return 1
  [ "$(plutil -extract CFBundleIdentifier raw -o - "$1/Contents/Info.plist" 2>/dev/null)" = "$DOCKER_BUNDLE_ID" ]
}
# The Docker Desktop app, if one is installed: /Applications, then ~/Applications.
find_docker_app() {
  DOCKER_APP=""
  for dir in "$SYSTEM_APPS" "$USER_APPS"; do
    if [ -d "$dir/Docker.app" ]; then DOCKER_APP="$dir/Docker.app"; return 0; fi
  done
  return 1
}
# $1 with every symbolic link followed: the file it really is.
real_path() {
  p="$1"; hops=0
  while [ -L "$p" ] && [ "$hops" -lt 40 ]; do
    to="$(readlink "$p")" || return 1
    case "$to" in /*) p="$to" ;; *) p="$(dirname "$p")/$to" ;; esac
    hops=$((hops + 1))
  done
  dir="$(cd "$(dirname "$p")" 2>/dev/null && pwd -P)" || return 1
  printf '%s/%s\n' "$dir" "$(basename "$p")"
}
# Whether $1 is an app installed in an Applications folder (/Applications,
# ~/Applications, or a folder inside either). Anything else isn't taken for
# an installed Docker Desktop: Docker's own half-done install or uninstall
# (~/Library/Application Support/com.docker.install/in_progress/Docker.app),
# the Trash, a disk image, a cache.
in_applications_folder() {
  case "$1" in
    */com.docker.install/*|*/.Trash/*|*/Library/*|*/Caches/*|/Volumes/*|"$DOCKER_CACHE"/*) return 1 ;;
    "$SYSTEM_APPS"/*|"$USER_APPS"/*|/Applications/*) return 0 ;;
  esac
  return 1
}
# A Docker Desktop elsewhere in an Applications folder: the app the docker
# command on PATH really is (a link to a renamed Docker.app, or one in a
# subfolder), or failing that one Spotlight knows.
find_docker_app_elsewhere() {
  if [ -n "$DOCKER" ]; then
    real="$(real_path "$DOCKER" || true)"
    case "$real" in
      */Contents/Resources/bin/docker)
        bundle="${real%/Contents/Resources/bin/docker}"
        if in_applications_folder "$bundle" && is_docker_bundle "$bundle"; then
          DOCKER_APP="$bundle"; return 0
        fi ;;
    esac
  fi
  command -v mdfind >/dev/null 2>&1 || return 1
  found="$(mdfind "kMDItemCFBundleIdentifier == '$DOCKER_BUNDLE_ID'" 2>/dev/null || true)"
  while IFS= read -r bundle; do
    case "$bundle" in
      */Docker.app/*) continue ;;
      *.app)
        if in_applications_folder "$bundle" && is_docker_bundle "$bundle"; then
          DOCKER_APP="$bundle"; return 0
        fi ;;
    esac
  done <<FOUND
$found
FOUND
  return 1
}
# The docker command: on PATH, or where Docker Desktop keeps it. With
# "prefer-app", the one in DOCKER_APP comes first (just installed: not
# another Docker's, such as Colima's or Homebrew's, that's on PATH).
find_docker_cli() {
  DOCKER=""
  app_cli="${DOCKER_APP:+$DOCKER_APP/Contents/Resources/bin/docker}"
  if [ "${1:-}" = prefer-app ] && [ -n "$app_cli" ] && [ -x "$app_cli" ]; then
    DOCKER="$app_cli"
    # The one on PATH, when it's a link to this same command.
    on_path="$(command -v docker 2>/dev/null || true)"
    if [ -n "$on_path" ] && [ "$(real_path "$on_path" || true)" = "$(real_path "$app_cli" || true)" ]; then
      DOCKER="$on_path"
    fi
  else
    DOCKER="$(command -v docker 2>/dev/null || true)"
    if [ -n "$DOCKER" ]; then return 0; fi
    for candidate in ${app_cli:+"$app_cli"} "$HOME/.docker/bin/docker"; do
      if [ -x "$candidate" ]; then DOCKER="$candidate"; break; fi
    done
  fi
  [ -n "$DOCKER" ] || return 1
  # Last on PATH: it doesn't hide anything already there.
  case ":$PATH:" in
    *":$(dirname "$DOCKER"):"*) ;;
    *) PATH="$PATH:$(dirname "$DOCKER")"; export PATH; DOCKER_ADDED_TO_PATH=1 ;;
  esac
}
# Runs a command, giving up after $1 seconds (a starting Docker can hang):
# then it's sent SIGTERM, and SIGKILL 2 seconds later, and this returns 124.
# The limit is kept by a parent process, not by an alarm in the command
# itself: docker is a Go program, and Go ignores SIGALRM. Perl runs the
# command directly (no shell), in a process group of its own so anything it
# started goes too; without perl, a background watcher does the same.
# DATALAB_TEST_WITHIN=sh (for tests) uses the watcher even with perl there.
within() {
  seconds="$1"; shift
  if [ "${DATALAB_TEST_WITHIN:-}" != sh ] && command -v perl >/dev/null 2>&1; then
    perl -e '
      use POSIX ":sys_wait_h";
      my $seconds = shift;
      my $pid = fork;
      exit 127 unless defined $pid;
      if ($pid == 0) { setpgrp(0, 0); exec { $ARGV[0] } @ARGV; exit 127 }
      sub stop {
        kill "TERM", -$pid, $pid;
        for (1 .. 20) { return if waitpid($pid, WNOHANG) == $pid; select(undef, undef, undef, 0.1) }
        kill "KILL", -$pid, $pid;
        waitpid($pid, 0);
      }
      $SIG{INT} = sub { stop(); exit 130 };
      $SIG{TERM} = sub { stop(); exit 143 };
      $SIG{ALRM} = sub { stop(); exit 124 };
      alarm $seconds;
      waitpid($pid, 0);
      alarm 0;
      exit(($? & 127) ? 128 + ($? & 127) : $? >> 8);
    ' "$seconds" "$@"
  else
    flag="$(mktemp "${TMPDIR:-/tmp}/datalab-within.XXXXXX")"
    "$@" &
    pid=$!
    ( /bin/sleep "$seconds"; : > "$flag.fired"; kill -TERM "$pid" 2>/dev/null
      /bin/sleep 2; kill -KILL "$pid" 2>/dev/null ) >/dev/null 2>&1 &
    watcher=$!
    status=0
    wait "$pid" || status=$?
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    if [ -e "$flag.fired" ]; then status=124; fi
    rm -f "$flag" "$flag.fired"
    return "$status"
  fi
}
# Whether Docker's engine answers. DOCKER_HUNG=1 when `docker info` had to be
# stopped (the app is up but its engine isn't answering).
DOCKER_INFO_SECONDS="${DATALAB_DOCKER_INFO_SECONDS:-20}"
DOCKER_HUNG=0
docker_ready() {
  [ -n "$DOCKER" ] || return 1
  answered=0
  within "$DOCKER_INFO_SECONDS" "$DOCKER" info >/dev/null 2>&1 || answered=$?
  if [ "$answered" -eq 124 ]; then DOCKER_HUNG=1; else DOCKER_HUNG=0; fi
  [ "$answered" -eq 0 ]
}
# Whether any of Docker Desktop's programs are running (to notice it quitting).
# The path is compared as plain text, whatever characters it has.
docker_app_alive() {
  running="$(ps -axo command= 2>/dev/null)" || return 0
  case "$running" in *"$1/Contents/MacOS/"*) return 0 ;; esac
  return 1
}
# Whether the text $1 has the line $2, or (has_line_starting) a line that
# starts with $2. Plain text, and no pipe (grep -q in a pipe can leave
# printf writing to a closed pipe: "write error: Broken pipe").
NL='
'
has_line() { case "$NL$1$NL" in *"$NL$2$NL"*) return 0 ;; esac; return 1; }
has_line_starting() { case "$NL$1" in *"$NL$2"*) return 0 ;; esac; return 1; }
# Whether $1 (a macOS version) is at least $2.
version_at_least() {
  awk -v have="$1" -v need="$2" 'BEGIN {
    split(have, h, "."); split(need, n, ".")
    for (i = 1; i <= 3; i++) { if (h[i] + 0 > n[i] + 0) exit 0; if (h[i] + 0 < n[i] + 0) exit 1 }
    exit 0 }'
}
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR == 2 { print int($4 / 1048576) }'; }
now() { date +%s; }
# Yes unless the person types n or no; pressing Return is yes. With no
# terminal to answer (a script, a pipe, CI), no: nothing is installed unasked.
ask_default_yes() {
  printf '%s ' "$1"
  answer=""
  [ -r /dev/tty ] || return 1
  read -r answer < /dev/tty 2>/dev/null || return 1
  case "$answer" in [nN]|[nN][oO]) return 1 ;; *) return 0 ;; esac
}
# For a Docker Desktop that doesn't come up: leftovers of an earlier install.
leftovers() {
  echo "  - If Docker Desktop was uninstalled or installed only partway before, programs"
  echo "    from it may still be running: quit them (Activity Monitor, search for docker,"
  echo "    Quit), or simply restart the Mac."
}
rerun() {
  echo "Then run this installer again, the same way: it carries on from where it stopped,"
  echo "and anything already done isn't done twice."
}
# $1 is Docker Desktop, signed by Docker Inc and notarized. Sets
# NOT_GENUINE to why not: "gatekeeper" when Gatekeeper is off, so macOS
# can't say whether it's notarized, otherwise "signature".
docker_app_is_genuine() {
  NOT_GENUINE=signature
  [ -d "$1" ] && [ ! -L "$1" ] || return 1
  codesign --verify --deep --strict -R="$DOCKER_SIGNER and identifier \"$DOCKER_BUNDLE_ID\"" "$1" \
    >/dev/null 2>&1 || return 1
  signed="$(codesign -dv --verbose=2 "$1" 2>&1)" || return 1
  has_line "$signed" "TeamIdentifier=$DOCKER_TEAM_ID" || return 1
  has_line "$signed" "Identifier=$DOCKER_BUNDLE_ID" || return 1
  assessed="$(spctl -a -vv -t exec "$1" 2>&1)" || return 1
  if ! has_line_starting "$assessed" "origin="; then
    NOT_GENUINE=gatekeeper
    return 1
  fi
  has_line "$assessed" "source=Notarized Developer ID" || return 1
  has_line "$assessed" "origin=Developer ID Application: Docker Inc ($DOCKER_TEAM_ID)"
}
detach_docker_image() {
  if [ -n "$DOCKER_MOUNT" ]; then
    hdiutil detach -quiet "$DOCKER_MOUNT" >/dev/null 2>&1 \
      || hdiutil detach -quiet -force "$DOCKER_MOUNT" >/dev/null 2>&1 || true
    rmdir "$DOCKER_MOUNT" 2>/dev/null || true
    DOCKER_MOUNT=""
  fi
}
# Stops the Docker Desktop install: the image detached, the partial copy
# and (with "delete") the download removed, then $2... printed.
stop_install() {
  what="$1"; shift
  detach_docker_image
  remove_partial_copy
  rm -f "$DOCKER_CACHE/copy-errors"
  if [ "$what" = delete ]; then rm -f "$DOCKER_CACHE/Docker-$arch.dmg"; fi
  for line; do echo "$line"; done
  rerun
  exit 1
}

# Downloads, checks and installs Docker Desktop, and sets DOCKER_APP. Its
# agreement is left for the person to accept when Docker first opens.
install_docker_desktop() {
  echo "Docker Desktop isn't installed. DataLab runs its assistant in Docker, so it's needed."
  echo
  if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || true)" = 1 ]; then
    arch=arm64; kind="Apple silicon"
  elif [ "$(uname -m)" = x86_64 ]; then
    arch=amd64; kind="Intel"
  else
    echo "This Mac's processor ($(uname -m)) isn't one Docker Desktop supports, so DataLab"
    echo "can't run on it. Ask the lab about another computer."
    exit 1
  fi
  macos="$(sw_vers -productVersion 2>/dev/null || echo 0)"
  if ! version_at_least "$macos" "$DOCKER_MIN_MACOS"; then
    echo "Docker Desktop needs macOS $DOCKER_MIN_MACOS or newer, and this Mac has macOS $macos."
    echo "Update macOS first (Apple menu > System Settings > General > Software Update)."
    rerun
    echo "If this Mac can't be updated that far, DataLab can't run on it: ask the lab."
    exit 1
  fi
  # An administrator account installs it with Docker's own installer (its
  # supported command-line install: into /Applications, with its helper and
  # /usr/local/bin links set up, after the person types their password for
  # sudo). Other accounts get a copy of the app: in /Applications if they can
  # add to it without sudo, otherwise ~/Applications.
  case " $(id -Gn 2>/dev/null) " in
    *" admin "*) method=docker-installer; place="$SYSTEM_APPS" ;;
    *) method=copy
       if [ -d "$SYSTEM_APPS" ] && [ -w "$SYSTEM_APPS" ]; then place="$SYSTEM_APPS"; else place="$USER_APPS"; fi ;;
  esac
  for where in "$HOME" "$(dirname "$place")"; do
    have="$(free_gb "$where")"
    if [ -n "$have" ] && [ "$have" -lt "$DOCKER_NEED_GB" ]; then
      echo "Docker Desktop needs about $DOCKER_NEED_GB GB of free disk space (for the download,"
      echo "the app and its first start), and this Mac has $have GB free."
      echo "Free up some space (Apple menu > System Settings > General > Storage)."
      rerun
      exit 1
    fi
  done

  echo "This installer can download Docker Desktop for this Mac ($kind) from Docker"
  if [ "$method" = docker-installer ]; then
    echo "(desktop.docker.com), check that it's signed by Docker Inc, and install it in"
    echo "$place with Docker's own installer: macOS asks for your password once, in"
    echo "this window. The download is about 0.6 GB."
  else
    echo "(desktop.docker.com), check that it's signed by Docker Inc, and copy it to"
    echo "$place. The download is about 0.6 GB."
  fi
  echo "When Docker Desktop first opens, it shows the Docker Subscription Service"
  echo "Agreement ($DOCKER_TERMS)"
  echo "for you to read and accept yourself: this installer doesn't accept it for you."
  echo "Docker Desktop's license terms apply to its use; your organisation may have its"
  echo "own guidance about Docker."
  if [ "$INSTALL_DOCKER" != yes ] \
    && ! ask_default_yes "Download and install Docker Desktop? [Y/n]"; then
    echo
    echo "Docker Desktop wasn't installed, and nothing was changed. To install it yourself:"
    echo "  1. Download Docker Desktop for Mac ($kind) from $DOCKER_PAGE"
    echo "  2. Open Docker.dmg and drag Docker to Applications."
    echo "  3. Open Docker from Applications and finish its setup, until its whale icon"
    echo "     at the top of the screen says Docker Desktop is running."
    rerun
    exit 1
  fi

  mkdir -p "$DOCKER_CACHE"
  dmg="$DOCKER_CACHE/Docker-$arch.dmg"
  if [ -f "$dmg" ]; then
    echo "Using the Docker Desktop download from before."
  else
    echo "Downloading Docker Desktop…"
    got=0
    # Given up on if it stalls (under 10 kB/s for 2 minutes).
    curl -fL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3 --connect-timeout 30 \
      --speed-limit 10240 --speed-time 120 --progress-bar -C - -o "$dmg.part" \
      "https://desktop.docker.com/mac/main/$arch/Docker.dmg" || got=$?
    if [ "$got" -ne 0 ]; then
      # 22: the server refused (a finished or stale part can't be resumed).
      # 33: it can't resume at all. Either way the next try starts afresh.
      case "$got" in 22|33) rm -f "$dmg.part" ;; esac
      echo
      echo "The Docker Desktop download didn't finish (curl stopped with code $got)."
      echo "Check this Mac is online (on a U-M network or VPN, desktop.docker.com must be"
      echo "reachable)."
      rerun
      echo "It continues the download where it stopped."
      exit 1
    fi
    mv "$dmg.part" "$dmg"
  fi

  echo "Checking the download…"
  # Docker signs the disk image itself too: checked before it's opened.
  if ! codesign --verify -R="$DOCKER_SIGNER" "$dmg" >/dev/null 2>&1; then
    stop_install delete \
      "The Docker Desktop download isn't signed by Docker Inc (team $DOCKER_TEAM_ID), so it" \
      "was deleted without being opened, and nothing was installed. This is unusual; a" \
      "network filter may have changed it. If it happens again, install Docker Desktop" \
      "yourself from $DOCKER_PAGE (or ask IT)."
  fi
  DOCKER_MOUNT="$(mktemp -d "$DOCKER_CACHE/mount.XXXXXX")"
  if ! hdiutil attach -quiet -nobrowse -readonly -noautoopen -mountpoint "$DOCKER_MOUNT" "$dmg" >/dev/null 2>&1; then
    rmdir "$DOCKER_MOUNT" 2>/dev/null || true
    DOCKER_MOUNT=""
    stop_install delete \
      "The Docker Desktop download couldn't be opened (it may be incomplete), so it was" \
      "deleted and nothing was installed. Running this again downloads it again."
  fi
  source_app="$DOCKER_MOUNT/Docker.app"
  if ! docker_app_is_genuine "$source_app"; then
    if [ "$NOT_GENUINE" = gatekeeper ]; then
      stop_install delete \
        "macOS's Gatekeeper is turned off on this Mac, so macOS can't confirm that the" \
        "downloaded Docker Desktop is notarized by Apple. Nothing from it was run or" \
        "installed, and the download was deleted. Turn Gatekeeper back on (ask IT if" \
        "your Mac is managed), or install Docker Desktop yourself from $DOCKER_PAGE."
    fi
    stop_install delete \
      "The downloaded Docker Desktop didn't pass macOS's checks: it must be signed and" \
      "notarized by Docker Inc (team $DOCKER_TEAM_ID). Nothing from it was run or installed," \
      "and the download was deleted. This is unusual; a network filter may have changed it." \
      "If it happens again, install Docker Desktop yourself from $DOCKER_PAGE (or ask IT)."
  fi
  needs="$(plutil -extract LSMinimumSystemVersion raw -o - "$source_app/Contents/Info.plist" 2>/dev/null || echo 0)"
  if ! version_at_least "$macos" "$needs"; then
    stop_install delete \
      "This Docker Desktop needs macOS $needs or newer, and this Mac has macOS $macos." \
      "Update macOS first (Apple menu > System Settings > General > Software Update)."
  fi
  echo "It's Docker Desktop $(plutil -extract CFBundleShortVersionString raw -o - "$source_app/Contents/Info.plist" 2>/dev/null || echo ''), signed and notarized by Docker Inc."

  installer_command="$source_app/Contents/MacOS/install"
  if [ "$method" = docker-installer ] && [ -x "$installer_command" ] && [ ! -L "$installer_command" ]; then
    install_with_dockers_installer
    return
  fi

  # Copied as it is (ditto keeps its signature), under a temporary name
  # until it's complete and checked again. /Applications if this account can
  # add to it without sudo, otherwise ~/Applications. Removed on the way out
  # if it doesn't get that far (Ctrl-C included).
  mkdir -p "$place" 2>/dev/null || true
  DOCKER_PARTIAL="$place/.Docker.app.datalab-partial"
  remove_partial_copy
  copy_errors="$DOCKER_CACHE/copy-errors"
  echo "Copying Docker Desktop to ${place}…"
  copied=0
  ditto "$source_app" "$DOCKER_PARTIAL" 2>"$copy_errors" || copied=$?
  if [ "$copied" -ne 0 ] || [ -L "$DOCKER_PARTIAL" ] || [ ! -d "$DOCKER_PARTIAL" ]; then
    copy_failed "$place" "$copy_errors"
  fi
  if ! docker_app_is_genuine "$DOCKER_PARTIAL"; then
    stop_install delete \
      "The copy of Docker Desktop in $place didn't pass macOS's checks again, so it" \
      "was removed, with the download."
  fi
  # Checked again just before: if a Docker.app arrived meanwhile, mv would
  # put the copy inside it.
  if [ -e "$place/Docker.app" ] || [ -L "$place/Docker.app" ]; then
    stop_install keep \
      "A Docker.app appeared in $place while this copy was being made (did you" \
      "install Docker Desktop at the same time?). This copy was removed; that one was" \
      "left as it is."
  fi
  moved=0
  mv "$DOCKER_PARTIAL" "$place/Docker.app" 2>"$copy_errors" || moved=$?
  if [ "$moved" -ne 0 ]; then copy_failed "$place" "$copy_errors"; fi
  DOCKER_PARTIAL=""
  DOCKER_APP="$place/Docker.app"
  detach_docker_image
  rm -f "$dmg" "$copy_errors"
  rmdir "$DOCKER_CACHE" 2>/dev/null || true
  echo "Docker Desktop is installed in $(dirname "$DOCKER_APP")."
}
# Docker's own installer, from the checked disk image, run with sudo: the
# person types their password (sudo reads it from the terminal; nothing is
# passed to it). Docker's agreement isn't accepted here: Docker shows it
# when it first opens, for the person to accept.
install_with_dockers_installer() {
  echo
  echo "Installing Docker Desktop in $place with Docker's installer. macOS asks for your"
  echo "password (the one you log in to this Mac with) to install Docker Desktop: type it"
  echo "and press Return. Nothing shows as you type."
  status=0
  sudo "$installer_command" --user "$(id -un)" || status=$?
  if [ "$status" -ne 0 ]; then
    left=""
    if [ -e "$place/Docker.app" ]; then
      left="Docker's installer may have left $place/Docker.app partly installed: drag it to the Trash first."
    fi
    stop_install keep \
      "Docker Desktop wasn't installed: the password wasn't given or accepted, or Docker's" \
      "installer stopped (code $status). ${left:-Nothing was changed.}" \
      "If your account can't use sudo, ask IT, or install Docker Desktop yourself from" \
      "$DOCKER_PAGE."
  fi
  if ! docker_app_is_genuine "$place/Docker.app"; then
    stop_install keep \
      "Docker's installer finished, but $place/Docker.app didn't pass macOS's checks" \
      "(signed and notarized by Docker Inc, team $DOCKER_TEAM_ID). Drag it to the Trash, or" \
      "ask IT."
  fi
  DOCKER_APP="$place/Docker.app"
  detach_docker_image
  rm -f "$dmg"
  rmdir "$DOCKER_CACHE" 2>/dev/null || true
  echo "Docker Desktop is installed in $place."
}
# Why copying to $1 failed, from the errors in $2.
copy_failed() {
  if grep -q "Operation not permitted" "$2" 2>/dev/null; then
    stop_install keep \
      "macOS didn't let Terminal add Docker Desktop to $1 (\"Operation not permitted\")." \
      "Either allow it: System Settings > Privacy & Security > App Management, turn on" \
      "Terminal (then quit and reopen Terminal); or install into your own Applications" \
      "folder instead: create a folder called Applications in your home folder." \
      "Your organisation's device management may also block this: then ask IT."
  fi
  stop_install keep \
    "Docker Desktop couldn't be copied to $1. macOS or your organisation's" \
    "device management may not allow adding apps there. On a Mac your organisation" \
    "manages, install Docker Desktop from its Self Service app or ask IT; otherwise" \
    "check there's space and that you can add files to $1."
}

# Opens Docker Desktop and waits until it answers. $1: "new" right after
# installing it (with its first-run guide), otherwise "existing".
start_docker() {
  if [ "$1" = new ]; then
    limit="${DATALAB_DOCKER_WAIT_SECONDS:-900}"
    echo
    echo "Docker Desktop is opening for the first time. In its window:"
    echo "  1. It shows the Docker Subscription Service Agreement. Read it and choose"
    echo "     Accept if you agree: Docker Desktop won't run without it. (If you decline,"
    echo "     it quits, and nothing else changes.)"
    echo "  2. Choose \"Use recommended settings\", then Finish. Type your Mac password if"
    echo "     asked (for Docker's helper)."
    case "$DOCKER_APP" in
      "$USER_APPS"/*)
        echo "     No administrator password? Choose \"Use advanced settings\" instead, set"
        echo "     the command line tools to \"User\", untick anything that needs a password,"
        echo "     then Finish." ;;
    esac
    echo "  3. Signing in to Docker is optional: choose Skip, or close the sign-in window."
    echo "     Skip any survey too."
    echo "This installer carries on by itself once Docker Desktop is running."
  else
    limit="${DATALAB_DOCKER_WAIT_SECONDS:-300}"
    # An empty or damaged leftover isn't a Docker Desktop that can start.
    program="$(plutil -extract CFBundleExecutable raw -o - "$DOCKER_APP/Contents/Info.plist" 2>/dev/null || true)"
    if [ -z "$program" ] || [ ! -x "$DOCKER_APP/Contents/MacOS/$program" ]; then
      echo "$DOCKER_APP is there, but it isn't a complete Docker Desktop (it may be left"
      echo "over from an earlier install). Drag it to the Trash; your containers and images"
      echo "aren't in it."
      rerun
      echo "It then offers to install Docker Desktop."
      exit 1
    fi
    echo "Starting Docker Desktop… If its window asks you something (its agreement, your"
    echo "password, signing in), answer it there; signing in is optional."
  fi
  if ! open "$DOCKER_APP" >/dev/null 2>&1; then
    echo "Docker Desktop ($DOCKER_APP) couldn't be opened. If it's damaged, drag it to the"
    echo "Trash (your containers and images aren't in it); otherwise open it from"
    echo "Applications and wait until its whale icon at the top of the screen says it's running."
    rerun
    exit 1
  fi
  # Timed by the clock: each `docker info` is stopped after 20 seconds.
  # A Docker Desktop that was already installed, whose programs are running
  # but whose engine hasn't answered for 90 seconds, is restarted once
  # (`docker desktop restart`, which fixes a stuck engine). Never on a first
  # run: its window may still be waiting for the person.
  restart_after="${DATALAB_DOCKER_RESTART_AFTER:-90}"
  restarted=0
  started="$(now)"
  said=0
  gone=0
  until docker_ready; do
    if [ -z "$DOCKER" ]; then find_docker_cli prefer-app || true; fi
    waited=$(($(now) - started))
    if [ "$waited" -ge "$limit" ]; then
      if [ "$limit" -ge 120 ]; then took="$((limit / 60)) minutes"; else took="$limit seconds"; fi
      if [ "$DOCKER_HUNG" = 1 ] || { [ "$1" = existing ] && docker_app_alive "$DOCKER_APP"; }; then
        echo "Docker Desktop is open, but its engine isn't answering after $took."
        echo "  1. Restart it: whale menu at the top of the screen > Restart."
        echo "  2. If that doesn't help: whale menu > Troubleshoot > Restart. If it still"
        echo "     doesn't answer, Troubleshoot offers \"Clean / Purge data\" and \"Reset to"
        echo "     factory defaults\": both DELETE Docker's containers, images and volumes"
        echo "     (for DataLab's practice database, its made-up data), so only if you're sure."
        leftovers
        rerun
        exit 1
      fi
      echo "Docker Desktop isn't ready after $took."
      echo "  - If its window is asking something (its agreement, your password), answer it."
      echo "  - If it shows an error, choose Restart in its whale menu at the top of the screen."
      echo "  - Wait until the whale menu says Docker Desktop is running."
      leftovers
      rerun
      exit 1
    fi
    alive=1
    if ! docker_app_alive "$DOCKER_APP"; then alive=0; fi
    if [ "$waited" -ge 30 ] && [ "$alive" -eq 0 ]; then
      gone=$((gone + 1))
    else
      gone=0
    fi
    if [ "$1" = existing ] && [ "$restarted" -eq 0 ] && [ "$alive" -eq 1 ] \
      && [ "$waited" -ge "$restart_after" ] && [ -n "$DOCKER" ]; then
      restarted=1
      if within 20 "$DOCKER" desktop restart --help >/dev/null 2>&1; then
        echo "Docker Desktop is open but its engine isn't answering; restarting it…"
        within 120 "$DOCKER" desktop restart >/dev/null 2>&1 \
          || echo "(Docker Desktop's restart didn't finish; still waiting.)"
      fi
    fi
    if [ "$gone" -ge 2 ]; then
      echo "Docker Desktop closed before it was ready. If you declined its agreement, that's"
      echo "why: Docker Desktop doesn't run without it. When you're ready, open Docker from"
      echo "Applications, accept its agreement and finish its setup."
      leftovers
      rerun
      exit 1
    fi
    if [ "$waited" -ge $((said + 30)) ]; then
      said=$((waited - waited % 30))
      echo "Still waiting for Docker Desktop ($said seconds)…"
    fi
    sleep "$DOCKER_POLL"
  done
}

step "1/7 Docker Desktop"
find_docker_app || true
find_docker_cli || true
if docker_ready; then
  :
elif [ -n "$DOCKER_APP" ] || find_docker_app_elsewhere; then
  find_docker_cli prefer-app || true
  start_docker existing
else
  if [ -n "$DOCKER" ]; then
    echo "A docker command is installed ($DOCKER), but it isn't answering, and Docker"
    echo "Desktop isn't installed. If you use another Docker (Colima, OrbStack), start it"
    echo "and run this installer again. Otherwise:"
    echo
  fi
  install_docker_desktop
  find_docker_cli prefer-app || true
  start_docker new
fi
echo "Docker Desktop is running."
if [ "$DOCKER_ADDED_TO_PATH" = 1 ]; then
  echo "(Its docker command isn't on your PATH, which is fine: DataLab finds it in $(dirname "$DOCKER").)"
fi
if [ "${DATALAB_INSTALL_STOP_AFTER_DOCKER:-}" = 1 ]; then
  echo "Stopping here: DATALAB_INSTALL_STOP_AFTER_DOCKER is set."
  exit 0
fi

step "2/7 uv"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
fi
uv --version

step "3/7 DataLab"
# The package's file name carries its version: datalab-<version>-py3-none-any.whl
WHEEL="$(basename "${PACKAGE%%\?*}")"
VERSION="$(printf '%s' "$WHEEL" | sed -n -e 's/^datalab-\([0-9][A-Za-z0-9.+!]*[A-Za-z0-9]\)-py3-none-any\.whl$/\1/p' \
  -e 's/^datalab-\([0-9]\)-py3-none-any\.whl$/\1/p')"
if [ -z "$VERSION" ]; then
  echo "The package must be a datalab-<version>-py3-none-any.whl file."
  exit 2
fi
case "$PACKAGE" in
  *://*) ;;
  *) if [ -z "$REQUIREMENTS" ] && [ -f "$(dirname "$PACKAGE")/requirements.txt" ]; then
       REQUIREMENTS="$(dirname "$PACKAGE")/requirements.txt"
     fi ;;
esac
if [ -z "$REQUIREMENTS" ]; then
  echo "requirements.txt (every dependency, pinned by hash) wasn't found. Pass --requirements."
  exit 2
fi
# uv gets both files under plain names, from their own folder: it cuts a path
# at its first space ("Application Support", "OneDrive - …").
STAGE="$(mktemp -d)"
fetch() {
  case "$1" in
    https://*) curl -fsSL --proto '=https' --proto-redir '=https' -o "$2" "$1" ;;
    *://*) echo "Only https downloads: $1"; exit 2 ;;
    *) cp "$1" "$2" ;;
  esac
}
fetch "$PACKAGE" "$STAGE/$WHEEL"
fetch "$REQUIREMENTS" "$STAGE/requirements.txt"
# The package must be the one requirements.txt names, by its checksum.
SHA256="$(shasum -a 256 "$STAGE/$WHEEL" | cut -d ' ' -f 1)"
if ! grep -qxF "./$WHEEL --hash=sha256:$SHA256" "$STAGE/requirements.txt"; then
  echo "requirements.txt doesn't name this package with this checksum. Use the two files"
  echo "from the same DataLab release."
  exit 1
fi
# Beside the data folders: versions/<version>/, current, previous, bin/datalab.
# The real and practice DataLabs share them.
ROOT="${DATALAB_INSTALL_DIR:-$HOME/Library/Application Support/DataLab/app}"
TARGET="$ROOT/versions/$VERSION"
if [ -f "$TARGET/.complete" ] && grep -qF "\"wheel_sha256\": \"$SHA256\"" "$TARGET/.complete"; then
  echo "DataLab $VERSION is installed already."
else
  # A folder without .complete (or with another package) is replaced.
  rm -rf "$TARGET"
  mkdir -p "$ROOT/versions"
  uv venv -q --no-config --python 3.13 "$TARGET"
  # Every file checked against requirements.txt's hashes, only wheels, and
  # only from PyPI.
  (cd "$STAGE" && uv pip install -q --no-config --require-hashes --only-binary :all: \
    --default-index https://pypi.org/simple --link-mode copy --python "$TARGET/bin/python" -r requirements.txt)
  SAID="$("$TARGET/bin/datalab" --version)"
  if [ "$SAID" != "datalab $VERSION" ]; then
    echo "The installed DataLab says '$SAID', not $VERSION."
    rm -rf "$TARGET"
    exit 1
  fi
  sync
  printf '{"version": "%s", "wheel_sha256": "%s", "installed_at": "%s"}\n' \
    "$VERSION" "$SHA256" "$(date +%Y-%m-%dT%H:%M:%S%z)" > "$TARGET/.complete"
fi
# The launcher's command runs whichever version `current` names.
mkdir -p "$ROOT/bin"
cat > "$ROOT/bin/datalab" <<'SHIM'
#!/bin/sh
# Runs the DataLab version the launcher opens (named in ../current), or the
# one before (../previous) if that one can't run.
# UTF-8 for Python's own text files and console, whatever the locale.
export PYTHONUTF8=1
root="$(cd "$(dirname "$0")/.." && pwd)"
version="$(head -n 1 "$root/current" 2>/dev/null || true)"
if [ -z "$version" ] || [ ! -x "$root/versions/$version/bin/datalab" ]; then
  echo "DataLab ${version:-(none)} can't be opened; opening the version before it." >&2
  version="$(head -n 1 "$root/previous" 2>/dev/null || true)"
fi
exec "$root/versions/$version/bin/datalab" "$@"
SHIM
chmod +x "$ROOT/bin/datalab"
OLD="$(head -n 1 "$ROOT/current" 2>/dev/null || true)"
if [ -n "$OLD" ] && [ "$OLD" != "$VERSION" ]; then
  printf '%s\n' "$OLD" > "$ROOT/.previous.new" && mv "$ROOT/.previous.new" "$ROOT/previous"
fi
printf '%s\n' "$VERSION" > "$ROOT/.current.new" && mv "$ROOT/.current.new" "$ROOT/current"
DATALAB="$ROOT/bin/datalab"
"$DATALAB" --version

step "4/7 Container images"
"$DATALAB" --profile "$PROFILE" pull-images

step "5/7 Settings and keys"
if [ "$PROFILE" = "practice" ]; then
  echo "Next, DataLab asks for your U-M GPT API key. It's optional for practice: press"
  echo "Enter to skip it. No database password, VPN or GitHub account is needed."
else
  echo "Next, DataLab asks for your U-M GPT API key (and the database password, if"
  echo "your lab uses one)."
fi
echo "Each character shows as *. Press Enter when done. Keys are kept in your macOS Keychain."
if [ -n "$SETTINGS" ]; then
  "$DATALAB" --profile "$PROFILE" setup --settings "$SETTINGS"
else
  "$DATALAB" --profile "$PROFILE" setup
fi

if [ "$PROFILE" = "practice" ]; then
  step "6/7 Setting up the practice database"
else
  step "6/7 The lab's knowledge base and pipelines"
fi
if [ "$PROFILE" = "practice" ]; then
  echo "Skipped: practice DataLab doesn't use the lab's repositories. Instead it runs its"
  echo "own database of made-up data, in Docker, reachable from this computer only."
  echo "The first time, this takes a few minutes; data it already has is kept."
  # Not a reason to stop: DataLab sets it up (or starts it) each time it opens.
  "$DATALAB" --profile "$PROFILE" practice-db setup \
    || echo "It isn't ready yet (the messages above say why). DataLab tries again each time it opens."
elif [ "$GITHUB" = "no" ]; then
  echo "Skipped. Sign in later in DataLab, under Settings → GitHub."
elif ask "Sign in to GitHub now, to download them? [Y/n]"; then
  # 2: not set up here (the lab's settings don't name the repos); it says so.
  # 1: not signed in, or no access to a repo; it says whom to ask.
  signed=0
  "$DATALAB" --profile "$PROFILE" github sign-in || signed=$?
  if [ "$signed" -ne 2 ]; then
    "$DATALAB" --profile "$PROFILE" repos sync \
      || echo "You can sync again later in DataLab (Knowledge and Pipelines)."
  fi
else
  echo "Skipped. Sign in later in DataLab, under Settings → GitHub."
fi

step "7/7 Launcher"
# Real and practice each have their own app, name and icon, so neither
# replaces the other and they're easy to tell apart.
if [ "$PROFILE" = "practice" ]; then
  NAME="DataLab (practice)"
  BUNDLE="edu.umich.ihs.datalab.practice"
  ICON="DataLab-practice.icns"
else
  NAME="DataLab"
  BUNDLE="edu.umich.ihs.datalab"
  ICON="DataLab.icns"
fi
# Whether a bundle is this profile's DataLab app (one this installer made).
ours() { [ -f "$1/Contents/Info.plist" ] && grep -qF "<string>$BUNDLE</string>" "$1/Contents/Info.plist"; }
# In /Applications, where people look, if you can add to it without sudo (an
# administrator account can); otherwise in your own Applications folder
# (~/Applications, which Finder, Spotlight and Launchpad also show). Only an
# app of ours is ever replaced: something else called "$NAME.app" is left alone.
# Makes $1/$NAME.app ready for a new copy: 0 when it is, 1 when what's there
# isn't ours, 2 when ours couldn't be replaced (open, or not ours to change).
prepare() {
  target="$1/$NAME.app"
  if [ -L "$target" ]; then return 1; fi
  if [ -e "$target" ]; then
    ours "$target" || return 1
    rm -rf "$target" 2>/dev/null || true
    [ ! -e "$target" ] || return 2
  fi
  mkdir -p "$target/Contents/MacOS" "$target/Contents/Resources" 2>/dev/null || return 2
}
APPS=""
if [ -d "$SYSTEM_APPS" ] && [ -w "$SYSTEM_APPS" ] && prepare "$SYSTEM_APPS"; then
  APPS="$SYSTEM_APPS"
else
  placed=0
  prepare "$USER_APPS" || placed=$?
  case "$placed" in
    0) APPS="$USER_APPS" ;;
    1) echo "$USER_APPS/$NAME.app is another app, not DataLab's, so it was left alone."
       echo "Move or rename it, then run this installer again (DataLab itself is installed)."
       exit 1 ;;
    *) echo "$USER_APPS/$NAME.app couldn't be replaced. Quit DataLab if it's open (or drag"
       echo "the app to the Trash), then run this installer again (DataLab itself is installed)."
       exit 1 ;;
  esac
fi
APP="$APPS/$NAME.app"
# The icon comes with the package. The app keeps its own copy, so an update
# that later removes this version's folder doesn't take the icon with it.
ICONFILE=""
for found in "$TARGET"/lib/python*/site-packages/datalab/branding/"$ICON"; do
  if [ -f "$found" ] && cp "$found" "$APP/Contents/Resources/DataLab.icns"; then ICONFILE="DataLab"; fi
done
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE</string>
  <key>CFBundleExecutable</key><string>DataLab</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>$ICONFILE</string>
</dict></plist>
PLIST
# DataLab runs in a Terminal window, so it's easy to see it's running and to quit
# (close the window or press Ctrl-C). It runs bin/datalab, never a version's own
# folder: that opens the version `current` names, which is what an update
# switches, so the app (and the Desktop shortcut to it) keeps working.
# The path goes to AppleScript as an argument, single-quoted for this script
# ('\'' for a quote, as in /Users/o'brien), and AppleScript quotes it for
# Terminal's shell with `quoted form of`.
QUOTED="$(printf '%s' "$DATALAB" | sed "s/'/'\\\\''/g")"
cat > "$APP/Contents/MacOS/DataLab" <<LAUNCH
#!/bin/sh
exec osascript - '$QUOTED' <<'OSA'
on run argv
  tell application "Terminal"
    activate
    do script (quoted form of item 1 of argv) & " --profile $PROFILE serve"
  end tell
end run
OSA
LAUNCH
chmod +x "$APP/Contents/MacOS/DataLab"
touch "$APP" # so Finder picks up the icon
echo "Added $NAME to $APPS."
# An earlier installer's copy in the other Applications folder would be a
# second, stale "$NAME".
for other in "$USER_APPS/$NAME.app" "$SYSTEM_APPS/$NAME.app"; do
  if [ "$other" != "$APP" ] && [ ! -L "$other" ] && ours "$other"; then
    if rm -rf "$other" 2>/dev/null; then
      echo "(Removed the copy an earlier installer put in $(dirname "$other").)"
    else
      echo "(An earlier copy is still in $(dirname "$other"); you can drag it to the Trash.)"
    fi
  fi
done
# A shortcut on the Desktop: a link to the app. One already there is replaced
# only if it's a link to this app, in either Applications folder.
DESKTOP_LINK=""
LINK="$HOME/Desktop/$NAME"
if [ -d "$HOME/Desktop" ]; then
  if [ -L "$LINK" ] || [ ! -e "$LINK" ]; then
    to="$(readlink "$LINK" 2>/dev/null || echo "$APP")"
    if [ "$to" = "$USER_APPS/$NAME.app" ] || [ "$to" = "$SYSTEM_APPS/$NAME.app" ]; then
      if rm -f "$LINK" 2>/dev/null && ln -s "$APP" "$LINK" 2>/dev/null; then
        DESKTOP_LINK="$LINK"
        echo "Added a shortcut to $NAME on your Desktop."
      else
        echo "A Desktop shortcut couldn't be added (macOS may not let Terminal use the Desktop)."
      fi
    else
      echo "Your Desktop already has a shortcut called $NAME to something else; it was left alone."
    fi
  else
    echo "Your Desktop already has something called $NAME; it was left alone."
  fi
fi

step "Done"
echo "$NAME is installed."
echo "  The app:           $APP"
if [ -n "$DESKTOP_LINK" ]; then echo "  Desktop shortcut:  $DESKTOP_LINK"; fi
echo "  Program files:     $ROOT"
echo "Open it with the Desktop shortcut, from Applications in Finder, or with Spotlight"
echo "(Cmd-Space, then type $NAME). It opens a Terminal window, then your browser."
echo "(Or run: \"$DATALAB\" --profile $PROFILE serve)"
# Only with someone at the keyboard: not when this runs from a pipe or a script.
if [ -t 0 ] && [ -t 1 ]; then
  if ask "Show $NAME in Finder? [Y/n]"; then open -R "$APP" || true; fi
  if ask "Open $NAME now? [Y/n]"; then open "$APP" || true; fi
fi
