#!/usr/bin/env bash
# Refresh this folder's authoring kit (skill + reference docs + wrappers)
# from the installed CanvasQuartoSync.
#
# Only the kit is touched: your content, config.toml, and any edits you made
# to CLAUDE.md are left alone.
set -u

PYTHON="@@PYTHON@@"
TOOL_DIR="@@REPO@@"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -x "$PYTHON" ] && ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "[update_kit] Python not found at: $PYTHON"
    echo "[update_kit] The tool may have moved. Re-scaffold with:"
    echo "    python init_content_project.py \"$HERE\" --update"
    exit 2
fi

exec "$PYTHON" "$TOOL_DIR/init_content_project.py" "$HERE" --update
