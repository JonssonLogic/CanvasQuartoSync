# Canvas Sync System User Guide

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
    *   **Folders NOT matching this pattern are IGNORED.**

### Content Files
*   **Format**: `NN_Name.ext` (Two digits, underscore, Name, extension).
*   **Example**: `01_Welcome.qmd`, `02_Assignment.qmd`.
*   **Behavior**:
    *   **In a Module Folder**: The file is synced and added to that Module.
    *   **In Root Folder**: The file is synced to Canvas (as a Page/Assignment/etc.) but is **NOT added to any module**. (Useful for "loose" pages or hidden assignments).
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
      published: true      # Optional (Default: false)
      indent: 0            # Optional (0-5)
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
      published: true
      points: 10
      due_at: 2024-10-15T23:59:00Z
      submission_types: [online_upload] # e.g., online_text_entry, online_url
      allowed_extensions: [py, txt]
      indent: 1
    ---
    ```

### Text Headers (`.md`)
*   Used to create visual separators within modules.
*   **Metadata**:
    ```yaml
    ---
    title: "Section Header"
    canvas:
      type: subheader
      published: true
      indent: 0
    ---
    ```

### Quizzes (`.json`)
*   **Format**: A JSON object with a `canvas` block and a `questions` list.
*   **Note**: Quizzes are **unpublished** by default.
    ```json
    {
      "canvas": {
        "title": "Quiz Title",
        "published": false,
        "indent": 1
      },
      "questions": [
        {
          "question_name": "Q1",
          "question_text": "What is 2+2?",
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

### 3.5 Solo Files (PDFs, ZIPs, etc.)
*   **Format**: `NN_Name.ext` (where `.ext` is NOT `.qmd` or `.json`).
*   **Locality**: Place directly inside a module folder.
*   **Behavior**: 
    1.  The file is uploaded to the system-managed `_sync_assets_files` folder in Canvas.
    2.  It is automatically added to the Module as a **File** item.
    3.  Because it is a "Module Item", it is protected from the automatic **Orphan Cleanup**.

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
When you link to a non-content file (PDF, ZIP, DOCX, etc.), the system **uploads** it to Canvas (`course_files`) and creates a secure download link.
*   **Markdown**: `[Download Syllabus](docs/Syllabus.pdf)`
*   **Result**: Link becomes `https://canvas.../files/123/download`

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
*   **Reserved Folders**: All assets from your `.qmd` files are uploaded to `_sync_assets_images` and `_sync_assets_files`. 
*   **Smart Upload**: The system checks the "Last Modified" time of your local files. If a file hasn't changed, it **skips the upload**, making subsequent syncs near-instant.
*   **Caching**: Folder IDs are cached during the run to minimize API calls.

### E. Orphan Asset Cleanup (Pruning)
Over time, course storage can get cluttered with old images you no longer use.
*   **How it works**: At the end of every sync, the system scans the reserved `_sync` folders.
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
The first time a file is synced, the system records its unique **Canvas ID** in a hidden file called `.canvas_sync_map.json` in your content root.

*   **Persistent Tracking**: Even if you change the `title:` in the metadata or rename the physical `.qmd` file, the system uses this ID to find and update the **existing** object in Canvas.
*   **Safe Renaming**: You can safely change the title of an assignment; it will be updated in both the Canvas Assignment list and the Module without creating duplicates.
*   **Preserving Data**: Because it updates the existing object by ID, student submissions, grades, and quiz results are always preserved.

> [!CAUTION]
> **Do not delete `.canvas_sync_map.json`**. If this file is lost, the system will fall back to "Matching by Title". If you then rename a title, it will likely create a duplicate object in Canvas.
