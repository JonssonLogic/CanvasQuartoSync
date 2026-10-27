# Changelog

What changed in each release, for the people using it. Earlier releases are described
on the [GitHub releases page](https://github.com/JonssonLogic/CanvasQuartoSync/releases).

## Unreleased

### `cqs ics`: the schedule in Outlook

- **`cqs ics`** writes `schedule.yaml` as an iCalendar file (`<course_code>.ics`).
  **`cqs ics --publish`** also puts it on a secret GitHub gist and prints an address that
  never changes. Subscribe to it once in Outlook, Google or Apple Calendar; after that,
  editing `schedule.yaml` and publishing again is the whole update.
- Event ids are stable, so an unchanged event stays the same event, and a moved or removed
  one moves or disappears in the subscribed calendar. No duplicates, unlike the Canvas
  calendar sync.
- Optional `[ics]` table in `config.toml`: `name`, `file`, and `gist` (written by the first
  publish).

### Preview: comments that work, and MDViewer's rendering

- **Comment highlights show up** even when the commented text spans bold, links, math or
  table cells. Comments are anchored in the source instead of searched for in the
  rendered page, so Hide/Show comments has something to toggle.
- A comment popup and a comment panel, which also lists orphaned comments (whose text
  is gone). Right-click an entry for *Go to comment* and *Delete comment*.
- Comments on links open with a right-click; a left click inside a link follows it.
- Saving a comment goes through the open editor, so it is not lost when the file has
  unsaved changes. A comment whose context contains `-->` no longer breaks the comment
  block.
- From MDViewer: definition lists, LaTeX in figure captions, the Swedish "AI:n" fix for
  directives, and a highlighted raw source view.

## 0.15.0 - 2026-10-21

### Course Board, and module order without `NN_` prefixes

The VS Code panel *Module Structure* is now the **Course Board**, the main place to work
on a course. A course's module order no longer has to live in its file names.

#### Order files

- New courses list their modules in **`_modules.yml`** at the course root, one folder
  per line, top to bottom. `- Folder: Name in Canvas` gives a module a different name.
- Each module folder can have an **`_order.yml`** listing its items in order.
- A folder is a module only if `_modules.yml` lists it, so `graphics/` and drafts stay out.
- Inside a module, a file with a `canvas.type` (or a quiz) is included without being
  listed, and goes last. A new page needs no bookkeeping. PDFs, plain notes and images
  are synced only when `_order.yml` lists them, so a quiz description file can no longer
  turn into a page by accident. Names starting with `_` stay out unless listed.
- File and folder names are plain: `Welcome.qmd`, not `01_Welcome.qmd`. Reordering never
  renames anything and never breaks a link.
- **Several teachers**: both files are one entry per line, with one order file per
  module, so people working in different modules never touch the same file. The files
  are marked `merge=union` in `.gitattributes`, so a git merge never stops on them, and
  the tool reads a merged file tolerantly: a duplicate line keeps its first place, an
  entry whose file is gone is skipped with a warning, and unlisted content is added at
  the end.
- Courses with `NN_` prefixes keep working exactly as before.

#### Course Board (VS Code)

- **Drag to reorder** modules (by the handle) and items (by the row), or drag an item
  into another module. This rewrites the order files, and for a move also the file's
  folder, the links that point at it, and its sync map entry. Nothing reaches Canvas
  until the next sync.
- Shows the course **in the order a sync will produce**, with Canvas-only modules and
  items slotted in after what they follow in Canvas.
- **Opens even without Canvas.** A missing `config.toml`, `course_id` or token, a refused
  token, an unknown course or no network used to replace the panel with
  "Python exit 1: ...". Now the local course is shown under a banner that says what is
  wrong and what to do, with **Open config.toml** and **Retry**.
- **Config** button in the header opens `config.toml`, or offers to create one.
- A local module that Canvas does not have yet appears in its place, marked
  *Not in Canvas yet*, with **Create in Canvas ↑**. Every module has **Sync all ↑**.
  (These used to sit in a "Local Only (no Canvas module)" list with no way to sync them.)
- Files in a module folder that the sync skips are listed under the module with
  **+ Add**. Folders with content that are not in the course offer **Add to course**.
- `+ New` items and modules, and imports from Canvas, use plain names and are added to
  the order files in a course that has them.
- An `NN_` course gets a banner and a one-time prompt per session (with *Don't ask
  again*) offering **Preview conversion**.
- The command is *Open Course Board* (`cqs.openCourseBoard`). The old
  `cqs.showModuleStructure` still works, so existing keybindings keep working.

#### Converting a course: `cqs migrate`

- `cqs migrate --dry-run` lists what would change; `cqs migrate` does it. The Course
  Board runs the same thing after showing the preview.
- Removes the `NN_` prefix from module folders and the files directly in them (a name
  that is already taken keeps its prefix), and writes `_modules.yml` and `_order.yml` in
  the current order.
- Rewrites links and paths that point at a renamed file in every text file of the
  course, including `#anchors` and project-root links (`/Folder/file.qmd`).
- Moves sync map entries and drift snapshots with their files, so every Canvas id is
  kept. Canvas sees the same module names and item titles.
- A file that was ignored for lacking a prefix but has a `canvas.type` gets a leading
  `_`, so it stays out of Canvas as before.
- Refuses to run with uncommitted changes, so the conversion is one commit of its own
  (`--allow-dirty` overrides). Local only; never contacts Canvas.

#### Sync

- **Modules are placed in Canvas in local order.** Before, a module created by a sync
  stayed wherever Canvas appended it. Modules that exist only in Canvas keep their place.
  This applies to `NN_` courses too.
- **An item moved to another module folder leaves its old module.** It is added to the
  new module and taken out of the old one, so a move does not leave a duplicate. Only
  the course's own modules are touched, and only pages, assignments, quizzes and files.
  This applies to `NN_` courses too: if a page was also added by hand to a second module
  of the course, the next sync removes that copy.
- Syncing one file into a module Canvas does not have yet creates the module in its
  place, not at the end.
- `--module-structure` always prints a document. Without a Canvas connection it holds
  the local course and a `problems` list instead of exiting with 1.

#### Checking content

- `cqs check` says why a file with `canvas:` metadata would be skipped: a folder not in
  `_modules.yml`, a leading `_`, a missing `NN_` prefix in an older course.
- Problems in `_modules.yml` and `_order.yml` are reported: entries whose file is gone,
  git conflict markers, a file that is not a list.
- An `NN_` course gets a note (not a warning) at the end of `cqs check` pointing to
  `cqs migrate`.

#### New courses and imports

- `cqs init` starts a course with an empty `_modules.yml` and the `.gitattributes`
  lines. The example module is `Introduction/Welcome.qmd`.
- `cqs import` into a new folder writes order files. Importing into a folder that
  already holds an `NN_` course keeps using prefixes.

#### For AI assistants

- The `canvas-content` skill and the course `CLAUDE.md` describe both layouts and how to
  tell them apart, and tell assistants not to run `cqs migrate` unless asked. Run
  `cqs kit update` in a course folder to get them.
