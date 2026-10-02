import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { resolveCqsRoot } from '../python/venvResolver';
import { studyGuideTemplate } from '../templates/studyGuide';

// ── New Project Webview Panel ────────────────────────────────────────
//
// Opens a full-page form (like PlatformIO's "New Project" page) where
// the user fills in all course configuration at once:
//   - Project folder (with Browse button)
//   - Course name, code, Canvas ID
//   - Canvas API URL, token file
//   - Semester, language, credits
//
// Submitting the form creates the project scaffold.

let currentPanel: vscode.WebviewPanel | undefined;

export function openNewProjectPanel(extensionPath: string): void {
  // If already open, focus it
  if (currentPanel) {
    currentPanel.reveal();
    return;
  }

  currentPanel = vscode.window.createWebviewPanel(
    'cqs.newProject',
    'New Course Project',
    vscode.ViewColumn.One,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
    }
  );

  currentPanel.webview.html = getFormHtml();

  // Handle messages from the webview
  currentPanel.webview.onDidReceiveMessage(async (msg) => {
    switch (msg.type) {
      case 'browseFolder': {
        const uri = await vscode.window.showOpenDialog({
          canSelectFiles: false,
          canSelectFolders: true,
          canSelectMany: false,
          openLabel: 'Select Project Folder',
        });
        if (uri && uri.length > 0) {
          currentPanel?.webview.postMessage({
            type: 'folderSelected',
            path: uri[0].fsPath,
          });
        }
        break;
      }
      case 'browseToken': {
        const uri = await vscode.window.showOpenDialog({
          canSelectFiles: true,
          canSelectFolders: false,
          canSelectMany: false,
          openLabel: 'Select Token File',
          filters: { 'All files': ['*'] },
        });
        if (uri && uri.length > 0) {
          currentPanel?.webview.postMessage({
            type: 'tokenSelected',
            path: uri[0].fsPath,
          });
        }
        break;
      }
      case 'createProject': {
        await createProject(msg.data, extensionPath);
        break;
      }
    }
  });

  currentPanel.onDidDispose(() => {
    currentPanel = undefined;
  });
}

interface ProjectData {
  folder: string;
  courseName: string;
  courseCode: string;
  courseId: string;
  canvasApiUrl: string;
  tokenPath: string;
  semester: string;
  language: string;
  credits: string;
}

