#!/bin/sh
# DataLab uninstaller for macOS.
#
#   sh uninstall.sh [--delete-data | --keep-data]
#
# Removes DataLab, its launcher, its container images, and the keys it saved in
# your Keychain. It asks before deleting DataLab's data folder (conversations and
# query results). It never touches your export folders, uv, or Docker Desktop.
set -eu
export PATH="$HOME/.local/bin:$PATH"

if command -v uv >/dev/null 2>&1 && [ -x "$(uv tool dir --bin)/datalab" ]; then
  "$(uv tool dir --bin)/datalab" uninstall "$@"
  uv tool uninstall datalab
else
  echo "DataLab isn't installed with uv; skipping that part."
fi
rm -rf "$HOME/Applications/DataLab.app"
echo "DataLab has been removed."
