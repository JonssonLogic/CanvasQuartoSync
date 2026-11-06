# Recipes

Working snippets for the things authors actually ask for.

## The rule behind most rendering surprises

Canvas receives **the rendered `<main>` body and no CSS**. No stylesheet, no Bootstrap,
no Quarto theme. Anything Quarto styles by attaching a *class* therefore arrives
unstyled unless the sync inlines it explicitly.

The sync already inlines the common cases for you - callouts, code syntax highlighting,
math (converted to Canvas equation images) and, when the course asks for it, table
styling. Everything else needs **inline styles or explicit dimensions**, not classes.

Practical consequence: don't invent `<div class="my-thing">` and expect it to look like
anything. Use inline `style="..."` if you need custom presentation.

## Embed a YouTube video

Use Quarto's video shortcode, on **its own line**, with explicit dimensions - the default
wrapper relies on Bootstrap ratio classes that Canvas doesn't have, so without
`width`/`height` the player collapses:

```markdown
{{< video https://www.youtube-nocookie.com/embed/VIDEO_ID?feature=oembed&rel=0 width="320" height="240" >}}
```

Use the `youtube-nocookie.com/embed/` form. `rel=0` limits suggested videos at the end.

## Add an image

```markdown
![Stress-strain curve](../graphics/stress_strain.png)
```

The image is uploaded to the course's `synced-images` folder and the link rewritten
automatically. Paths are **relative to the file you're writing** - a page in
`01_Introduction/` referring to `graphics/` at the content root needs `../graphics/`.
This is the most common broken-link mistake; the validator checks it.

Sizing and captions use Quarto attributes, which survive because they become plain HTML
attributes:

```markdown
![Cross-section](../graphics/beam.png){width=400}
```

Images inside quiz answers work the same way.

## Link a downloadable file

```markdown
[Course handbook](../files/handbook.pdf)
```

Any link to a non-content local file (PDF, ZIP, DOCX, CSV, PY…) uploads it and links to
the Canvas file page, which gives students a download button. No prefix needed on the
file - it isn't a module item.

To put a file in the module list *as its own item* instead, drop it in the module folder
and list it in that folder's `_order.yml` where it should appear (`- Datasheet.pdf`).
In a course with `NN_` prefixes, give it a prefix instead: `03_Datasheet.pdf`.

## Link to another page, assignment, or quiz

Link the **local file**, and the sync resolves it to the real Canvas URL:

```markdown
See the [Truss Analysis assignment](../02_Statics/01_Truss_Analysis.qmd).
```

If the target hasn't synced yet, a placeholder is created so the link never breaks, and
it gets filled in when that file syncs. See linking.md.

## Callouts

```markdown
::: {.callout-tip}
## Rule of thumb
Stress concentrations roughly triple at a sharp corner.
:::
```

Available: `callout-tip`, `callout-note`, `callout-important`, `callout-warning`,
`callout-caution`. Colours and icons come from `branding.css` if it defines them.

**Always give a callout a title** - a `##` heading as the first line inside, as above.
Quarto only emits the `callout-titled` structure that the sync's style inliner matches
when a callout has a title; an untitled one arrives in Canvas as an unstyled block of
text. The heading form is the one this project's tests exercise.

## Tables

Ordinary markdown pipe tables work and arrive as real HTML tables:

```markdown
| Material | E (GPa) | Yield (MPa) |
|:---------|--------:|------------:|
| Steel    |     210 |         250 |
| Aluminium|      69 |         240 |
```

### Styling a table

Options go on the caption line after the table, with or without caption text:

```markdown
: Material data {.striped .bordered}
```

| Option | What it does |
|:--|:--|
| `.striped` | a background on every other body row |
| `.bordered` | lines around every cell |
| `.sm` | tighter cell padding |
| `.hover` | passed on to Canvas, which shows no visible highlight: don't promise one |
| `.plain` | the table stays as Canvas draws it, whatever the course sets |

In a course without table settings these get Canvas's own faint styling. A course
turns on its own look with `[tables] style = [...]` in `config.toml` (the options for
tables that name none) and colours on `:root` in `branding.css`. Those are the
teacher's settings: change them only when asked.

For one table, wrap it. The wrapper's values override the course's, and the sync
removes the wrapper:

```markdown
::: {.cqs-table stripe="#eef3f8" border="#999999"}

| Week | Topic |
|---|---|
| 44 | Introduction |

: {.striped .bordered}

:::
```

| In `branding.css` | On the wrapper | Sets |
|:--|:--|:--|
| `--table-stripe` | `stripe=` | background of the striped rows |
| `--table-border` | `border=` | colour of the lines |
| `--table-header-bg` | `header-bg=` | background of the header row |
| `--table-header-fg` | `header-fg=` | text colour of the header row |

A class in `branding.css` that sets these values, such as
`.schema { --table-stripe: #eef3f8; }`, is a named style: add `.schema` to a caption
line to use it. Values are colours (`#003865`, `rgb(0, 56, 101)`, `white`,
`var(--brand-heading)`). Quarto drops `key=value` on the caption line itself, which is
why one table's values need the wrapper. `check_content` flags a wrapper with no table
inside and a value that is not a colour.

## Math

Inline `$\sigma = F/A$` and display `$$\int_0^L M(x)\,dx$$` both work. They are converted
to Canvas equation images at sync time, so they render for students without MathJax.

Inside quiz questions and answers, math works the same way - except in **formula
questions**, where `[variable]` placeholders must stay outside the math (see quizzes.md).

## Code

````markdown
```python
def stress(force, area):
    return force / area
```
````

Syntax highlighting is inlined automatically, so it keeps its colours in Canvas.

## Raw HTML

When you genuinely need markup Quarto won't produce:

````markdown
```{=html}
<div style="border-left: 4px solid #961B81; padding: 8px 12px;">
  Custom block with inline styles.
</div>
```
````

Inline `style` attributes only - classes won't survive. Canvas also sanitizes HTML, so
scripts and most embeds other than allowlisted media will be stripped.

## Not confirmed

These are undocumented rather than known-broken - check with the developer before
relying on them, and note the outcome in `.claude/kit-gaps.md`:

- Quarto shortcodes other than `{{< video >}}` (`{{< include >}}`, `{{< embed >}}`).
- Quarto figure cross-referencing (`@fig-label`) across separate Canvas pages.
- Mermaid or other diagram blocks that render client-side.
