# Team mode: several teachers, one course

How CanvasQuartoSync decides what to publish when more than one person edits a
course, and what it does when someone edits Canvas directly. Introduced in 0.13.0.
It settles #7 in part, #8, #9, #15 and #17; see the end for what is still open.

## The principle

**The git repository holds the truth. Canvas is where it is published.**

Every change goes through the repo. Anyone on the course can sync, but a sync
publishes exactly what is on GitHub, and leaves behind what the next person needs.

```toml
# config.toml
workflow = "team"      # default: "solo", which behaves as before
```

## Two questions per item

Every sync answers two questions for each page, assignment and quiz:

1. **Has the source changed since it was last published?** Has someone edited the `.qmd`?
2. **Has Canvas changed since it was last published?** Has someone edited it in Canvas?

Each needs a record of what was published last time, and **both records have to
be shared**: Kalle's sync must know what Mirza published. Together they give four
cases:

| Source | Canvas | Meaning | Solo | Team |
|:--|:--|:--|:--|:--|
| same | same | Nothing happened | skip | skip |
| changed | same | Normal editing | publish | publish |
| same | changed | Someone edited Canvas | keep Canvas, save a copy, report | same |
| changed | changed | A real conflict | overwrite, after saving a copy | **refuse**, save a copy |

`--accept-drift <file>` (or `all`) publishes over a Canvas edit in either mode.

## What it was like before

- **"Has the source changed?" was answered with the file's mtime.** An mtime is
  a fact about one computer. The same file has a different mtime in every clone,
  and a `git pull` stamps new ones on files whose content did not change. With
  a shared map, two people syncing in turn re-rendered the entire course every
  time, each overwriting the other's mtimes, and the map conflicted on nearly
  every pull.