async function createProject(
  data: ProjectData,
  extensionPath: string
): Promise<void> {
  if (!data.folder) {
    vscode.window.showErrorMessage('Please select a project folder.');
    return;
  }

  try {
    const courseDir = data.folder;

    // Create folders
    for (const dir of ['01_Course_Info', 'graphics']) {
      const fullPath = path.join(courseDir, dir);
      if (!fs.existsSync(fullPath)) {
        fs.mkdirSync(fullPath, { recursive: true });
      }
    }

    // Write config.toml
    const configToml = [
      `course_id = ${data.courseId || '0'}`,
      `course_name = "${data.courseName || 'My Course'}"`,
      `course_code = "${data.courseCode || 'CODE'}"`,
      `credits = "${data.credits || '7.5 ECTS'}"`,
      `semester = "${data.semester}"`,
      `canvas_api_url = "${data.canvasApiUrl}"`,
      `canvas_token_path = "${(data.tokenPath || 'privateCanvasToken').replace(/\\/g, '/')}"`,
      `language = "${data.language || 'english'}"`,
    ].join('\n');

    fs.writeFileSync(path.join(courseDir, 'config.toml'), configToml, 'utf-8');

    // Copy _quarto.yml and run_sync_here.bat from Example folder
    const cqsRoot = resolveCqsRoot(extensionPath);
    const copies: [string, string][] = [
      [path.join(cqsRoot, 'Example', '_quarto.yml'), '_quarto.yml'],
      [path.join(cqsRoot, 'run_sync_here.bat'), 'run_sync_here.bat'],
    ];
    for (const [src, dst] of copies) {
      const dstPath = path.join(courseDir, dst);
      if (fs.existsSync(src) && !fs.existsSync(dstPath)) {
        fs.copyFileSync(src, dstPath);
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
        studyGuideTemplate(data.courseName || 'the course'),
        'utf-8'
      );
    }

    // Notify the webview
    currentPanel?.webview.postMessage({ type: 'projectCreated' });

    const action = await vscode.window.showInformationMessage(
      `Course project "${data.courseName}" created!`,
      'Open in VS Code',
      'Open config.toml'
    );

    if (action === 'Open in VS Code') {
      await vscode.commands.executeCommand(
        'vscode.openFolder',
        vscode.Uri.file(courseDir)
      );
    } else if (action === 'Open config.toml') {
      const doc = await vscode.workspace.openTextDocument(
        path.join(courseDir, 'config.toml')
      );
      await vscode.window.showTextDocument(doc);
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    vscode.window.showErrorMessage(`Failed to create project: ${msg}`);
  }
}

function getFormHtml(): string {
  const now = new Date();
  const season = now.getMonth() < 6 ? 'Spring' : 'Fall';
  const defaultSemester = `${season} ${now.getFullYear()}`;

  return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>New Course Project</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: var(--vscode-font-family);
      color: var(--vscode-foreground);
      background: var(--vscode-editor-background);
      padding: 0;
    }

    .header {
      background: var(--vscode-sideBar-background);
      border-bottom: 1px solid var(--vscode-panel-border);
      padding: 24px 32px;
    }

    .header h1 {
      font-size: 22px;
      font-weight: 600;
      color: var(--vscode-foreground);
      margin-bottom: 4px;
    }

    .header p {
      color: var(--vscode-descriptionForeground);
      font-size: 13px;
    }

    .form-container {
      max-width: 640px;
      margin: 0 auto;
      padding: 32px;
    }

    .section {
      margin-bottom: 28px;
    }

    .section-title {
      font-size: 13px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--vscode-descriptionForeground);
      margin-bottom: 12px;
      padding-bottom: 6px;
      border-bottom: 1px solid var(--vscode-panel-border);
    }

    .field {
      margin-bottom: 16px;
    }

    .field label {
      display: block;
      font-size: 13px;
      font-weight: 500;
      margin-bottom: 4px;
      color: var(--vscode-foreground);
    }

    .field .hint {
      font-size: 11px;
      color: var(--vscode-descriptionForeground);
      margin-bottom: 4px;
    }

    .field input,
    .field select {
      width: 100%;
      padding: 6px 10px;
      font-size: 13px;
      font-family: var(--vscode-font-family);
      color: var(--vscode-input-foreground);
      background: var(--vscode-input-background);
      border: 1px solid var(--vscode-input-border, var(--vscode-panel-border));
      border-radius: 4px;
      outline: none;
    }

    .field input:focus,
    .field select:focus {
      border-color: var(--vscode-focusBorder);
    }

    .browse-row {
      display: flex;
      gap: 8px;
    }

    .browse-row input {
      flex: 1;
    }

    .browse-row button {
      padding: 6px 14px;
      font-size: 13px;
      font-family: var(--vscode-font-family);
      color: var(--vscode-button-foreground);
      background: var(--vscode-button-secondaryBackground);
      border: none;
      border-radius: 4px;
      cursor: pointer;
      white-space: nowrap;
    }

    .browse-row button:hover {
      background: var(--vscode-button-secondaryHoverBackground);
    }

    .row {
      display: flex;
      gap: 16px;
    }

    .row .field {
      flex: 1;
    }

    .submit-area {
      margin-top: 32px;
      display: flex;
      gap: 12px;
      align-items: center;
    }

    .submit-btn {
      padding: 8px 24px;
      font-size: 14px;
      font-weight: 600;
      font-family: var(--vscode-font-family);
      color: var(--vscode-button-foreground);
      background: var(--vscode-button-background);
      border: none;
      border-radius: 4px;
      cursor: pointer;
    }

    .submit-btn:hover {
      background: var(--vscode-button-hoverBackground);
    }

    .submit-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .status {
      font-size: 13px;
      color: var(--vscode-descriptionForeground);
    }

    .status.success {
      color: var(--vscode-charts-green);
    }
  </style>
