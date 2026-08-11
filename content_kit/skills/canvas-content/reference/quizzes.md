# Quizzes

Two formats (`.qmd` and `.json`) across two Canvas engines (Classic and New Quizzes).

**Use `.qmd` + `type: new_quiz` unless you have a reason not to.** It supports every
question type, rich answers with images, and per-answer feedback. Reach for JSON only
for compact text-only quizzes, and for Classic only when you need `quiz_type`,
`description_file`, or `show_correct_answers`.

Quiz settings live under `canvas:` and are documented in frontmatter.md. This file
covers the **question syntax**.

## File shape

````markdown
---
canvas:
  type: new_quiz
  title: "Stress and Strain"
  published: true
  points: 10
---

:::: {.question name="Stress definition" points=2}

Which formula describes **normal stress**?

- [x] $\sigma = F/A$
  - Correct - stress is force per unit area.
- [ ] $\sigma = F \cdot A$
  - That gives the wrong units.
- [ ] $\sigma = F + A$

::: correct-comment
Well done.
:::

::: incorrect-comment
Think about what the unit Pa represents.
:::

::::
````

Four colons open and close a question; three colons are used for blocks inside it.
Indenting the content of a block is optional - the parser strips common leading
whitespace.

## Question attributes

Written inside the opening fence: `:::: {.question name="..." points=2 type=...}`

| Attribute | Default | Notes |
|---|---|---|
| `name` | `Fråga 1`, `Fråga 2`, … | **Always set this.** It is how the sync matches a question to the one already in Canvas across re-syncs; the default is a Swedish placeholder |
| `points` (or `points_possible`) | `1` | Points for this question |
| `type` | `multiple_choice_question` | See the list below |

Question names must be unique within a quiz - duplicates get treated as extras and
deleted on the next sync.

## Answer styles - pick one per question

**Checklist** - short answers:

```markdown
- [x] Correct answer
  - Optional feedback shown for this choice.
- [ ] Wrong answer
  - Optional feedback for this one.
```

**Rich divs** - answers with images, multiple paragraphs, or formatting:

```markdown
::: {.answer correct=true comment="Also known as Young's modulus."}
**Elastic modulus** - a material constant describing stiffness.

![](../graphics/e_modulus.png)
:::

::: {.answer comment="No, strain is denoted by epsilon."}
Strain
:::
```

**Never mix the two styles inside one question.** If a question contains any
`::: {.answer}` block, the parser uses div answers and **silently drops every
checklist answer**. The validator treats this as an error.

## Whole-question feedback

```markdown
::: correct-comment
Shown when the student gets it right.
:::

::: incorrect-comment
Shown when they don't.
:::
```

Note these use bare names, not `{.class}` syntax.

## Question types

`multiple_choice_question` (default), `true_false_question`, `multiple_answers_question`,
`short_answer_question`, `essay_question`, `file_upload_question`, `text_only_question`,
`fill_in_multiple_blanks_question`, `multiple_dropdowns_question`, `matching_question`,
`numerical_question`, `calculated_question`, plus two that are **New Quizzes only**:
`numeric_question` and `formula_question`.

Rules the validator enforces:
- `multiple_choice_question` and `true_false_question` need exactly **one** correct answer.
- `multiple_answers_question` needs at least one.
- `essay_question` and `file_upload_question` take no answers and are graded manually.

## Numeric questions (New Quizzes only)

Student types a number; graded with tolerance. Use `.answer` blocks with attributes and
no body. Multiple blocks mean multiple accepted answers.

```markdown
:::: {.question type="numeric_question" name="Max stress" points=3}

A 200 kN force acts on 1000 mm². What is the stress in MPa?

::: {.answer value="200" margin="5"}
:::

::::
```

| Form | Attributes | Accepts |
|---|---|---|
| Exact | `value="200"` | Exactly 200 |
| Margin (absolute) | `value="200" margin="5"` | 195-205 |
| Margin (percent) | `value="200" margin="2" margin_type="percent"` | 196-204 |
| Range | `start="190" end="210"` | Anything in the range |
| Precision | `value="200.00" precision="2" precision_type="decimals"` | Matching to 2 decimals |

## Formula questions (New Quizzes only)

Randomised variables per student attempt. Three parts: `[placeholders]` in the text, a
`.formula` block, and one `.variable` block per placeholder.

````markdown
:::: {.question type="formula_question" name="Stress calculation" points=5}

A bar carries F = [F] kN over an area of A = [A] mm². Compute the stress in MPa.

::: {.formula}
formula: F * 1000 / A
margin: 2
margin_type: percent
answer_count: 10
distribution: even
:::

::: {.variable name="F"}
min: 10
max: 100
precision: 0
:::

::: {.variable name="A"}
min: 50
max: 500
precision: 0
:::

::::
````

`.formula` keys: `formula` (required), `margin` (default `0`), `margin_type`
(`absolute` | `percent`), `answer_count` (default `10`), `distribution`
(`random` | `even`; `even` spreads values across the full range).

`.variable` keys: `min`, `max`, `precision` (decimal places; `0` = integers).

Expressions support `+ - * / **`, parentheses, and functions like `sin`, `cos`, `sqrt`,
`abs`, `pi`.

Two things that will bite you:

- **Never put a `[placeholder]` inside math**: `$[F] = 5$` breaks, because Quarto
  processes the brackets. Write `F = [F] kN` outside the math instead.
- **Canvas does not evaluate your formula.** The sync computes `answer_count` solution
  sets locally and uploads them. A formula that can divide by zero over its variable
  ranges fails at validation time - keep `min` above `0` for any divisor.

## JSON format

Same `canvas` settings block, questions as an array. Add `"quiz_engine": "new"` for
New Quizzes; omit it for Classic.

```json
{
  "canvas": {
    "title": "Materials Quiz",
    "quiz_engine": "new",
    "published": true,
    "shuffle_answers": true
  },
  "questions": [
    {
      "question_name": "Q1",
      "question_text": "What is $2+2$?",
      "question_type": "multiple_choice_question",
      "points_possible": 1,
      "answers": [
        {"answer_text": "4", "weight": 100},
        {"answer_text": "5", "weight": 0}
      ]
    }
  ]
}
```

LaTeX in JSON is rendered through Quarto just as in `.qmd`. JSON cannot express rich
answers, per-answer feedback, numeric, or formula questions - use `.qmd` for those.

## Engine differences at a glance

| | Classic (`type: quiz`) | New (`type: new_quiz`) |
|---|---|---|
| Canvas object | Quiz | Assignment (quiz-backed) |
| `time_limit` unit | minutes | **seconds** |
| Numeric / formula questions | not supported | supported |
| `quiz_type`, `description_file`, `show_correct_answers` | supported | ignored |
| `points`, `shuffle_questions`, `score_to_keep`, `result_view`, `hide_in_gradebook`, `omit_from_final_grade`, `calculator_type`, `cooling_period_seconds` | ignored | supported |
| Updating after students have submitted | needs a manual "Save It Now" click in Canvas | updates directly |
