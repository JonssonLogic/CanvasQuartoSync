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
 * Returns the workspace root path, or undefined if no workspace is open.
 */
export function getWorkspaceRoot(): string | undefined {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    return undefined;
  }
  return folders[0].uri.fsPath;
}