</head>
<body>
  <div class="header">
    <h1>New Course Project</h1>
    <p>Set up a new CanvasQuartoSync project. All fields can be changed later in config.toml.</p>
  </div>

  <div class="form-container">
    <!-- PROJECT LOCATION -->
    <div class="section">
      <div class="section-title">Project Location</div>
      <div class="field">
        <label>Project Folder</label>
        <div class="hint">Choose an empty folder where the course files will be created.</div>
        <div class="browse-row">
          <input type="text" id="folder" placeholder="Click Browse to select a folder..." readonly />
          <button onclick="browseFolder()">Browse</button>
        </div>
      </div>
    </div>

    <!-- COURSE INFO -->
    <div class="section">
      <div class="section-title">Course Information</div>
      <div class="row">
        <div class="field">
          <label>Course Name</label>
          <input type="text" id="courseName" value="My Course" placeholder="e.g. Mechatronics" />
        </div>
        <div class="field">
          <label>Course Code</label>
          <input type="text" id="courseCode" value="CODE" placeholder="e.g. TMRK16" />
        </div>
      </div>
      <div class="row">
        <div class="field">
          <label>Semester</label>
          <input type="text" id="semester" value="${defaultSemester}" placeholder="e.g. Spring 2026" />
        </div>
        <div class="field">
          <label>Credits</label>
          <input type="text" id="credits" value="7.5 ECTS" placeholder="e.g. 7.5 ECTS" />
        </div>
      </div>
      <div class="field">
        <label>Language</label>
        <select id="language">
          <option value="english" selected>English</option>
          <option value="swedish">Swedish</option>
        </select>
      </div>
    </div>

    <!-- CANVAS CONNECTION -->
    <div class="section">
      <div class="section-title">Canvas Connection</div>
      <div class="field">
        <label>Canvas Course ID</label>
        <div class="hint">The number in your Canvas course URL (e.g. canvas.university.edu/courses/<strong>12345</strong>).</div>
        <input type="text" id="courseId" value="0" placeholder="e.g. 12345" />
      </div>
      <div class="field">
        <label>Canvas API URL</label>
        <div class="hint">Your institution's Canvas API endpoint.</div>
        <input type="text" id="canvasApiUrl" value="https://your-institution.instructure.com/api/v1" />
      </div>
      <div class="field">
        <label>Token File</label>
        <div class="hint">A file containing your Canvas API token. Leave as default to set up later.</div>
        <div class="browse-row">
          <input type="text" id="tokenPath" value="privateCanvasToken" placeholder="Path to token file" />
          <button onclick="browseToken()">Browse</button>
        </div>
      </div>
    </div>

    <!-- SUBMIT -->
    <div class="submit-area">
      <button class="submit-btn" id="createBtn" onclick="createProject()">Create Project</button>
      <span class="status" id="status"></span>
    </div>
  </div>

  <script>
    const vscode = acquireVsCodeApi();

    function browseFolder() {
      vscode.postMessage({ type: 'browseFolder' });
    }

    function browseToken() {
      vscode.postMessage({ type: 'browseToken' });
    }

    function createProject() {
      const btn = document.getElementById('createBtn');
      const status = document.getElementById('status');
      btn.disabled = true;
      status.textContent = 'Creating project...';
      status.className = 'status';

      vscode.postMessage({
        type: 'createProject',
        data: {
          folder: document.getElementById('folder').value,
          courseName: document.getElementById('courseName').value,
          courseCode: document.getElementById('courseCode').value,
          courseId: document.getElementById('courseId').value,
          canvasApiUrl: document.getElementById('canvasApiUrl').value,
          tokenPath: document.getElementById('tokenPath').value,
          semester: document.getElementById('semester').value,
          language: document.getElementById('language').value,
          credits: document.getElementById('credits').value,
        },
      });
    }

    window.addEventListener('message', (event) => {
      const msg = event.data;
      switch (msg.type) {
        case 'folderSelected':
          document.getElementById('folder').value = msg.path;
          break;
        case 'tokenSelected':
          document.getElementById('tokenPath').value = msg.path;
          break;
        case 'projectCreated': {
          const btn = document.getElementById('createBtn');
          const status = document.getElementById('status');
          btn.disabled = false;
          status.textContent = 'Project created successfully!';
          status.className = 'status success';
          break;
        }
      }
    });
  </script>
</body>
</html>`;
}
