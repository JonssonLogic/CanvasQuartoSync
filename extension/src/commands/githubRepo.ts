import * as vscode from 'vscode';
import * as path from 'path';
import { execFile, spawn } from 'child_process';
import { resolvePython, resolveCqsRoot } from '../python/venvResolver';
import { getWorkspaceRoot } from '../config/configLoader';

// ── GitHub: create the course repository, invite teachers ─────────────
//
// Both commands run github_course.py, the same code as `cqs github`, so the
// checks and the order of steps live in one place. The extension only adds
// what a terminal cannot: VS Code's own GitHub sign-in, passed to the script
// as GH_TOKEN, so nobody has to install or log in to the GitHub CLI.

const TERMINAL_NAME = 'Canvas GitHub';
const LAST_ORG_KEY = 'cqs.githubLastOrg';
const NAME_RE = /^[A-Za-z0-9._-]+$/;

interface OrgList {
  login: string;
  last_org: string | null;
  orgs: { login: string; role: string }[];
}

async function githubToken(): Promise<string | undefined> {
  try {
    const session = await vscode.authentication.getSession(
      'github', ['repo', 'read:org'], { createIfNone: true });
    return session.accessToken;
  } catch {
    vscode.window.showErrorMessage('Sign-in to GitHub was cancelled.');
    return undefined;
  }
}

function originUrl(root: string): Promise<string | undefined> {
  return new Promise((resolve) => {
    execFile('git', ['remote', 'get-url', 'origin'], { cwd: root }, (err, stdout) =>
      resolve(err ? undefined : stdout.trim()));
  });
}

function runScript(python: string, args: string[], root: string, token: string):
    Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    execFile(python, args, {
      cwd: root,
      env: { ...process.env, GH_TOKEN: token, PYTHONIOENCODING: 'utf-8' },
    }, (err, stdout, stderr) => resolve({
      code: err ? (typeof err.code === 'number' ? err.code : 1) : 0, stdout, stderr,
    }));
  });
}

function runInTerminal(python: string, args: string[], root: string, token: string,
                       title: string): Promise<number> {
  vscode.window.terminals.filter((t) => t.name === TERMINAL_NAME).forEach((t) => t.dispose());
  return new Promise((resolve) => {
    const write = new vscode.EventEmitter<string>();
    const pty: vscode.Pseudoterminal = {
      onDidWrite: write.event,
      open() {
        write.fire(`\x1b[1m> ${title}\x1b[0m\r\n\r\n`);
        const proc = spawn(python, args, {
          cwd: root,
          env: { ...process.env, GH_TOKEN: token, PYTHONIOENCODING: 'utf-8' },
        });
        proc.stdout?.on('data', (d: Buffer) => write.fire(d.toString().replace(/\n/g, '\r\n')));
        proc.stderr?.on('data', (d: Buffer) =>
          write.fire(`\x1b[31m${d.toString().replace(/\n/g, '\r\n')}\x1b[0m`));
        proc.on('close', (code) => {
          write.fire(code === 0 ? '\r\n\x1b[32m✔ Done.\x1b[0m\r\n'
                                : `\r\n\x1b[31m✖ Failed (exit code ${code}).\x1b[0m\r\n`);
          resolve(code ?? 1);
        });
        proc.on('error', (e) => {
          write.fire(`\x1b[31mError: ${e.message}\x1b[0m\r\n`);
          resolve(1);
        });
      },
      close() {},
    };
    vscode.window.createTerminal({ name: TERMINAL_NAME, pty }).show();
  });
}

function setup(extensionPath: string) {
  const root = getWorkspaceRoot();
  if (!root) {
    vscode.window.showErrorMessage('Open a course folder first.');
    return undefined;
  }
  const python = resolvePython();
  if (!python) {
    vscode.window.showErrorMessage('Python virtual environment not found.');
    return undefined;
  }
  const script = path.join(resolveCqsRoot(extensionPath), 'github_course.py');
  return { root, python, script };
}

export async function createGithubRepo(context: vscode.ExtensionContext): Promise<void> {
  const s = setup(context.extensionPath);
  if (!s) return;

  const existing = await originUrl(s.root);
  if (existing) {
    vscode.window.showInformationMessage(`This course is already on GitHub: ${existing}`);
    return;
  }

  const token = await githubToken();
  if (!token) return;

  const listed = await runScript(s.python, [s.script, s.root, 'orgs', '--json'], s.root, token);
  if (listed.code !== 0) {
    vscode.window.showErrorMessage(listed.stderr.trim() || 'Could not list your GitHub organisations.');
    return;
  }
  const info: OrgList = JSON.parse(listed.stdout);
  if (info.orgs.length === 0) {
    vscode.window.showErrorMessage(
      `${info.login} is not in any GitHub organisation where you may create repositories. ` +
      'Ask an owner of the organisation to invite you.');
    return;
  }

  // The organisation is chosen, never guessed. The last choice comes first.
  const last = context.globalState.get<string>(LAST_ORG_KEY) ?? info.last_org ?? undefined;
  const items = info.orgs
    .map((o) => ({ label: o.login, description: o.login === last ? `${o.role}, last used` : o.role }))
    .sort((a, b) => (a.label === last ? -1 : b.label === last ? 1 : 0));
  const org = await vscode.window.showQuickPick(items, {
    title: `Create GitHub Repository (signed in as ${info.login})`,
    placeHolder: 'Organisation for the course repository',
  });
  if (!org) return;

  const name = await vscode.window.showInputBox({
    title: 'Repository name',
    value: path.basename(s.root),
    prompt: 'Course code and the year the course starts, e.g. T1H2UO-26',
    validateInput: (v) => NAME_RE.test(v) ? null : "Letters, digits, '.', '-' and '_' only",
  });
  if (!name) return;

  const ok = await vscode.window.showInformationMessage(
    `Create the private repository ${org.label}/${name}?`,
    {
      modal: true,
      detail: 'Everything in the course folder is committed and pushed, the course switches ' +
              'to team mode, and the sync map is committed. Student data and tokens are not.',
    },
    'Create');
  if (ok !== 'Create') return;

  await context.globalState.update(LAST_ORG_KEY, org.label);
  const code = await runInTerminal(
    s.python, [s.script, s.root, '--org', org.label, '--name', name, '--yes'],
    s.root, token, `Creating ${org.label}/${name}`);
  if (code === 0) {
    const open = await vscode.window.showInformationMessage(
      `The course is on GitHub: ${org.label}/${name}`, 'Open on GitHub', 'Invite a teacher');
    if (open === 'Open on GitHub') {
      vscode.env.openExternal(vscode.Uri.parse(`https://github.com/${org.label}/${name}`));
    } else if (open === 'Invite a teacher') {
      vscode.commands.executeCommand('cqs.inviteToGithub');
    }
  }
}

export async function inviteToGithub(context: vscode.ExtensionContext): Promise<void> {
  const s = setup(context.extensionPath);
  if (!s) return;

  if (!(await originUrl(s.root))) {
    vscode.window.showErrorMessage('This course is not on GitHub yet. Use "Create GitHub Repository" first.');
    return;
  }
  const input = await vscode.window.showInputBox({
    title: 'Invite teachers to the course on GitHub',
    prompt: 'GitHub usernames, separated by spaces or commas',
    validateInput: (v) => v.trim() ? null : 'At least one username',
  });
  if (!input) return;
  const users = input.split(/[\s,]+/).filter(Boolean);

  const token = await githubToken();
  if (!token) return;
  await runInTerminal(s.python, [s.script, s.root, 'invite', ...users], s.root, token,
                      `Inviting ${users.join(', ')}`);
}
