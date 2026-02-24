# Canvas Sync System User Guide

## Table of Contents

- [1. Getting Started](#1-getting-started)
  - [Prerequisites](#prerequisites)
  - [Configuration](#configuration)
  - [Usage](#usage)
- [2. File Organization & Naming Conventions](#2-file-organization--naming-conventions)
  - [Modules (Directories)](#modules-directories)
  - [Content Files](#content-files)
- [3. Content Types & Metadata](#3-content-types--metadata)
  - [Quarto Pages (.qmd)](#quarto-pages-qmd)
  - [Quarto Assignments (.qmd)](#quarto-assignments-qmd)
  - [Text Headers (.qmd)](#text-headers-qmd)
  - [Quizzes (.json)](#quizzes-json)
  - [QMD Quizzes (.qmd)](#qmd-quizzes-qmd)
  - [Solo Files (PDFs, ZIPs, etc.)](#solo-files-pdfs-zips-etc)
- [4. Calendar Synchronization](#4-calendar-synchronization)
- [5. Linking & Asset Handling (Power Feature)](#5-linking--asset-handling-power-feature)
  - [A. Local Files (Downloads)](#a-local-files-downloads)
  - [B. Images](#b-images)
  - [C. Cross-Linking (Smart Navigation)](#c-cross-linking-smart-navigation)
  - [D. Asset Namespacing & Optimization](#d-asset-namespacing--optimization)
  - [E. Orphan Asset Cleanup (Pruning)](#e-orphan-asset-cleanup-pruning)
- [6. Portable Syncing (Batch Script)](#6-portable-syncing-batch-script)
  - [Usage](#usage-1)
- [7. Synchronization Strategy & Tracking](#7-synchronization-strategy--tracking)
  - [The Sync Map (.canvas_sync_map.json)](#the-sync-map-canvas_sync_mapjson)

This system automates the synchronization of local course content to a Canvas course. It supports pages, assignments, quizzes, module headers, and calendar events.

## 1. Getting Started

### Prerequisites
1.  **Python 3.8+**
2.  **Quarto CLI**: Must be installed and available in your system PATH.
3.  **Python Packages**:
    ```bash
    pip install canvasapi python-frontmatter PyYAML
    ```
4.  **Environment Variables**:
    *   `CANVAS_API_URL` (e.g., `https://canvas.instructure.com`)
    *   `CANVAS_API_TOKEN` (Your generated API Access Token)

### Configuration
The **Course ID** must be specified in one of two ways (in order of priority):
1.  **Command Line Argument**: `--course-id 12345`
2.  **File**: Create a `course_id.txt` file in your content folder containing only the numeric ID.

### Usage
Run the script from the root of your project:

```powershell
# Default: Sync content from current directory
python sync_to_canvas.py

# Sync from a specific folder
python sync_to_canvas.py ../MyCourseData

# Sync including Calendar (Opt-in)
python sync_to_canvas.py --sync-calendar
```

---

## 2. File Organization & Naming Conventions

The system uses a **strict naming convention** to identify Modules and Content. 

### Modules (Directories)
*   **Format**: `NN_Name` (Two digits, underscore, Name).
*   **Example**: `01_Introduction`, `02_Python Basics`.
*   **Behavior**: 
    *   The prefix `01_` determines the module order in Canvas.
    *   The part after `_` becomes the Module Name (e.g., "Introduction").
    *   **Clean Look**: The `NN_` prefix is automatically removed from the title in Canvas.
    *   **Folders NOT matching this pattern are IGNORED.**

### Content Files
*   **Format**: `NN_Name.ext` (Two digits, underscore, Name, extension).
*   **Example**: `01_Welcome.qmd`, `02_Assignment.qmd`.
*   **Behavior**:
    *   **In a Module Folder**: The file is synced and added to that Module.
    *   **In Root Folder**: The file is synced to Canvas (as a Page/Assignment/etc.) but is **NOT added to any module**. (Useful for "loose" pages or hidden assignments).
    *   **Clean Titles**: When added to a module, the `NN_` prefix is stripped from the title (e.g. `01_Intro.pdf` becomes "Intro.pdf").
    *   **Files NOT matching this pattern are IGNORED.**

**Example Structure**:
```text
DailyWork/
├── 01_Introduction/        -> Module: "Introduction"
│   ├── 01_Welcome.qmd      -> Page (In Module)
│   └── 03_Resources.md     -> SubHeader
├── 02_Python Basics/       -> Module: "Python Basics"
│   └── 01_FirstProg.qmd    -> Assignment (In Module)
├── 99_HiddenPage.qmd       -> Page (Synced, but NOT in any module)
├── graphics/               -> Ignored (no prefix)
└── handlers/               -> Ignored (no prefix)
```

---

## 3. Content Types & Metadata

> [!IMPORTANT]
> **Safe Updates**: When the system syncs, it checks if an item with the same title or internal ID already exists. 
> *   **If Found**: It **updates** the existing item (description, points, dates, etc.). This ensures student submissions and grades are **preserved**.
> *   **Dynamic Renaming**: If you change the `title` in your frontmatter (or JSON), the system will update the title of the existing Page/Assignment in Canvas. The link within the Canvas Module will also be updated to match the new title automatically.
> *   **File Rename**: Renaming the physical file (e.g., `01_Intro.qmd` -> `01_Introduction.qmd`) while keeping the same `title` in the frontmatter is perfectly safe and will not create a duplicate.

### Quarto Pages (`.qmd`)
*   **Locality**: Place in a module folder.
*   **Metadata**:
    ```yaml
    ---
    title: "Page Title"
    format:
      html:
        page-layout: article # Recommended
    canvas:
      type: page
      published: true      # (optional, Default: false)
      indent: 0            # (optional, 0-5)
    ---
    ```

### Quarto Assignments (`.qmd`)
*   **Metadata**:
    ```yaml
    ---
    title: "Assignment Title"
    format:
      html:
        page-layout: article
    canvas:
      type: assignment
      published: true                   # (optional)
      points: 10                       # (optional)
      due_at: 2024-10-15T23:59:00Z      # (optional, ISO 8601)
      unlock_at: 2024-10-01T08:00:00Z   # (optional)
      lock_at: 2024-10-20T23:59:00Z     # (optional)
      grading_type: points              # (optional: points, percentage, pass_fail, letter_grade, gpa_scale, not_graded)
      submission_types: [online_upload] # (optional: [online_upload, online_text_entry, online_url, media_recording, student_annotation, none, external_tool])
      allowed_extensions: [py, txt]     # (optional)
      indent: 1                       # (optional)
    ---
    ```

### Text Headers (`.qmd`)
*   Used to create visual separators within modules.
*   **Metadata**:
    ```yaml
    ---
    title: "Section Header"
    canvas:
      type: subheader
      published: true      # (optional)
      indent: 0            # (optional)
    ---
    ```

### Quizzes (`.json`)
*   **Format**: A JSON object with a `canvas` block and a `questions` list.
*   **LaTeX Support**: Supports LaTeX math (e.g., `$x^2$` or `$$ \int dx $$`).
    > **Note**: JSON quizzes are processed through Quarto to render Markdown and LaTeX. This ensures consistent formatting but may be slightly slower than syncing raw text.
*   **Note**: Quizzes are **unpublished** by default.
    ```json
    {
      "canvas": {
        "title": "Quiz Title",
        "published": true,                // (optional)
        "description_file": "Quiz_Description.qmd", // (optional) - Path to .qmd file for rich description
        "due_at": "2024-10-15T23:59:00Z", // (optional) - Removing this clears the date
        "unlock_at": "2024-10-01T08:00:00Z", // (optional)
        "lock_at": "2024-10-20T23:59:00Z", // (optional)
        "show_correct_answers": true,     // (optional)
        "shuffle_answers": true,          // (optional)
        "time_limit": 30,                 // (optional)
        "allowed_attempts": 3,            // (optional)
        "indent": 1                       // (optional)
      },
      "questions": [
        {
          "question_name": "Q1",
          "question_text": "What is 2+2?",
          "question_type": "multiple_choice_question", // (multiple_choice_question, true_false_question, short_answer_question, fill_in_multiple_blanks_question, multiple_answers_question, multiple_dropdowns_question, matching_question, numerical_question, calculated_question, essay_question, file_upload_question, text_only_question)
          "points_possible": 1,
          "answers": [
            {"answer_text": "4", "weight": 100},
            {"answer_text": "5", "weight": 0}
          ]
        }
      ]
    }
    ```

    > [!IMPORTANT] 
    > Do **not** use the `NN_` prefix for description files (e.g., `Quiz_Description.qmd`, NOT `04_Quiz_Description.qmd`), or they might be synced as separate pages.
*   **Supported Settings**:
    *   `due_at` (optional, ISO 8601 String) - *Removing this clears the date in Canvas*
    *   `unlock_at` (optional, ISO 8601 String) - *Removing this clears the date in Canvas*
    *   `lock_at` (optional, ISO 8601 String) - *Removing this clears the date in Canvas*
    *   `description_file` (optional, String) - *Relative path to a `.qmd` file containing the quiz description (supports images/markdown)*
    *   `show_correct_answers` (optional, Boolean)
    *   `shuffle_answers` (optional, Boolean)
    *   `time_limit` (optional, Minutes)
    *   `allowed_attempts` (optional, Integer, use -1 for unlimited)
    *   `quiz_type` (optional: practice_quiz, assignment, graded_survey, survey)

    > [!WARNING]
    > **Modifying Active Quizzes**
    > To safely update questions, the sync tool uses an **"Unpublish → Modify → Republish"** workflow.
    > *   **If no students have started**: This is seamless. The quiz briefly flips to "Draft" mode, updates, and re-publishes.
    > *   **If students have submissions**: Canvas **blocks** unpublishing. The tool detects this, skips draft mode, and updates questions in-place. All changes are saved to the Canvas database, but you will need to click **"Save It Now"** in Canvas to regenerate the quiz snapshot. The tool prints a direct link to the quiz for convenience.
    >     *   *This is a known Canvas API limitation — the REST API cannot trigger the internal snapshot regeneration for already-published quizzes.*

### QMD Quizzes (`.qmd`)

Quizzes can also be written as `.qmd` files, enabling **rich content** (formatted text, LaTeX, images) in both question text and answer text. The system detects a `.qmd` as a quiz if it contains `:::: {.question` blocks.

*   **Structure**: YAML frontmatter (quiz settings) + `:::: {.question}` fenced div blocks.
*   **Rendering**: All markdown content is rendered to HTML via Quarto and images are uploaded to Canvas automatically.

**Frontmatter** (identical settings as JSON quizzes):
```yaml
---
canvas:
  title: "Quiz Title"
  quiz_type: practice_quiz
  published: true
  shuffle_answers: true
  show_correct_answers: true
  allowed_attempts: -1
---
```

**Question Block Reference**:

| Element | Syntax | Default |
|---|---|---|
| Question block | `:::: {.question name="..." points=N type=...}` | `points=1`, `type=multiple_choice_question` |
| Question name | `name="..."` attribute | Auto: "Fråga 1", "Fråga 2", ... |
| Simple answer ✓ | `- [x] answer text` | `answer_weight: 100` |
| Simple answer ✗ | `- [ ] answer text` | `answer_weight: 0` |
| Simple answer comment | Indented sub-item: `  - comment text` | Optional |
| Rich answer | `::: {.answer correct=true comment="..."}` | `correct=false`, no comment |
| Correct feedback | `::: correct-comment` ... `:::` | Optional |
| Incorrect feedback | `::: incorrect-comment` ... `:::` | Optional |

> [!IMPORTANT]
> Each question uses **either** checklist answers (`- [x]`/`- [ ]`) **or** div answers (`::: .answer`) — never both in the same question.
>
> - **Checklist style**: Best for short text/formula answers. Per-answer comments are indented sub-items.
> - **Div style**: Best when answers need images, multiple paragraphs, or rich formatting. Per-answer comments use the `comment="..."` attribute.

**Example — Checklist answers** (simple, short answers):
```markdown
:::: {.question name="Stress Definition"}

  Which formula describes **normal stress**?

  ![](graphics/stress_diagram.png)

  - [x] $\sigma = F/A$
    - Correct! Stress is force per area.
  - [ ] $\sigma = F \cdot A$
    - This gives the wrong units.
  - [ ] $\sigma = F + A$
  - [ ] $\sigma = F - A$

  ::: correct-comment
  Well done! Stress is defined as force per unit area.
  :::

  ::: incorrect-comment
  Think about what the unit Pa represents.
  :::

::::
```

**Example — Rich div answers** (multi-line, images in answers):
```markdown
:::: {.question name="Hooke's Law" points=2}

  What does $E$ represent in **Hooke's law**?

  ::: {.answer correct=true comment="Correct! Also known as Young's modulus."}
  **Elastic modulus** (Young's modulus) — a material constant
  that describes the material's stiffness.

  ![](graphics/e_modulus.png)
  :::

  ::: {.answer comment="No, strain is denoted by ε."}
  Strain
  :::

  ::: {.answer}
  Cross-sectional area
  :::

::::
```

> [!TIP]
> **Indentation is optional.** Content inside `:::: question` and `::: answer` blocks can be indented (e.g., 2 spaces) for readability — the parser handles both indented and non-indented content.

### Solo Files (PDFs, ZIPs, etc.)
*   **Format**: `NN_Name.ext` (where `.ext` is NOT `.qmd` or `.json`).
*   **Locality**: Place directly inside a module folder.
*   **Behavior**: 
    1.  The file is uploaded to the system-managed `synced-files` folder in Canvas.
    2.  It is automatically added to the Module as a **File** item.
    3.  **Clean Titles**: The `NN_` prefix is stripped from the module item title (e.g., `05_Syllabus.pdf` becomes "Syllabus.pdf").
    4.  Because it is a "Module Item", it is protected from the automatic **Orphan Cleanup**.

---

## 4. Calendar Synchronization

*   **File**: `schedule.yaml` in the content root.
*   **Command**: Must run with `--sync-calendar` to update.
*   **Logic**: 
    *   **Single Events**: Created as-is.
    *   **Series**: Defined with `days: ["Mon", "Thu"]`. Expanded into individual events.
*   **Manual Changes**: Syncing without the flag preserves manual changes in Canvas.

**Example `schedule.yaml`**:
```yaml
events:
  - title: "Kickoff Meeting"
    date: "2024-01-10"
    time: "09:00-10:00"
    description: "Introductory session."

  - title: "Weekly Lecture"
    start_date: "2024-01-15"
    end_date: "2024-05-15"
    days: ["Mon", "Wed"]
    time: "10:15-12:00"
    location: "Room 101"
```

---

## 5. Linking & Asset Handling (Power Feature)

The system automatically scans your Quarto content (`.qmd`) for links to local files and converts them into Canvas-ready links using intelligent resolution.

### A. Local Files (Downloads)
When you link to a non-content file (PDF, ZIP, DOCX, PY, etc.), the system **uploads** it to Canvas and links to the **Canvas file preview page**, which has a built-in **Download** button.
*   **Markdown**: `[Download Syllabus](docs/Syllabus.pdf)` or `[Get Script](files/script.py)`
*   **Result**: Link becomes `https://canvas.../courses/101/files/123`

### B. Images
Local images are **uploaded** to `course_images` and embedded.
*   **Markdown**: `![Elephant](graphics/elephant.jpg)`
*   **Result**: Image displays using Canvas file storage.

### C. Cross-Linking (Smart Navigation)
You can link directly to other Pages, Assignments, or Quizzes by referencing their **local filename**.
*   **Markdown**: `[Next Assignment](../02_Python/01_Assignment.qmd)`
*   **Result**: The system finds the real Canvas Assignment URL and links to it `https://canvas.../courses/101/assignments/555`.
*   **Circular Links**: If you link to a Page that hasn't been synced yet, the system automatically creates a **"Stub"** (empty placeholder) to generate the URL, ensuring your links never break.

### D. Asset Namespacing & Optimization
To keep your course clean and fast, the system uses a specialized strategy for assets:
*   **Reserved Folders**: All assets from your `.qmd` files are uploaded to `synced-images` and `synced-files`. 
*   **Smart Render & Upload**: The system checks the "Last Modified" time (`mtime`) of your local files. 
    *   If a `.qmd` or `.json` file hasn't changed, it **skips Quarto rendering** and the Canvas `edit()` call.
    *   If an asset (image/PDF) hasn't changed, it **skips the upload**.
    *   This makes subsequent syncs for large courses faster.
*   **Caching**: Folder IDs are cached during the run to minimize API calls.

### E. Orphan Asset Cleanup (Pruning)
Over time, course storage can get cluttered with old images you no longer use.
*   **How it works**: At the end of every sync, the system scans the reserved `synced` folders.
*   **Pruning**: Any file in these folders that is **NOT** referenced in your current content is automatically deleted.
*   **Safety**: This process **only** touches files inside the system's reserved folders. Your manuals uploads in `course_files` or `Documents` are never affected.

---

## 6. Portable Syncing (Batch Script)

A helper script `run_sync_here.bat` is available to execute the sync from any directory (e.g., if you keep your content separate from the code).

### Usage
1.  Copy `run_sync_here.bat` into your content folder.
2.  **Basic Sync**: Double-click the file to sync the content in that folder.
3.  **Shortcuts & Arguments** (e.g., for Calendar Sync):
    *   Create a shortcut to the `.bat` file.
    *   Right-click the Shortcut -> **Properties**.
    *   In the **Target** field, append the argument: 

---

## 7. Synchronization Strategy & Tracking

To ensure your Canvas course stays in sync through renames and moves, the system uses a **Local Mapping** strategy.

### The Sync Map (`.canvas_sync_map.json`)
The first time a file is synced, the system records its unique **Canvas ID** and the local **Last Modified Time (mtime)** in a hidden file called `.canvas_sync_map.json` in your content root.

*   **Persistent Tracking**: Even if you change the `title:` in the metadata or rename the physical `.qmd` file, the system uses this ID to find and update the **existing** object in Canvas.
*   **Safe Renaming**: You can safely change the title of an assignment; it will be updated in both the Canvas Assignment list and the Module without creating duplicates.
*   **Preserving Data**: Because it updates the existing object by ID, student submissions, grades, and quiz results are always preserved.

> [!CAUTION]
> **Do not delete `.canvas_sync_map.json`**. If this file is lost, the system will fall back to "Matching by Title". If you then rename a title, it will likely create a duplicate object in Canvas.
