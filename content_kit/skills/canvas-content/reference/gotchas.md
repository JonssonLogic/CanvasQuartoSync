# Gotchas

Behaviour that surprises people. Worth a look before touching published quizzes, front
pages, or gradebook settings.

## Silent failures

**No `NN_` prefix means the file is invisible to the sync.** No error, no warning - the
sync simply never looks at it. Same for a module folder without a prefix. This is the
first thing to check when content "didn't appear in Canvas".

**Misspelled settings are ignored.** `publish:` instead of `published:` doesn't fail; the
setting just never applies. The validator catches these.

**Numeric and formula questions on the Classic engine vanish.** `type: quiz` with a
`numeric_question` syncs "successfully" and the question is silently lost. Use
`type: new_quiz`.

**Mixing answer styles drops answers.** A question containing any `::: {.answer}` block
uses div answers, and every `- [x]` checklist answer in that question is discarded.

**Untitled callouts arrive unstyled.** Give every callout a `##` heading as its first
line - see recipes.md.

## Dates and the source of truth

Your files are authoritative. **Removing a date key clears that date in Canvas** rather
than leaving the existing value. If a due date should stay, it must stay in the file.

## Titles and renaming

- Renaming a *file* is safe: the sync tracks Canvas IDs in `.canvas_sync_map.json`, so
  the existing Canvas item is updated rather than duplicated.
- Changing a `title:` is also safe - it renames the existing item.
- **Never delete `.canvas_sync_map.json`.** Without it the tool falls back to matching by
  title, and anything renamed since the last sync becomes a duplicate in Canvas.
- Quiz **question** names are matched the same way. Renaming a question makes the old one
  get deleted and a new one created, which discards its statistics.

## Quizzes with student submissions

Canvas refuses to unpublish a Classic quiz once students have submitted. The sync updates
the questions in place, but Canvas won't regenerate its internal snapshot, so it shows a
**"Save It Now"** banner that the developer must click by hand. Nothing to fix in the
content - just flag it when editing a live quiz.

New Quizzes don't have this problem.

## Front pages can't be unpublished

Once a page is the course front page, Canvas rejects any update carrying
`published: false`. The sync detects this and syncs the content anyway, leaving the
published state alone. Setting `published: false` on the front page simply won't take.

## `hide_in_gradebook` has strict rules

Canvas requires `omit_from_final_grade: true` **and** points to be 0 or unset. With
points assigned, the update is rejected outright. The sync sets `omit_from_final_grade`
for you, but it cannot work around the points constraint.

## New Quizzes are assignments

A New Quiz appears in Canvas as an assignment, not a quiz. It shows in the assignment
list and the gradebook accordingly. This is Canvas's design, not a bug.

## Time limits use different units

Classic counts **minutes**; New Quizzes counts **seconds**. `time_limit: 30` is half an
hour on Classic and thirty seconds on New Quizzes.

## Group assignments can block a sync

`group_assignment: true` without a `group_set` name makes the sync stop and **prompt
interactively** for which group set to use, then write the answer back into the file.
Always set `group_set` explicitly to a group set that already exists in the course.

## Asset cleanup deletes unreferenced files

At the end of a sync, anything in `synced-images` / `synced-files` that no content
references is deleted. Removing the last reference to an image removes it from Canvas.
Files uploaded by hand elsewhere in the course are never touched.

## Quarto renders before Canvas sees anything

Content is rendered by Quarto first, and only the `<main>` body reaches Canvas - no CSS,
no scripts. See recipes.md for what that means in practice.
