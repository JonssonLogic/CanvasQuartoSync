# E2E Manual Verification Checklist

Most rendering is now checked **automatically** by `tests/e2e/test_full_sync.py`
via API read-back — math rendered to Canvas equation images (and each image is
HTTP-fetched to confirm it actually renders), callouts styled, code highlighted,
tables present, no raw markdown leaking, cross-links resolved, indentation,
`new_tab`, unpublished state, assignment dates/grading/group, and quiz
question types/settings.

This list is only the handful of things automation **can't** see. The E2E run
prints this prompt (with the course URL) at the end. Fixture =
**MECH201 — Mechanics of Materials**.

## Human-only checks
- [ ] **Course PM (PDF)** (Course Documents module) opens and is a properly
      formatted PDF — LaTeX, math, and tables look correct on the page.
- [ ] **New Quizzes** open and render in the New Quizzes UI:
      *Beam Bending Concepts*, *Beam Bending Calculations* (numeric + formula
      questions show their values/variables), *Section Properties (JSON)*.
      The API can't verify the New Quizzes UI, so eyeball these.
- [ ] **Overall branding / layout** looks right (colours, headings, spacing) on
      the front page and a couple of content pages.
- [ ] **Styled table** on *Stress, Strain & Material Behaviour* (Load Cases):
      light blue-grey stripes on rows 1 and 3, grey lines around every cell, a
      dark blue header with white text. The material table above it looks as it
      always has. Hovering a row shows **no** highlight; that is Canvas's own
      `.hover` and expected (see LESSONS_LEARNED.md, tables).

## Optional spot-checks
- [ ] Click a cross-link on the Welcome page (e.g. "Truss Analysis assignment")
      and confirm it lands on the correct Canvas item.
- [ ] The Course PM front page renders as the course home page.

> Calendar events are **not** created by the E2E run (it doesn't pass
> `--sync-calendar`); only check the calendar if you ran a calendar sync.
