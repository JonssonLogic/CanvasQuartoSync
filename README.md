# Canvas Quarto Sync

> [!NOTE]
> 🤖 Generated with [**Gemini 3 Pro**](https://antigravity.google/)

A Python tool to synchronize local **Quarto** content, assignments, quizzes, and calendar events directly to **Instructure Canvas**.

Allows you to manage your entire course as a local code repository (Git) while keeping Canvas perfectly in sync for students.

## Table of Contents

- [🚀 Key Features](#-key-features)
- [📚 Documentation & Examples](#-documentation--examples)
- [🛠️ Prerequisites](#-prerequisites)
- [📦 Installation](#-installation)
- [⚙️ Configuration](#-configuration)
- [🏃 Usage](#-usage)
- [📂 File Organization](#-file-organization)
- [📝 Content Metadata](#-content-metadata)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)

## 🚀 Key Features

*   **Quarto Integration**: Renders `.qmd` files to HTML and syncs them as Canvas Pages, Assignments, or Quizzes (both Classic and New Quizzes).
*   **External Links**: Add external URL links as module items using simple QMD frontmatter.
*   **Rich Quiz Descriptions**: Support for external `.qmd` description files for quizzes, enabling full markdown formatting and images.
*   **Smart Linking**:
    *   **Auto-Uploads**: Links to local PDFs, ZIPs, or images (`[Syllabus](docs/syllabus.pdf)`) are automatically uploaded to Canvas and securely linked.
    *   **Cross-References**: Link to other content by filename (`[Next Lab](02_Lab.qmd)`). The system resolves the correct Canvas URL.
    *   **JIT Stubbing**: Handles circular dependencies by creating placeholders ("stubs") if a link target doesn't exist yet.
*   **Safe Updates**: Edits existing Canvas items instead of overwriting them, preserving student submissions and grades.
*   **Performance & Caching**: 
    - **Smart Upload**: Only re-uploads assets (images/PDFs) if they have changed locally.
    - **Caching**: Minimizes API calls by remembering Canvas folder IDs.
*   **Auto-Cleanup**: Automatically "prunes" (deletes) orphaned assets from Canvas `synced-` folders when they are removed from your local files.
*   **Opt-in Calendar**: Manage your course schedule in a simple YAML file (`--sync-calendar`).
*   **Clean Output**: Semantic HTML rendering without duplicate headers or metadata clutter.

## 📚 Documentation & Examples

*   **[User Guide](Guides/Canvas_Sync_User_Guide.md)**: Comprehensive documentation on all features, file naming conventions, and advanced linking.
*   **[Example Project](Example/)**: A reference directory showing the correct folder structure, naming conventions, and typical `.qmd` file headers.

## 🛠️ Prerequisites

*   **Python 3.8+**
*   **[Quarto CLI](https://quarto.org/docs/get-started/)** (Must be in your system PATH)
*   **Canvas API Token**

## 📦 Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/JonssonLogic/CanvasQuartoSync.git
    cd CanvasQuartoSync
    ```

2.  Install dependencies:
    ```bash
    pip install canvasapi python-frontmatter PyYAML asteval
    ```

## ⚙️ Configuration

1.  **Environment Variables**:
    Set the following in your shell or `.env` file:
    *   `CANVAS_API_URL`: Your institution's Canvas URL (e.g., `https://canvas.instructure.com`)
    *   `CANVAS_API_TOKEN`: Your generic API access token.

2.  **Course ID**:
    Create a file named `course_id.txt` in your content directory containing only the numeric ID of your Canvas course (e.g., `12345`).

## 🏃 Usage

Run the sync script pointing to your content directory:

```bash
# Sync content from the current directory
python sync_to_canvas.py .

# Sync from a specific content folder
python sync_to_canvas.py ./MyCourseContent

# Sync including Calendar events (Opt-in)
python sync_to_canvas.py --sync-calendar
```

### Portable Mode
Copy `run_sync_here.bat` to your content folder to run the sync with a simple double-click (Windows).

## 📂 File Organization

The system enforces a **Module-based** structure using a `NN_Name` naming convention.

*   **Folders** starting with `NN_` (e.g., `01_Intro`) become **Canvas Modules**.
*   **Files** starting with `NN_` inside those folders become **Module Items**.

**Example Structure:**
```text
MyCourse/
├── course_id.txt           # Target Course ID
├── schedule.yaml           # (Optional) Calendar Events
├── 01_Introduction/        # -> Module: "Introduction"
│   ├── 01_Welcome.qmd      # -> Page
│   ├── 02_Syllabus.qmd     # -> Page
│   └── 03_Resources.pdf    # -> Solo File (Synced to module)
├── 02_Python_Basics/       # -> Module: "Python_Basics"
│   ├── 01_Lab.qmd          # -> Assignment
│   └── 05_Quiz.json        # -> Quiz
└── 99_Hidden.qmd           # -> Page (Synced but NOT added to module)
```

## 📝 Content Metadata

Control Canvas settings using YAML frontmatter in your `.qmd` or `.md` files.

**Page Example (`01_Welcome.qmd`)**:
```yaml
---
title: "Welcome to the Course"
canvas:
  type: page
  published: true
  indent: 0
---
```

**Assignment Example (`01_Lab.qmd`)**:
```yaml
---
title: "Lab 1: Hello World"
canvas:
  type: assignment
  published: true
  points: 10
  due_at: 2024-05-10T23:59:00Z
  submission_types: [online_upload]
  allowed_extensions: [py, ipynb]
---
```

## 📄 License

This project is open source and available under the [MIT License](LICENSE).
