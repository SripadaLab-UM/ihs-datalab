#!/bin/sh
# DataLab uninstaller for macOS.
#
#   sh uninstall.sh [--delete-data | --keep-data]
#
# Removes DataLab (every version installed side by side), its launcher, its
# container images, and the keys it saved in your Keychain. It asks before
# deleting DataLab's data folder (conversations and query results). It never
# touches your export folders, uv, or Docker Desktop.
set -eu
export PATH="$HOME/.local/bin:$PATH"
ROOT="${DATALAB_INSTALL_DIR:-$HOME/Library/Application Support/DataLab/app}"

if [ -x "$ROOT/bin/datalab" ] && [ -f "$ROOT/current" ]; then
  "$ROOT/bin/datalab" uninstall "$@"
  rm -rf "$ROOT"
elif command -v uv >/dev/null 2>&1 && [ -x "$(uv tool dir --bin)/datalab" ]; then
  # Installed by an installer from before versions went side by side.
  "$(uv tool dir --bin)/datalab" uninstall "$@"
else
  echo "DataLab's program files weren't found; skipping that part."
fi
# The older installers' copy, if there is one.
if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^datalab '; then
  uv tool uninstall datalab
fi
rm -rf "$HOME/Applications/DataLab.app" "$HOME/Applications/DataLab (practice).app"
echo "DataLab has been removed."
