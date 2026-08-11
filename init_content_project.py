"""
Scaffold a course content folder with the AI authoring kit.

Copies the Claude Code skill, its reference documentation, and the offline
validator wrappers into a content folder, so a fresh AI session opened on that
folder knows how to write CanvasQuartoSync content without reading this repo's
source.

Usage:
    python init_content_project.py C:\\Courses\\MECH201             # scaffold
    python init_content_project.py C:\\Courses\\MECH201 --update    # refresh kit
    python init_content_project.py . --with-example                 # + sample module

The wrappers are stamped with absolute paths derived from the interpreter that
runs this script (``sys.executable``) and this file's location, so the venv can
live anywhere and be called anything.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from handlers import __version__

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
KIT_SRC = os.path.join(REPO_ROOT, "content_kit")
STAMP_FILE = os.path.join(".claude", "canvas-kit.json")

# Files copied verbatim into the target root, only when absent.
_STARTER_FILES = {
    "config.toml": "config.toml",
    "_quarto.yml": "_quarto.yml",
    "branding.css": "branding.css",
    "gitignore.template": ".gitignore",
}

_WRAPPERS = ["check_content.bat", "check_content.sh", "update_kit.bat", "update_kit.sh"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, text, executable=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if executable and os.name != "nt":
        os.chmod(path, 0o755)


def verify_interpreter(python_exe):
    """Confirm the interpreter that will be stamped can import the tool's deps."""
    try:
        result = subprocess.run(
            [python_exe, "-c", "import frontmatter, yaml"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"could not run {python_exe}: {e}"
    if result.returncode != 0:
        return False, (
            f"{python_exe} cannot import the tool's dependencies.\n"
            f"        Install them with:  \"{python_exe}\" -m pip install -r "
            f"{os.path.join(REPO_ROOT, 'requirements.txt')}"
        )
    return True, ""


def load_stamp(target):
    path = os.path.join(target, STAMP_FILE)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def kit_status(content_root):
    """Return a warning string if the folder's authoring kit is out of date.

    Returns None when there is no kit, or when it matches this tool version.
    Called by sync_to_canvas.py so a stale kit surfaces at the moment it matters;
    it never blocks or modifies anything.
    """
    stamp = load_stamp(content_root)
    installed = stamp.get("kit_version")
    if not installed or installed == __version__:
        return None
    return (
        f"Authoring kit in this folder is v{installed}, tool is v{__version__}. "
        f"Refresh it by running update_kit.bat here."
    )


def _course_name(target):
    """Best-effort course name from an existing config.toml, for CLAUDE.md."""
    cfg = os.path.join(target, "config.toml")
    if not os.path.exists(cfg):
        return "(set course_name in config.toml)"
    try:
        import tomllib
        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        return data.get("course_name") or "(set course_name in config.toml)"
    except Exception:
        return "(set course_name in config.toml)"


# ---------------------------------------------------------------------------
# Copy steps
# ---------------------------------------------------------------------------

def copy_skill(target, log):
    """Replace the skill directory wholesale - it is tool-owned, never edited."""
    src = os.path.join(KIT_SRC, "skills", "canvas-content")
    dst = os.path.join(target, ".claude", "skills", "canvas-content")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    log.append(f"  skill      .claude/skills/canvas-content/ ({len(os.listdir(os.path.join(dst, 'reference')))} reference files)")


def copy_wrappers(target, python_exe, log):
    for name in _WRAPPERS:
        text = _read(os.path.join(KIT_SRC, name))
        text = text.replace("@@PYTHON@@", python_exe).replace("@@REPO@@", REPO_ROOT)
        _write(os.path.join(target, name), text, executable=name.endswith(".sh"))
    log.append(f"  wrappers   {', '.join(_WRAPPERS)}")


def copy_claude_md(target, stamp, log):
    """Write CLAUDE.md, but never clobber edits the developer made to it."""
    dst = os.path.join(target, "CLAUDE.md")
    text = _read(os.path.join(KIT_SRC, "CLAUDE.md.template"))
    text = text.replace("@@COURSE_NAME@@", _course_name(target))

    if os.path.exists(dst):
        current = _read(dst)
        if _hash(current) == stamp.get("claude_md_hash"):
            _write(dst, text)
            log.append("  CLAUDE.md  refreshed")
        else:
            log.append("  CLAUDE.md  SKIPPED - you have edited it; compare against "
                       "content_kit/CLAUDE.md.template if you want the new version")
            return _hash(current)
    else:
        _write(dst, text)
        log.append("  CLAUDE.md  created")
    return _hash(text)


def copy_starter(target, log):
    written = []
    for src_name, dst_name in _STARTER_FILES.items():
        dst = os.path.join(target, dst_name)
        if os.path.exists(dst):
            continue
        shutil.copyfile(os.path.join(KIT_SRC, "starter", src_name), dst)
        written.append(dst_name)
    if written:
        log.append(f"  starter    {', '.join(written)}")


def copy_example(target, log):
    src = os.path.join(KIT_SRC, "example")
    written = []
    for dirpath, _, filenames in os.walk(src):
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), src)
            dst = os.path.join(target, rel)
            if os.path.exists(dst):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(dirpath, name), dst)
            written.append(rel.replace("\\", "/"))
    if written:
        log.append(f"  example    {', '.join(written)}")


