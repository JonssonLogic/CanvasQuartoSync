// ── Study Guide Template ─────────────────────────────────────────────
// Used by both initCourse.ts and newProjectPanel.ts when scaffolding
// a new course project.

export function studyGuideTemplate(courseName: string): string {
  return `---
title: "Course PM"
canvas:
  type: study_guide
  preprocess: true
  published: true
  pdf:
    target_module: "Course Documents"
    filename: "KursPM.pdf"
    title: "Course PM (PDF)"
    published: true
---

# Introduction

Welcome to ${courseName}.

![Course intro image](../graphics/intro.png)

# Schedule

| Week | Topic | Activity |
|:-----|:------|:---------|
| 1 | Introduction | Lecture |
| 2 | Fundamentals | Lab 1 |
| 3 | ... | ... |

# Work Instructions

<!-- Describe how students should approach the coursework, lab procedures, report expectations, etc. -->

# Examination

All learning outcomes are examined through the **project** and the **labs**. The project grade determines the course grade.

| Component | Credits | Individual/Group | Grade |
|:----------|:--------|:-----------------|:------|
| Project^1^ | 4.5 ECTS | Group | 5/4/3/F |
| Laboratory work^2^ | 3 ECTS | Group | P/F |

^1^ Determines the course grade, which is issued only when all components are approved.\\
^2^ Labs are carried out in groups.

## Laboratory Work (Pass/Fail)

Labs are carried out in groups:

- Submit a short lab report written in Quarto, rendered to PDF.
- Demonstrate working results at your bench, or upload a photo/video.

## Project (graded 5/4/3/Fail)

The project is examined through:

1. ...
2. ...

## Re-examination

Re-examination follows the schedule at your institution.

# Grading Criteria

| ILO | Fail | 3 | 4 | 5 |
|:----|:-----|:--|:--|:--|
| Understanding of core concepts | Cannot explain basics | Can explain concepts | Can relate and compare | Can analyze in depth |

# Academic Integrity

Using unauthorized aids during examination is cheating. This includes phones, notes, or unauthorized materials. Attempting to cheat counts as a violation even if the aids were not used.

## Artificial Intelligence

AI tools (ChatGPT, Claude, Copilot) are **encouraged** as learning aids. They are useful for debugging, explaining concepts, and generating code starting points.

You are always responsible for understanding everything you submit and present. At the oral exam, your grade depends on your individual understanding, not what AI generated.

# Course Literature

## Reference (not required to purchase)

Title: ...\\
Author: ...\\
ISBN: ...

# Course Evaluation

A course evaluation will be conducted at the end of the course. Intermediate evaluations may be run. Improvements may be made during the course if they do not conflict with the course plan.

# Teaching Staff

| Name | Role | Image | Link |
|:-----|:-----|:------|:-----|
| Your Name | Course responsible | photo.png | https://example.com |

# Research Connection

See the education plan for research connection details.
`;
}
