@echo off
setlocal
:: Refresh this folder's authoring kit (skill + reference docs + wrappers)
:: from the installed CanvasQuartoSync. Double-click to run.
::
:: Only the kit is touched: your content, config.toml, and any edits you made
:: to CLAUDE.md are left alone.

set "PYTHON=@@PYTHON@@"
set "TOOL_DIR=@@REPO@@"

if not exist "%PYTHON%" (
    echo [update_kit] Python not found at: %PYTHON%
    echo [update_kit] The tool may have moved. Re-scaffold with:
    echo     python init_content_project.py "%~dp0." --update
    pause
    exit /b 2
)

"%PYTHON%" "%TOOL_DIR%\init_content_project.py" "%~dp0." --update
echo.
pause
exit /b %ERRORLEVEL%
