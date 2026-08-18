# Frontmatter reference

Every `canvas.*` setting, by content type. Settings not listed here are **ignored** by
the sync - a misspelling fails silently, so run the validator.

## Where the title goes

| Content type | Title source |
|---|---|
| page, assignment, study_guide, subheader, external_url | top-level `title:` in the frontmatter |
| quiz, new_quiz | `canvas.title` |

If there's no title at all, the filename with its `NN_` prefix stripped is used.
Putting `title:` at the top level of a quiz file does **not** work - it is ignored.

## Shared by every module item

| Key | Type | Default | Notes |
|---|---|---|---|
| `type` | string | - | Selects the handler. See the table in SKILL.md |
| `published` | bool | `false` (subheaders: `true`) | Visible to students. Quizzes and pages default to unpublished |
| `indent` | int | `0` | Indent level in the module list, `0`-`5` |

## page

| Key | Type | Default | Notes |
|---|---|---|---|
| `front_page` | bool | `false` | Makes this the course home page and sets the course to show it. A front page cannot be unpublished - see gotchas.md |

## assignment

| Key | Type | Default | Notes |
|---|---|---|---|
| `points` | number | `0` | Points possible |
| `due_at` | ISO 8601 | unset | e.g. `"2026-03-15T23:59:00"` (course-local). Removing the key **clears** the date in Canvas |
| `unlock_at` | ISO 8601 | unset | Available from |
| `lock_at` | ISO 8601 | unset | Available until |
| `grading_type` | string | Canvas default | `points`, `percentage`, `pass_fail`, `letter_grade`, `gpa_scale`, `not_graded` |
| `submission_types` | list | `[online_upload]` | `online_upload`, `online_text_entry`, `online_url`, `media_recording`, `student_annotation`, `none`, `external_tool`, `on_paper` |
| `allowed_extensions` | list | `[]` | Only with `online_upload`, e.g. `[pdf, zip]` |
| `omit_from_final_grade` | bool | `false` | Graded but excluded from the final grade |
| `group_assignment` | bool | `false` | Marks this as group work. Without `group_set`, the sync **prompts interactively** and writes your answer back into this file |
| `group_set` | string | unset | Name of an existing Canvas group set. Must already exist in the course |

## study_guide

Renders twice: an HTML Canvas page in its own module, plus a PDF uploaded to another.
See study-guide.md for the preprocessor and required `config.toml` keys.

| Key | Type | Default | Notes |
|---|---|---|---|
| `front_page` | bool | `false` | As for pages |
| `preprocess` | bool | `false` | Expand plain markdown into dual-format HTML/PDF content |
| `pdf` | block | - | Nested settings below |

Nested under `pdf:`

| Key | Type | Default | Notes |
|---|---|---|---|
| `target_module` | string | current module | Module that receives the PDF. Created if missing |
| `filename` | string | `<title>.pdf` | Filename uploaded to Canvas |
| `title` | string | the filename | Module item label |
| `published` | bool | `false` | Whether the PDF item is visible |

## subheader

No settings beyond the shared three. The file body is ignored - only the title,
`published`, and `indent` are used. `.md` files work here as well as `.qmd`.

## external_url

The file body is ignored entirely; only frontmatter is read.

| Key | Type | Default | Notes |
|---|---|---|---|
| `url` | string | **required** | Full URL including `https://` |
| `new_tab` | bool | `false` | Open in a new browser tab |

## quiz (Classic engine)

| Key | Type | Default | Notes |
|---|---|---|---|
| `title` | string | filename | Quiz title |
| `quiz_type` | string | `practice_quiz` | `practice_quiz`, `assignment` (graded), `graded_survey`, `survey` |
| `description` | string | unset | Inline HTML/text intro |
| `description_file` | string | unset | Path to a `.qmd` rendered as the description. **Must not** have an `NN_` prefix |
| `due_at` / `unlock_at` / `lock_at` | ISO 8601 | unset | As for assignments |
| `shuffle_answers` | bool | `false` | Randomise answer order |
| `show_correct_answers` | bool | Canvas default | Classic only |
| `allowed_attempts` | int | `1` | `1` = single, `-1` = unlimited, `N` = N attempts |
| `time_limit` | int | unset | **Minutes** on Classic |
| `one_question_at_a_time` | bool | `false` | Show one question per screen |
| `cant_go_back` | bool | `false` | No effect unless `one_question_at_a_time` is true |
| `access_code` | string | unset | Password required to start |

## new_quiz (New Quizzes engine)

Accepts every shared quiz key above (`title`, dates, `shuffle_answers`,
`allowed_attempts`, `time_limit`, `one_question_at_a_time`, `cant_go_back`,
`access_code`) with one difference: **`time_limit` is in seconds**, not minutes.
`quiz_type`, `description`, `description_file`, and `show_correct_answers` are
Classic-only and do nothing here.

