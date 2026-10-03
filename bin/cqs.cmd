@echo off
setlocal
:: cqs on the PATH. The installer adds this folder, and only this folder, to
:: the user PATH: the venv's Scripts would put a second python ahead of the
:: user's own. CANVAS_QUARTO_SYNC_DIR (a dev clone with a .venv inside) wins,
:: the same rule as check_content and the extension.
if defined CANVAS_QUARTO_SYNC_DIR (
    set "TOOL=%CANVAS_QUARTO_SYNC_DIR%"
) else (
    set "TOOL=%~dp0.."
)
if not exist "%TOOL%\cqs.py" goto :missing
if not exist "%TOOL%\.venv\Scripts\python.exe" goto :missing
"%TOOL%\.venv\Scripts\python.exe" "%TOOL%\cqs.py" %*
exit /b %ERRORLEVEL%

:missing
echo [cqs] No CanvasQuartoSync with a .venv at "%TOOL%".
if defined CANVAS_QUARTO_SYNC_DIR echo [cqs] CANVAS_QUARTO_SYNC_DIR is set; unset it to use the installed copy.
exit /b 1
