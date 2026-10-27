import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import * as TOML from '@iarna/toml';

export interface CqsConfig {
  course_id?: string;
  course_name?: string;
  course_code?: string;
  credits?: string;
  semester?: string;
  canvas_api_url?: string;
  canvas_api_token?: string;
  canvas_token_path?: string;
  language?: string;
  branding_css?: string;
  [key: string]: unknown;
}

/**
 * Loads config.toml from the workspace root and merges with environment variables.
 * Resolution priority (highest wins):
 * 1. Environment variables (CANVAS_API_URL, CANVAS_API_TOKEN)
 * 2. config.toml values
 */
export function loadConfig(workspaceRoot: string): CqsConfig | undefined {
  const configPath = path.join(workspaceRoot, 'config.toml');
  if (!fs.existsSync(configPath)) {
    return undefined;
  }

  const raw = fs.readFileSync(configPath, 'utf-8');
  const parsed = TOML.parse(raw) as unknown as CqsConfig;

  // Environment variables override config.toml
  if (process.env.CANVAS_API_URL) {
    parsed.canvas_api_url = process.env.CANVAS_API_URL;
  }
  if (process.env.CANVAS_API_TOKEN) {
    parsed.canvas_api_token = process.env.CANVAS_API_TOKEN;
  }

  // Resolve token from file if not set directly
  if (!parsed.canvas_api_token && parsed.canvas_token_path) {
    const tokenFile = path.resolve(workspaceRoot, parsed.canvas_token_path);
    if (fs.existsSync(tokenFile)) {
      parsed.canvas_api_token = fs.readFileSync(tokenFile, 'utf-8').trim();
    }
  }

  return parsed;
}

/**
 * Returns the course folder a file belongs to: the nearest folder upwards
 * with a config.toml, the same rule the cqs CLI uses. Works whether the
 * workspace is the course, a parent of it, or something else entirely.
 */
export function findCourseRoot(filePath: string): string | undefined {
  let dir = path.dirname(path.resolve(filePath));
  while (true) {
    if (fs.existsSync(path.join(dir, 'config.toml'))) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) {
      return undefined;
    }
    dir = parent;
  }
}

/**
 * Picks the course to work on when the workspace may hold more than one:
 * the course of the open file, else the workspace root if it is a course,
 * else the course folder directly under the root (asking when there are
 * several). Falls back to the workspace root.
 */
export async function resolveCourseRoot(): Promise<string | undefined> {
  const active = vscode.window.activeTextEditor?.document;
  if (active && active.uri.scheme === 'file') {
    const fromFile = findCourseRoot(active.uri.fsPath);
    if (fromFile) {
      return fromFile;
    }
  }
  const ws = getWorkspaceRoot();
  if (!ws || fs.existsSync(path.join(ws, 'config.toml'))) {
    return ws;
  }
  const courses = fs.readdirSync(ws, { withFileTypes: true })
    .filter(d => d.isDirectory() && fs.existsSync(path.join(ws, d.name, 'config.toml')))
    .map(d => d.name)
    .sort();
  if (courses.length === 1) {
    return path.join(ws, courses[0]);
  }
  if (courses.length > 1) {
    const pick = await vscode.window.showQuickPick(courses, { placeHolder: 'Which course?' });
    return pick ? path.join(ws, pick) : undefined;
  }
  return ws;
}

// The course the Course Board opened, so its actions (rollups, diffs)
// work on the same folder even when the workspace is a parent of it.
let boardCourseRoot: string | undefined;

export function setCourseRoot(root: string | undefined): void {
  boardCourseRoot = root;
}

/** The course the board is showing, else the workspace root. */
export function getCourseRoot(): string | undefined {
  return boardCourseRoot ?? getWorkspaceRoot();
}

/**
 * Returns the workspace root path, or undefined if no workspace is open.
 */
export function getWorkspaceRoot(): string | undefined {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    return undefined;
  }
  return folders[0].uri.fsPath;
}
