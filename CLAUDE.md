# Claude Code — Project Instructions

Start with [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for architecture and
[TESTING.md](TESTING.md) for the full test guide.

## E2E manual verification (when testing content changes)

The E2E suite (`tests/e2e/`, run with `-m canvas`) auto-checks everything that
can be read back from the Canvas API — including math rendered to equation
images (each image is HTTP-fetched to confirm it renders), callouts/code styled,
tables, dates, grading, indentation, quiz settings, etc. A few things still need
human eyes: the rendered **PDF**, the **New Quizzes UI**, and overall **visual
polish/branding**.

Manual verification is intentionally **advisory** — there is deliberately **no**
pytest sign-off gate.

**Behavioral rule for AI agents:** when you run the E2E suite while testing new
code that affects synced content, after it passes you MUST prompt the developer
to perform the manual checks in
[tests/e2e/MANUAL_CHECKLIST.md](tests/e2e/MANUAL_CHECKLIST.md), wait for their
feedback, and fold their pass/fail into your summary. Do **not** declare a content
change verified on "N passed" alone until the developer has confirmed the visual
items (or explicitly waived them).
