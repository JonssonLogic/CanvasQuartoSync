import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { resolveCqsRoot } from '../python/venvResolver';
import { studyGuideTemplate } from '../templates/studyGuide';

// ── Init Course Wizard ───────────────────────────────────────────────
//
// Multi-step wizard that creates a new course project.
// Replaces init_course.bat with a guided VS Code experience.
//
// Steps:
//   1. Pick a folder for the course project
//   2. Enter course name
//   3. Enter course code
//   4. Enter Canvas course ID
//   5. Enter Canvas API URL
//   6. Pick token file path
//   7. Enter semester
//   8. Pick language
//
// Creates: config.toml, _quarto.yml, run_sync_here.bat,
//          01_Course_Info/, 01_Course_Info/01_StudyGuide.qmd, graphics/

export async function initCourse(extensionPath?: string): Promise<void> {
  // Step 1: Choose folder
  const folderUri = await vscode.window.showOpenDialog({
    canSelectFiles: false,
    canSelectFolders: true,
    canSelectMany: false,
    openLabel: 'Select Course Folder',
    title: 'Choose an empty folder for the new course project',
  });

  if (!folderUri || folderUri.length === 0) {
    return;
  }

  const courseDir = folderUri[0].fsPath;

  // Warn if config.toml already exists
  if (fs.existsSync(path.join(courseDir, 'config.toml'))) {
    const overwrite = await vscode.window.showWarningMessage(
      'This folder already has a config.toml. Overwrite it?',
      'Yes, overwrite',
      'Cancel'
    );
    if (overwrite !== 'Yes, overwrite') {
      return;
    }
  }

  // Step 2: Course name
  const courseName = await vscode.window.showInputBox({
    prompt: 'Step 2/8 — Course name (displayed in Canvas)',
    placeHolder: 'e.g. Mechatronics',
    value: 'My Course',
  });
  if (courseName === undefined) return;

  // Step 3: Course code
  const courseCode = await vscode.window.showInputBox({
    prompt: 'Step 3/8 — Course code',
    placeHolder: 'e.g. TMRK16',
    value: 'CODE',
  });
  if (courseCode === undefined) return;

  // Step 4: Canvas course ID
  const courseIdStr = await vscode.window.showInputBox({
    prompt: 'Step 4/8 — Canvas course ID (the number in the Canvas URL)',
    placeHolder: 'e.g. 12345',
    value: '0',
    validateInput: (v) => (/^\d+$/.test(v) ? null : 'Must be a number'),
  });
  if (courseIdStr === undefined) return;

  // Step 5: Canvas API URL
  const canvasApiUrl = await vscode.window.showInputBox({
    prompt: 'Step 5/8 — Canvas API URL for your institution',
    placeHolder: 'e.g. https://canvas.university.edu/api/v1',
    value: 'https://your-institution.instructure.com/api/v1',
  });
  if (canvasApiUrl === undefined) return;

  // Step 6: Token file path
  const tokenResult = await vscode.window.showOpenDialog({
    canSelectFiles: true,
    canSelectFolders: false,
    canSelectMany: false,
    openLabel: 'Select Canvas Token File',
    title:
      'Step 6/8 — Select the file containing your Canvas API token (or Cancel to set later)',
    filters: { 'All files': ['*'] },
  });
  const tokenPath = tokenResult?.[0]?.fsPath ?? 'privateCanvasToken';

  // Step 7: Semester
  const now = new Date();
  const season = now.getMonth() < 6 ? 'Spring' : 'Fall';
  const defaultSemester = `${season} ${now.getFullYear()}`;

  const semester = await vscode.window.showInputBox({
    prompt: 'Step 7/8 — Semester',
    placeHolder: 'e.g. Spring 2026',
    value: defaultSemester,
  });
  if (semester === undefined) return;

  // Step 8: Language
  const language = await vscode.window.showQuickPick(['english', 'swedish'], {
    placeHolder: 'Step 8/8 — Course language',
  });
  if (language === undefined) return;

  // ── Create the project ──────────────────────────────────────────────

  try {
    // Create folders
    const dirs = [
      path.join(courseDir, '01_Course_Info'),
      path.join(courseDir, 'graphics'),
    ];
    for (const dir of dirs) {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
    }

    // Write config.toml
    const configToml = [
      `course_id = ${courseIdStr}`,
      `course_name = "${courseName}"`,
      `course_code = "${courseCode}"`,
      `credits = "7.5 ECTS"`,
      `semester = "${semester}"`,
      `canvas_api_url = "${canvasApiUrl}"`,
      `canvas_token_path = "${tokenPath.replace(/\\/g, '/')}"`,
      `language = "${language}"`,
    ].join('\n');

    fs.writeFileSync(path.join(courseDir, 'config.toml'), configToml, 'utf-8');

    // Copy _quarto.yml from Example folder
    if (extensionPath) {
      const cqsRoot = resolveCqsRoot(extensionPath);
      const quartoSrc = path.join(cqsRoot, 'Example', '_quarto.yml');
      const quartoDst = path.join(courseDir, '_quarto.yml');
      if (fs.existsSync(quartoSrc) && !fs.existsSync(quartoDst)) {
        fs.copyFileSync(quartoSrc, quartoDst);
      }

      // Copy run_sync_here.bat
      const syncBatSrc = path.join(cqsRoot, 'run_sync_here.bat');
      const syncBatDst = path.join(courseDir, 'run_sync_here.bat');
      if (fs.existsSync(syncBatSrc) && !fs.existsSync(syncBatDst)) {
        fs.copyFileSync(syncBatSrc, syncBatDst);
      }
    }

    // Write example study guide
    const studyGuidePath = path.join(
      courseDir,
      '01_Course_Info',
      '01_StudyGuide.qmd'
    );
    if (!fs.existsSync(studyGuidePath)) {
      fs.writeFileSync(
        studyGuidePath,
        studyGuideTemplate(courseName || 'the course'),
        'utf-8'
      );
    }

    // Ask if the user wants to open the new project
    const openAction = await vscode.window.showInformationMessage(
      `Course project "${courseName}" created successfully!`,
      'Open in VS Code',
      'Open config.toml'
    );

    if (openAction === 'Open in VS Code') {
      await vscode.commands.executeCommand(
        'vscode.openFolder',
        vscode.Uri.file(courseDir)
      );
    } else if (openAction === 'Open config.toml') {
      const doc = await vscode.workspace.openTextDocument(
        path.join(courseDir, 'config.toml')
      );
      await vscode.window.showTextDocument(doc);
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`Failed to create course project: ${msg}`);
  }
}
