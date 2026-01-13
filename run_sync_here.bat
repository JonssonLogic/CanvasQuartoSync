@echo off
setlocal

:: --- Configuration ---
:: Point this to your CanvasAPI project directory
set "PROJECT_DIR=C:\Users\CV\MyCodeProjects\CanvasAPI"

:: --- Execution ---
echo Starting Canvas Sync...
echo Content Directory: %~dp0

:: Run the python script from the project using its virtual environment
:: %~dp0 refers to the directory where this .bat file is located (the content content)
:: %* allows passing additional arguments (like --sync-calendar)
"%PROJECT_DIR%\.venv\Scripts\python.exe" "%PROJECT_DIR%\sync_to_canvas.py" "%~dp0." %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ! Sync encountered an error.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo Sync Complete.
pause
