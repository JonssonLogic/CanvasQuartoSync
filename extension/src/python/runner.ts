import * as vscode from 'vscode';
import { spawn, ChildProcess } from 'child_process';

export interface RunResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

/**
 * Strips Rich markup tags ([bold], [cyan], [/bold], etc.) and ANSI escape codes
 * from Python output so progress messages are clean.
 */
function stripFormatting(text: string): string {
  // Strip ANSI escape codes
  let clean = text.replace(/\x1b\[[0-9;]*m/g, '');
  // Strip Rich markup tags like [bold], [/cyan], [green], etc.
  clean = clean.replace(/\[\/?\w[\w\s]*\]/g, '');
  return clean.trim();
}

/**
 * Runs a Python script inside the resolved venv with progress reporting.
 */
export async function runPythonScript(
  pythonPath: string,
  scriptPath: string,
  args: string[],
  cwd: string,
  progress?: vscode.Progress<{ message?: string; increment?: number }>,
  token?: vscode.CancellationToken
): Promise<RunResult> {
  return new Promise((resolve, reject) => {
    const proc: ChildProcess = spawn(pythonPath, [scriptPath, ...args], {
      cwd,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });

    let stdout = '';
    let stderr = '';

    if (token) {
      token.onCancellationRequested(() => {
        proc.kill();
      });
    }

    proc.stdout?.on('data', (data: Buffer) => {
      const text = data.toString();
      stdout += text;

      if (progress) {
        // Report the last non-empty line as the progress message
        const lines = text.split('\n').filter((l: string) => l.trim());
        if (lines.length > 0) {
          progress.report({ message: stripFormatting(lines[lines.length - 1]) });
        }
      }
    });

    proc.stderr?.on('data', (data: Buffer) => {
      stderr += data.toString();
    });

    proc.on('close', (code) => {
      resolve({
        exitCode: code ?? 1,
        stdout,
        stderr,
      });
    });

    proc.on('error', (err) => {
      reject(err);
    });
  });
}