- **The map was gitignored** (#8), so a colleague's clone had no Canvas ids. Its
  first sync matched on title, created duplicates of anything renamed, and the
  drift check reported every item as clean because it had nothing to compare.
- **Canvas edits were noticed only when the source had also changed**, and were
  then overwritten after a yellow warning. An edit to a page nobody was touching
  went unnoticed. Only page bodies were compared: not assignment settings such as
  due dates, and not quizzes at all.
- **The "what changed" view used local snapshots** that only exist on the machine
  that published.

## How it works now

### 1. A source fingerprint instead of mtime

`source_hash` in the map is a fingerprint of everything that shapes the published
result (`handlers/fingerprint.py`):

- the file's content, with **line endings normalised**, so a Windows checkout
  (CRLF) and a Mac checkout (LF) agree;
- the **local files it references**: images, `{{< include >}}` files, a quiz's
  description file. Replacing an image re-publishes the page that shows it;
- the **course-wide render inputs**: `_quarto.yml`, `branding.css`, `config.toml`.
  Changing these used to sync as a no-op (#30).

Links to other content files are not followed: they become Canvas URLs, so
editing the target does not change the linking page.

Old entries convert themselves. If an entry still has an `mtime` and it matches,
the file is unchanged since it was published from this machine: the fingerprint
is recorded without re-rendering. Otherwise the file renders once.

### 2. One shared map, nothing machine-specific in it

```json
"04_Laborationer/04_Vridlabb.qmd": {
    "id": 191680,
    "source_hash": "9c1e…",
    "canvas_hash": "f3ad…",
    "canvas_kind": "page",
    "last_synced_at": "2026-10-20T14:02:11Z",
    "last_synced_by": "Mirza Cenanovic"
}
```

`canvas_settings_hash` is added for assignments. The map is written sorted, UTF-8
and with LF line endings, so diffs are stable and readable. It changes only when
something is actually published, not on every sync.

An earlier proposal (#8) split the map into a committed part and a local cache
holding `mtime`. Once `mtime` is replaced there is nothing local left to split
off, so the split is not needed.

`last_synced_at` stays shared on purpose: the extension's Module Structure panel
compares it with Canvas's own edit time to show "edited in Canvas after the last
sync". A local value would show your colleague's syncs as Canvas edits.

### 3. Canvas is checked on every sync, not just before a write

The handlers already fetch each Canvas object when it has an id, so comparing it
costs no extra request for pages and assignments. Quizzes cost one request each
for their questions, and only when there is a baseline to compare against.

- **Pages and study guides:** the body, as Canvas stored it.
- **Assignments:** the description, and the settings a person might change in
  Canvas: name, points, due/unlock/lock dates, grading type, submission types,
  allowed extensions and attempts, omit from final grade. `published` is left out,
  because the extension's publish toggle changes it in Canvas on purpose.
- **Quizzes, classic and New:** the settings and every question, written out as
  one canonical text, so they go through the same hash, snapshot and diff code as
  page HTML.

Every baseline is taken from what Canvas returns **after** a publish, never from
what was sent, because Canvas rewrites HTML on save.

### 4. On a Canvas edit: stop and save

The Canvas version is saved to `.canvas_drift/` (gitignored): the HTML, a `.qmd`
conversion for a side-by-side diff, and who edited it and when. For pages Canvas
keeps revisions with the editor's name. Assignments and quizzes only say when.

When this machine has no local snapshot of what was published, the page diff is
recovered from Canvas's page history: the revision whose hash matches the map's
`canvas_hash` is what was published.

Resolving it is a person's decision:

- **Keep the Canvas edit** (the usual case, someone fixed something): bring the
  change into the `.qmd`, commit, sync with `--accept-drift <file>`.
- **Discard it:** sync with `--accept-drift <file>` directly.

A refused conflict makes the sync exit with code 2, so the extension reports it.
A kept edit (source unchanged) is reported but is not a failure.

`cqs diff` (`--check-drift`) now also says which items it **could not** check,
instead of counting them as clean.

### 5. Guards and the lock (team mode only)

Before the sync:

1. The folder is a git repository with an upstream branch, and the map is not
   gitignored.
2. **No uncommitted changes**, untracked content files included: Canvas should
   show what is in the repo.
3. **Take the lock.**
4. **Pull**, fast-forward only.
5. **Nothing unpushed.** This is the consequence of a shared fingerprint that is
   easy to miss. Publish a commit that is not on GitHub, and the next person's
   older copy of that file no longer matches the fingerprint in the map. Their
   sync sees it as a new edit and publishes it, **silently reverting your change
   in Canvas**.

After the sync, always, including after a crash: **commit and push the map**,
rebasing over anything colleagues pushed meanwhile, then release the lock. If the
map cannot be pushed, **the lock is kept**: without the map, the next person would
duplicate whatever this sync created.

**The lock** is a ref on GitHub, `refs/cqs/sync-lock`, pointing at a commit whose
message says who holds it, from which computer, since when. Creating a ref that
already exists is refused by the server, so whoever pushes first wins, with no
extra service. Without it, two people syncing at once would both create any new
page, once each.

A lock older than 30 minutes is probably from a crash; the message says so. Run
the sync again with `--break-lock` to take it over.

## Why not the alternatives

- **Keep mtime and split the map (#8 as proposed).** Solves "different times on
  different computers", but not line endings, dependencies, or Canvas edits. More
  files for less.
- **Record the git commit each file was published from (#9).** The same
  information as a fingerprint, but it ties the tool to git, so it does not work
  for solo courses, and single-file syncs and partial failures get complicated.
- **Render everything and compare with Canvas.** Rendering is the slow part, and
  Canvas rewrites the HTML it receives, so what is sent never exactly matches
  what is stored.

## Setting up a course for team mode

1. Put the course in a private repo (for us: the `jth-crunch` organisation, one
   repo per course run, e.g. `THLK16-26`).
2. Remove `.canvas_sync_map.json` from `.gitignore` and commit it. Add
   `.canvas_drift/` to `.gitignore`.
3. Add a `.gitattributes` with `* text=auto`, so line endings are stored the same
   way for everyone.
4. Set `workflow = "team"` in `config.toml`, commit and push. This changes a
   course-wide render input, so the next sync re-publishes everything once.
5. Each teacher: clone the repo, keep their own Canvas token outside it
   (`canvas_token_path` or `CANVAS_API_TOKEN`), and have a teacher role in the
   Canvas course.

## Still open

- **A personal sandbox course** next to the live one (#7) is not built yet.
- **Deleting items from the extension** updates the map but does not commit it;
  commit the map afterwards in team mode.
- **Same Quarto version for everyone.** Harmless for the checks (they compare
  sources, and Canvas's stored copy), but different versions render slightly
  different HTML for the same `.qmd`.
- **Canvas edits to per-section date overrides** are not compared.
- **Verified against mocks and real git, not yet against a live Canvas course.**
  The quiz fingerprints in particular should be confirmed with the E2E suite
  before a team relies on them: if Canvas returns a field differently on a later
  read, every quiz would show as edited until it is republished with
  `--accept-drift`.
