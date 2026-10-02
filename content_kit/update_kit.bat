@echo off
setlocal
:: Refresh this folder's authoring kit (skill + reference docs + wrappers)
:: from the installed CanvasQuartoSync. Double-click to run.
::
:: Only the kit is touched: your content, config.toml, and any edits you made
:: to CLAUDE.md are left alone.

call :find_tool
if errorlevel 1 (
    pause
    exit /b 2
)

"%PYTHON%" "%TOOL_DIR%\init_content_project.py" "%~dp0." --update
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%

:: ---------------------------------------------------------------------------
:: Locate the tool and its Python. CANVAS_QUARTO_SYNC_DIR (a dev clone with a
:: .venv inside) wins and must be valid; otherwise the install location, then
:: the two older layouts.
:: ---------------------------------------------------------------------------
:find_tool
set "TOOL_DIR="
set "PYTHON="
if defined CANVAS_QUARTO_SYNC_DIR (
    call :try "%CANVAS_QUARTO_SYNC_DIR%" "%CANVAS_QUARTO_SYNC_DIR%\.venv\Scripts\python.exe"
    if not defined TOOL_DIR (
        echo [update_kit] CANVAS_QUARTO_SYNC_DIR is set to "%CANVAS_QUARTO_SYNC_DIR%",
        echo [update_kit] but there is no sync_to_canvas.py and .venv\Scripts\python.exe there.
        exit /b 1
    )
    exit /b 0
)
call :try "%LOCALAPPDATA%\CanvasQuartoSync" "%LOCALAPPDATA%\CanvasQuartoSync\.venv\Scripts\python.exe"
if not defined TOOL_DIR call :try "%USERPROFILE%\CanvasQuartoSync" "%USERPROFILE%\.venvs\canvas_quarto_env\Scripts\python.exe"
if not defined TOOL_DIR call :try "%USERPROFILE%\venvs\canvas_quarto_env\CanvasQuartoSync" "%USERPROFILE%\venvs\canvas_quarto_env\Scripts\python.exe"
if defined TOOL_DIR exit /b 0
echo [update_kit] CanvasQuartoSync not found. Looked in:
echo     %LOCALAPPDATA%\CanvasQuartoSync
echo     %USERPROFILE%\CanvasQuartoSync
echo     %USERPROFILE%\venvs\canvas_quarto_env\CanvasQuartoSync
echo [update_kit] Install it with install.ps1, or set CANVAS_QUARTO_SYNC_DIR to a clone.
exit /b 1

:try
if exist "%~1\sync_to_canvas.py" if exist "%~2" (
    set "TOOL_DIR=%~1"
    set "PYTHON=%~2"
)
exit /b 0
