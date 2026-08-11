@echo off
setlocal
:: Offline content validation for this course folder.
:: Paths are stamped in by init_content_project.py - re-run it with --update
:: if the tool moves.

set "PYTHON=@@PYTHON@@"
set "TOOL_DIR=@@REPO@@"

if not exist "%PYTHON%" (
    echo [check_content] Python not found at: %PYTHON%
    echo [check_content] Re-run init_content_project.py --update from the tool folder.
    exit /b 2
)

if "%~1"=="" (
    "%PYTHON%" "%TOOL_DIR%\validate_content.py" "%~dp0." --content-root "%~dp0."
) else (
    "%PYTHON%" "%TOOL_DIR%\validate_content.py" %* --content-root "%~dp0."
)
exit /b %ERRORLEVEL%