| Key | Type | Default | Notes |
|---|---|---|---|
| `quiz_engine` | string | - | JSON files only: set to `new` to select this engine |
| `points` | number | unset | Total points possible |
| `instructions` | string | unset | Shown before the quiz starts |
| `shuffle_questions` | bool | `false` | Randomise question order |
| `calculator_type` | string | `none` | `none`, `basic`, `scientific` |
| `score_to_keep` | string | `highest` | `highest`, `latest`, `average`, `first`. Required by Canvas whenever multiple attempts are enabled |
| `cooling_period_seconds` | int | unset | Enforced wait between attempts |
| `grading_type` | string | `points` | Same values as assignments. Leave at `points` unless you have a reason - it is what makes autograding work |
| `omit_from_final_grade` | bool | `false` | Excluded from the final grade |
| `hide_in_gradebook` | bool | `false` | Hidden from the gradebook. Canvas requires `omit_from_final_grade: true` **and** points to be 0 or unset, or it rejects the update |
| `result_view` | block | unset | What students see after submitting; nested settings below |

Nested under `result_view:` - `restricted` is the master switch; when it is `false`
Canvas shows everything regardless of the rest.

| Key | Type | Notes |
|---|---|---|
| `restricted` | bool | Hide results from students |
| `show_questions` | bool | Show question text in results |
| `show_student_responses` | bool | Show what the student answered |
| `show_responses_frequency` | string | `always`, `once_per_attempt`, `after_last_attempt`, `once_after_last_attempt`. Needs `show_student_responses` |
| `show_responses_at` | ISO 8601 | Start showing responses at this time |
| `hide_responses_at` | ISO 8601 | Stop showing responses at this time |
| `show_correctness` | bool | Mark answers right/wrong |
| `show_correctness_at` | ISO 8601 | Start showing correctness |
| `hide_correctness_at` | ISO 8601 | Stop showing correctness |
| `show_correct_answers` | bool | Reveal the correct answer |
| `show_feedback` | bool | Show per-question feedback comments |
| `show_points_awarded` | bool | Show points earned |
| `show_points_possible` | bool | Show points available |

Example:

```yaml
---
canvas:
  type: new_quiz
  title: "Beam Bending Concepts"
  published: true
  points: 10
  time_limit: 1800          # seconds = 30 minutes
  allowed_attempts: -1
  score_to_keep: highest
  result_view:
    restricted: true
    show_questions: true
    show_student_responses: true
    show_responses_frequency: after_last_attempt
    show_correctness: false
    show_points_awarded: true
---
```

## Dates

Use ISO 8601 and **write the time students should see**:

```yaml
due_at: "2026-03-15T23:59:00"     # 23:59 course-local, all year round
```

A time with no `Z` and no offset is read as **course-local wall clock** and
converted to the right UTC instant at sync time, so daylight saving is handled for
you. Prefer this form - it is the one that keeps a weekly 23:59 deadline at 23:59
in every week of the semester.

Adding `Z` or an offset still means exactly what it says, and is passed through
untouched:

```yaml
due_at: "2026-03-15T22:59:00Z"        # an exact UTC instant
due_at: "2026-03-15T23:59:00+01:00"   # an exact instant, stated as an offset
```

Avoid hardcoding an offset unless you mean it: `+02:00` is simply wrong for the
half of the year Sweden is on `+01:00`, and a fixed `...Z` deadline shifts by an
hour when the clocks change. See `gotchas.md`.

### Accepted spellings

All of these are read as course-local time:

| Written | Means |
|---|---|
| `"2026-08-17T09:00:00"` | 09:00:00 - **use this one** |
| `"2026-08-17 09:00:00"` | same, space instead of `T` |
| `"2026-08-17T09:00"` | 09:00:00, seconds default to zero |
| `"2026-08-17"` | **midnight (00:00)** - see below |

**A bare date means the START of that day.** `due_at: "2026-08-17"` is a deadline at
00:00 on the 17th, which is almost never what is wanted - write
`"2026-08-17T23:59:00"` for end of day. `check_content` warns about this on `due_at`
and `lock_at`. It stays quiet for `unlock_at`, where "available from the 17th"
genuinely does mean midnight.

**Quoting is optional but recommended.** Unquoted values work fine; quoting just
reads consistently with the JSON quiz format, where quotes are mandatory.

**Removing a date key clears it in Canvas** - the local file is the source of
truth, so an omitted `due_at` means "no due date", not "leave whatever is there".

### Where the timezone comes from

`timezone` in `config.toml` if set, otherwise the Canvas course's own setting. Either
way the sync converts correctly.

The difference shows up in `check_content`, which runs **offline** and so can only see
`config.toml`. Without a `timezone` there it cannot check local times at all, and the
daylight-saving warnings below never fire. If you want those checks, set it:

```toml
timezone = "Europe/Stockholm"
```

With it set, `check_content` flags the two times daylight saving makes strange: an
hour that never happens (clocks jump forward) and one that happens twice (clocks go
back).