def write_stamp(target, python_exe, claude_hash):
    stamp = {
        "kit_version": __version__,
        "tool_dir": REPO_ROOT,
        "python": python_exe,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "claude_md_hash": claude_hash,
    }
    path = os.path.join(target, STAMP_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stamp, f, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def install(target, python_exe=None, update=False, with_example=False):
    """Scaffold or refresh the kit in ``target``. Returns a log of what happened."""
    target = os.path.abspath(target)
    python_exe = python_exe or sys.executable
    log = []

    if not os.path.isdir(KIT_SRC):
        raise FileNotFoundError(f"content_kit/ not found next to this script: {KIT_SRC}")

    os.makedirs(target, exist_ok=True)
    stamp = load_stamp(target)

    copy_skill(target, log)
    copy_wrappers(target, python_exe, log)
    claude_hash = copy_claude_md(target, stamp, log)

    if not update:
        copy_starter(target, log)
        if with_example:
            copy_example(target, log)

    write_stamp(target, python_exe, claude_hash)
    return log


def main():
    parser = argparse.ArgumentParser(
        description="Scaffold a course content folder with the AI authoring kit."
    )
    parser.add_argument("target", help="Content folder to scaffold (created if missing).")
    parser.add_argument("--update", action="store_true",
                        help="Refresh the kit only; leave content, config.toml, and "
                             "starter files alone.")
    parser.add_argument("--with-example", action="store_true",
                        help="Also write a sample module to copy from.")
    parser.add_argument("--python",
                        help="Interpreter to stamp into the wrappers "
                             "(default: the one running this script).")
    args = parser.parse_args()

    python_exe = args.python or sys.executable
    ok, problem = verify_interpreter(python_exe)
    if not ok:
        print(f"[init] {problem}")
        return 2

    target = os.path.abspath(args.target)
    action = "Updating kit in" if args.update else "Scaffolding"
    print(f"{action}: {target}")

    try:
        log = install(target, python_exe=python_exe, update=args.update,
                      with_example=args.with_example)
    except Exception as e:
        print(f"[init] Failed: {e}")
        return 1

    for line in log:
        print(line)

    print(f"\nKit version {__version__}, tool at {REPO_ROOT}")
    if not args.update:
        print("\nNext steps:")
        print("  1. Set course_id and course_name in config.toml")
        print(f"  2. Open {target} in VS Code and start Claude Code")
        print("  3. Check content any time with:  check_content.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
