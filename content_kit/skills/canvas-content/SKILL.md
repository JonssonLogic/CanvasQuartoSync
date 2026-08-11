---
name: canvas-content
description: >-
  Authoring course content for CanvasQuartoSync - Canvas pages, assignments,
  quizzes (Classic and New Quizzes), study guides, module structure, and
  calendar events, written locally as Quarto .qmd/.json files and later synced
  to Canvas. Use whenever creating or editing course material in this folder:
  it defines the required NN_ file naming, the canvas.* frontmatter schema for
  every content type, the quiz syntax, and how links and images are handled.
when_to_use: >-
  Triggers include: add or edit a page, assignment, quiz, module, study guide,
  syllabus, or course PM; embed a video or image; link a PDF; set a due date,
  points, or attempts; "why isn't this showing up in Canvas".
allowed-tools: Bash(check_content.bat:*), Bash(./check_content.sh:*), PowerShell(check_content.bat:*)
---

# Authoring content for CanvasQuartoSync

This folder is a **course source tree**. A separate tool (CanvasQuartoSync) renders
these files with Quarto and pushes them to Canvas. Your job is to write the files
correctly; the developer runs the sync.

## Rules of engagement

1. **Never sync.** Do not run `sync_to_canvas.py`, `import_from_canvas.py`,
   `purge_course.py`, `run_sync_here.bat`, or anything else that contacts Canvas.
   Pushing to a live course is always the developer's call. Write files and stop.
2. **Match the scope of the request.** "Add a video to the welcome page" is one edit.
   "Draft the statics module" may be several files. Don't create files nobody asked
   for, and don't restructure existing content as a side effect.
3. **Validate what you touched**, then report. See below.
4. **Don't guess at behaviour.** If something isn't covered in this skill or its
   reference files, say so and append a line to `.claude/kit-gaps.md` in this folder
   describing what was missing. That file gets fed back into the tool's docs.

## Validate before you report

After writing or editing any content file, run the checker on it:

```
check_content.bat 01_Introduction/02_Welcome.qmd     # Windows
./check_content.sh 01_Introduction/02_Welcome.qmd    # macOS/Linux
```

It runs offline - no Canvas, no credentials, about a second. It reports what each
file **will become in Canvas**, and catches the mistakes that otherwise only surface
after a sync: missing `NN_` prefixes, misspelled settings, invalid dates, broken image
paths, quiz questions that won't grade. Fix every `ERROR` before reporting back.
Pass a folder instead of a file to check everything.

If the wrapper is missing or fails to run, say so rather than skipping the check.

## What you write, and what it becomes

The tool decides what a file becomes from **the file extension plus the `canvas.type`
key in its frontmatter**. Get this wrong and the file either syncs as the wrong thing
or is silently ignored.

| You write | Becomes in Canvas | Choose it when |
|---|---|---|
| `.qmd` + `type: page` | Wiki Page | Any read-only material: notes, instructions, resources |
| `.qmd` + `type: assignment` | Assignment | Students submit something or it needs a grade/due date |
| `.qmd` + `type: study_guide` | Page **and** a PDF file | Course PM / syllabus that must also exist as a downloadable PDF. Emits a second artefact into another module |
| `.qmd` + `type: subheader` | Text header in the module | A visual divider between items. No page is created |
| `.qmd` + `type: external_url` | External URL module item | Linking off-site. Body of the file is ignored; only frontmatter matters |
| `.qmd` + `type: new_quiz` | Quiz (New Quizzes engine) | **Default choice for quizzes.** Required for numeric and formula questions |
| `.qmd` + `type: quiz` | Quiz (Classic engine) | Only when you specifically need Classic behaviour (`quiz_type`, `description_file`) |
| `.json` + `"quiz_engine": "new"` | Quiz (New Quizzes engine) | Compact quizzes with no images or rich answers |
| `.json` with a `questions` array | Quiz (Classic engine) | As above, Classic |
| `.pdf`, `.csv`, `.zip`, … | Uploaded file + module item | Handouts and datasets. No frontmatter involved |
| `schedule.yaml` | Calendar events | Only synced when the developer passes `--sync-calendar` |

**The two choices that are expensive to get wrong:**

- **Classic vs New Quizzes.** Classic *cannot* do numeric or formula questions - it
  will sync without complaining and simply lose them. New Quizzes is assignment-backed
  and measures `time_limit` in **seconds**; Classic measures it in **minutes**. When in
  doubt use `new_quiz`.
- **Page vs study guide.** `study_guide` renders twice (HTML + PDF) and drops the PDF
  into a *different* module. Don't use it for an ordinary page - it needs LaTeX
  installed and will fail the PDF half without it.

Note: a `.qmd` whose filename contains `studyguide` or `kurspm` is treated as a study
guide **even without** `type: study_guide`. Avoid those words in ordinary page filenames.

## Naming: `NN_` prefixes are mandatory

- **Folders** = modules: `01_Introduction`, `02_Statics`. The `NN_` sets module order
  and is stripped from the Canvas name.
- **Files** = module items: `01_Welcome.qmd`, `02_Lab.qmd`. Same rule.
- **Anything without an `NN_` prefix is silently ignored by the sync** - no warning.
  This is the single most common reason content "doesn't show up in Canvas".
- Exceptions that *should not* have a prefix: images, `branding.css`, `config.toml`,
  and quiz `description_file` targets (a prefix would make them sync as their own page).
- A file at the content root syncs to Canvas but is **not** added to any module.

## Skeletons

A page - note `title:` is top-level, **not** under `canvas:`:

```yaml
---
title: "Welcome to the Course"
format:
  html:
    page-layout: article
canvas:
  type: page
  published: true
  indent: 0
---

Body content in normal markdown.
```

An assignment:

```yaml
---
title: "Lab 1: Truss Analysis"
canvas:
  type: assignment
  published: true
  points: 20
  due_at: 2026-03-15T23:59:00Z
  submission_types: [online_upload]
  allowed_extensions: [pdf, zip]
---

Instructions here.
```

Quizzes put their title **under `canvas:`** instead - see `reference/quizzes.md`.

## Reference files

Load these as needed; don't read them all up front.

| File | Load it when |
|---|---|
| `reference/frontmatter.md` | You need a setting that isn't in the skeletons above: dates, grading, group work, publishing, indentation, result visibility |
| `reference/quizzes.md` | Anything involving quizzes - question syntax, answer styles, per-answer feedback, numeric and formula questions, Classic vs New settings |
| `reference/recipes.md` | Embedding video, images, downloadable files, cross-links, callouts, tables, math. **Read this before hand-writing HTML** |
| `reference/linking.md` | How links and assets resolve: uploads, cross-links between content, orphan cleanup |
| `reference/study-guide.md` | Building a Course PM / syllabus with the HTML+PDF dual output and the preprocessor |
| `reference/calendar.md` | `schedule.yaml` events and recurring series |
| `reference/gotchas.md` | Something behaves unexpectedly, or before doing anything involving published quizzes, front pages, or gradebook settings |

## Reporting back

When you finish, tell the developer:
- which files you created or changed, and what each becomes in Canvas;
- the validator result;
- anything you had to assume, and anything worth eyeballing before they sync.

Then stop. Do not sync.
