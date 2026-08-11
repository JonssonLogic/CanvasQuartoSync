#!/usr/bin/env bash
# Offline content validation for this course folder.
# Paths are stamped in by init_content_project.py - re-run it with --update
# if the tool moves.
set -u

PYTHON="@@PYTHON@@"
TOOL_DIR="@@REPO@@"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -x "$PYTHON" ] && ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "[check_content] Python not found at: $PYTHON"
    echo "[check_content] Re-run init_content_project.py --update from the tool folder."
    exit 2
fi

if [ "$#" -eq 0 ]; then
    exec "$PYTHON" "$TOOL_DIR/validate_content.py" "$HERE" --content-root "$HERE"
else
    exec "$PYTHON" "$TOOL_DIR/validate_content.py" "$@" --content-root "$HERE"
fi
