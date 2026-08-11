# Links, images, and assets

Every link and image in your content is rewritten at sync time. You always write
**local relative paths**; the tool resolves them to Canvas URLs.

Paths are relative to **the file you are writing**, not the content root. A page in
`01_Introduction/` pointing at a shared `graphics/` folder at the root needs
`../graphics/...`.

## What happens to each kind of link

| You write | Result |
|---|---|
| `![alt](../graphics/x.png)` | Uploaded to the `synced-images` Canvas folder, link rewritten |
| `<img src="../graphics/x.png">` | Same - raw HTML `<img>` tags are processed too |
| `[text](../files/handbook.pdf)` | Uploaded to `synced-files`, linked to the Canvas file page with a download button |
| `[text](../02_Statics/01_Lab.qmd)` | Resolved to that item's real Canvas URL (a cross-link) |
| `[text](https://example.com)` | Left alone |
| Anything inside a fenced code block | Left alone |

## Cross-links between content

Link the local file and the tool finds the corresponding Canvas page, assignment, or
quiz:

```markdown
Read the [syllabus](01_Syllabus.qmd) before starting [Lab 1](../02_Statics/01_Lab.qmd).
```

The target must have `canvas:` frontmatter - that's what identifies it as a Canvas item.
A `.qmd` **without** canvas metadata is treated as a plain file and uploaded for
download instead, which is occasionally what you want (a template students edit) and
otherwise a mistake. The validator warns when it sees this.

**Order doesn't matter.** If you link something that hasn't synced yet, the tool creates
an empty placeholder in Canvas so the link resolves, and fills it in when that file
syncs. Circular links between two pages are fine.

## Uploaded assets are cleaned up automatically

Files in `synced-images` and `synced-files` that are no longer referenced by any content
get **deleted** at the end of a sync. That keeps the course tidy, and it means:

- Removing an image from a page removes it from Canvas too. That's intended.
- Anything the developer uploaded by hand in Canvas, outside those two folders, is never
  touched.

## Re-uploads are skipped when nothing changed

Assets are re-uploaded only when their modification time changes, so syncs stay fast.
If an image was deleted in Canvas by hand but is unchanged locally, it won't come back
on its own - that's a known wrinkle for the developer to resolve, not something to work
around by touching files.

## Naming reminder

Images, `branding.css`, and quiz `description_file` targets must **not** carry an `NN_`
prefix - a prefix makes the sync treat them as module items in their own right. Folders
without a prefix (`graphics/`, `files/`) are never walked for content, which is exactly
why they're the right place for assets.
