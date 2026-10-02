@echo off
setlocal
:: Sync this folder to Canvas. Copy into a content folder and double-click;
:: extra arguments (like --sync-calendar) are passed on to sync_to_canvas.py.
:: Finds the installed CanvasQuartoSync itself - see :find_tool below.

call :find_tool
if errorlevel 1 (
    pause
    exit /b 2
)

echo Starting Canvas Sync...
echo Content Directory: %~dp0
echo Tool: %TOOL_DIR%

"%PYTHON%" "%TOOL_DIR%\sync_to_canvas.py" "%~dp0." %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo ! Sync encountered an error.
    pause
    exit /b %RC%
)

echo.
echo Sync Complete.
pause
exit /b 0

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
        echo [run_sync_here] CANVAS_QUARTO_SYNC_DIR is set to "%CANVAS_QUARTO_SYNC_DIR%",
        echo [run_sync_here] but there is no sync_to_canvas.py and .venv\Scripts\python.exe there.
        exit /b 1
    )
    exit /b 0
)
call :try "%LOCALAPPDATA%\CanvasQuartoSync" "%LOCALAPPDATA%\CanvasQuartoSync\.venv\Scripts\python.exe"
if not defined TOOL_DIR call :try "%USERPROFILE%\CanvasQuartoSync" "%USERPROFILE%\.venvs\canvas_quarto_env\Scripts\python.exe"
if not defined TOOL_DIR call :try "%USERPROFILE%\venvs\canvas_quarto_env\CanvasQuartoSync" "%USERPROFILE%\venvs\canvas_quarto_env\Scripts\python.exe"
if defined TOOL_DIR exit /b 0
echo [run_sync_here] CanvasQuartoSync not found. Looked in:
echo     %LOCALAPPDATA%\CanvasQuartoSync
echo     %USERPROFILE%\CanvasQuartoSync
echo     %USERPROFILE%\venvs\canvas_quarto_env\CanvasQuartoSync
echo [run_sync_here] Install it with install.ps1, or set CANVAS_QUARTO_SYNC_DIR to a clone.
exit /b 1

:try
if exist "%~1\sync_to_canvas.py" if exist "%~2" (
    set "TOOL_DIR=%~1"
    set "PYTHON=%~2"
)
exit /b 0
