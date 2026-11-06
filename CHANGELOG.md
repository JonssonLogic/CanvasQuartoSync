# Changelog

What changed in each release, for the people using it. Earlier releases are described
on the [GitHub releases page](https://github.com/JonssonLogic/CanvasQuartoSync/releases).

## Unreleased

### One folder, a sandbox and a live course

- **Name your courses in `config.toml` and switch with one word**: `course_id = "live"`
  with a `[courses]` table (`live = 17671`, `training = 74`), or `--course-id training`
  for one run. Each course keeps its own sync map (`.canvas_sync_map.json` for the first,
  `.canvas_sync_map.<id>.json` for the others), so switching never mixes their Canvas ids.
- **"Wrong course" now says how to fix it.** It used to suggest `--force`, which only
  re-renders files and never got past the check. It now shows the `[courses]` table to add.
  A course that is not listed is still refused.
- **The Course Board shows "Wrong course" as a banner** instead of "JSON parse error":
  the error was printed on stdout in front of the board's JSON.
- **Renames and moves update every course's map**, from the Course Board and from a sync.
- New course folders gitignore `.canvas_sync_map*.json`, so every course's map stays out
  of a solo course's repo. Team mode commits and pushes all of them.

### The sync keeps `_order.yml` in step with the folder

- **Every sync now updates each module's `_order.yml`**, also a one-file sync from the
  Course Board: new content files are listed
  last, lines for files that are gone are removed (nothing is deleted in Canvas), and the
  sync logs each change. Lines for files that still exist are never touched, so a manual
  order survives. `PDFs and notes` are only kept if you listed them yourself.
- **A rename keeps its place and its Canvas id**: one dead line plus one new file with the
  same extension is treated as a rename, so Canvas gets an update and not a duplicate.
  Anything less clear is not guessed at. Fixes #55.
- **Syncing a file by its old name syncs the renamed file.** The Course Board shows
  `_order.yml`, so right after a rename it asked for the old name and the sync failed
  with "File not found".
- **In team mode, the `_order.yml` files a sync changed are committed and pushed** with
  the sync map, instead of waiting for the next sync.

### Fixes

- **Course Board actions are committed and pushed in team mode.** Deleting, importing,
  publishing or creating a module changed the local files but left them for you to
  commit and push. Each now runs like a sync: it commits and pulls first, holds the sync
  lock, and commits and pushes what it changed ("Deleted by Mirza: Test_page.qmd").
- **A quiz students have answered is no longer doubled.** Canvas locks the questions of
  a New Quiz once it has submissions. The sync then created a copy of every question and
  left the old one, so the quiz had each question twice. A locked question is now left
  as it is, and the sync warns once and names the locked ones.
- **An imported file is not uploaded again.** Importing a file from Canvas (Course Board
  or `cqs import`) did not record it in the sync map, so the next sync uploaded a second
  copy and added it to the module next to the original.
- **External links without `https://` are no longer added again on every sync.** Canvas
  stores `mechanics.ju.se` as `http://mechanics.ju.se`, and the sync compared the raw
  strings, so it never found the existing link.
- **Imported file names are decoded**: `L1+Fj%C3%A4drar.pdf` is saved as `L1 Fjädrar.pdf`.

### Tooling

- **`cqs --version` shows which build runs**: a build from `dev-deploy.ps1` reads
  `0.16.0+b6eb056 (feat/x)`, with `modified` if it had uncommitted changes. A release
  install shows the bare version.
- **`cqs` works in VS Code terminals without restarting VS Code.** The extension puts
  the install's `bin` folder on PATH for its terminals. `update.ps1` also adds it to the
  user PATH, as `install.ps1` does, and `dev-deploy.ps1` no longer stops on Node's
  deprecation warning when installing the extension.

## 0.16.0 - 2026-11-03

### Renders no longer happen in the course folder

- **Quarto renders in a mirror of the course under the temp folder**, not next to the
  source. Dropbox, OneDrive and VS Code no longer see the `tmp-*` files, so they cannot
  lock them, and nothing is left behind in the course when a render fails. The mirror is
  hard links (instant, no extra space), made once per sync and removed at the end. If it
  cannot be made, the render happens in place as before.
- **A PDF that Quarto wrote is used even if Quarto then exits with an error.** Quarto
  fails when only its cleanup cannot delete a locked folder (os error 32), after the PDF
  is finished; that used to throw the PDF away.
- **A study guide whose PDF fails to render or upload keeps its old PDF in Canvas.** The
  unused-file cleanup at the end of the sync used to delete it, leaving the course with no
  PDF until the next successful render.

### Team mode: no committing by hand

- **The sync commits your changes itself**, new and deleted files included, then pulls
  your colleagues' work in under yours and pushes. It used to refuse to run with
  uncommitted or unpushed changes. The only thing that stops it now is a **merge
  conflict**: it names the files, keeps your commit on your computer, and syncs nothing.
- **Leftover changes to the sync map after a sync are gone.** Uploaded files record when
  they were synced, and the Course Board no longer writes the map when it refreshes.
- **Deleting a file in the Course Board removes it from the module's `_order.yml`**, so
  the board no longer warns that the order file lists a file that does not exist.
- **A study guide's PDF shows with its guide** in the Course Board instead of as "Not in
  order". The sync no longer records the rendered PDF as a source file of its own.

### Course Board: the publish toggle sticks ([#48](https://github.com/JonssonLogic/CanvasQuartoSync/issues/48))

- **Publish/Draft on an item now writes `canvas.published` into its file** (`.qmd` or
  `.json` quiz), as well as changing Canvas. Before, only Canvas changed, and the next
  Sync quietly put the old state back. Only that one line in the frontmatter is
  touched; comments and key order stay. A study guide's PDF row writes
  `canvas.pdf.published`.
- **PDFs, ZIPs and other plain files** are published when they are first added, and after
  that a sync leaves their state alone. Unpublishing a handout in the board lasts.
- A sync that changes an item's published state says so in the log
  (`Published: …` / `Unpublished: …`).

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

### Courses in a subfolder, and YH courses

- **The Course Board opens on a course in a subfolder.** It used to read `config.toml`
  from the workspace root only. It now takes the course of the active file, else the
  root, else the one course under it, and asks when there are several.
- **Sync this file works from any workspace.** It uses the nearest folder above the file
  that holds a `config.toml`, the same rule as `cqs`. Sync All uses the active file's
  course.
- **Study guides for yrkeshögskola courses**: `yh = true` in `config.toml` (or
  `canvas.preprocessor: yh` in the guide) gives Yh-poäng on the cover, IG/G/VG grade
  cards taken from the table header, and a teacher table with optional photo, link and
  e-mail.

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
