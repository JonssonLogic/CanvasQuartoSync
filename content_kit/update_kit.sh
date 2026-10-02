#!/usr/bin/env bash
# Refresh this folder's authoring kit (skill + reference docs + wrappers)
# from the installed CanvasQuartoSync.
#
# Only the kit is touched: your content, config.toml, and any edits you made
# to CLAUDE.md are left alone.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Locate the tool and its Python. CANVAS_QUARTO_SYNC_DIR (a dev clone with a
# .venv inside) wins and must be valid; otherwise the install location, then
# the two older layouts.
try() {
    if [ -z "${TOOL_DIR:-}" ] && [ -f "$1/sync_to_canvas.py" ] && [ -x "$2" ]; then
        TOOL_DIR="$1"; PYTHON="$2"
    fi
}
find_tool() {
    TOOL_DIR=""
    if [ -n "${CANVAS_QUARTO_SYNC_DIR:-}" ]; then
        try "$CANVAS_QUARTO_SYNC_DIR" "$CANVAS_QUARTO_SYNC_DIR/.venv/bin/python"
        if [ -z "$TOOL_DIR" ]; then
            echo "[update_kit] CANVAS_QUARTO_SYNC_DIR is set to \"$CANVAS_QUARTO_SYNC_DIR\","
            echo "[update_kit] but there is no sync_to_canvas.py and .venv/bin/python there."
            return 1
        fi
        return 0
    fi
    if [ "$(uname)" = "Darwin" ]; then
        APP_DIR="$HOME/Library/Application Support/CanvasQuartoSync"
    else
        APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/canvasquartosync"
    fi
    try "$APP_DIR" "$APP_DIR/.venv/bin/python"
    try "$HOME/CanvasQuartoSync" "$HOME/.venvs/canvas_quarto_env/bin/python"
    try "$HOME/venvs/canvas_quarto_env/CanvasQuartoSync" "$HOME/venvs/canvas_quarto_env/bin/python"
    [ -n "$TOOL_DIR" ] && return 0
    echo "[update_kit] CanvasQuartoSync not found. Looked in:"
    echo "    $APP_DIR"
    echo "    $HOME/CanvasQuartoSync"
    echo "    $HOME/venvs/canvas_quarto_env/CanvasQuartoSync"
    echo "[update_kit] Install it with install.sh, or set CANVAS_QUARTO_SYNC_DIR to a clone."
    return 1
}

find_tool || exit 2

exec "$PYTHON" "$TOOL_DIR/init_content_project.py" "$HERE" --update
