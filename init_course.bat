@echo off
setlocal

:: --- Canvas Course Project Initializer ---
:: Copy this file to an empty folder and double-click to initialize a course project.
:: It will create config.toml, _quarto.yml, and an example study guide.

set "COURSE_DIR=%~dp0"
set "SCRIPT_DIR=%~dp0"

:: Try to find CanvasQuartoSync installation
:: Check if we're inside the CanvasQuartoSync repo (init_course.bat hasn't been copied yet)
if exist "%SCRIPT_DIR%handlers\qmd_preprocessor.py" (
    set "CQS_DIR=%SCRIPT_DIR%"
) else if exist "%SCRIPT_DIR%CanvasQuartoSync\handlers\qmd_preprocessor.py" (
    set "CQS_DIR=%SCRIPT_DIR%CanvasQuartoSync\"
) else (
    :: Search common install locations
    for %%d in (
        "%USERPROFILE%\venvs\canvas_quarto_env\CanvasQuartoSync"
        "%USERPROFILE%\CanvasQuartoSync"
    ) do (
        if exist "%%~d\handlers\qmd_preprocessor.py" set "CQS_DIR=%%~d\"
    )
)

echo.
echo  Canvas Course Project Initializer
echo  ==================================
echo.
echo  Project folder: %COURSE_DIR%
echo.

:: Copy _quarto.yml from CanvasQuartoSync Example folder
if defined CQS_DIR (
    if exist "%CQS_DIR%Example\_quarto.yml" (
        if not exist "%COURSE_DIR%_quarto.yml" (
            copy /Y "%CQS_DIR%Example\_quarto.yml" "%COURSE_DIR%_quarto.yml" >nul
            echo  [OK] _quarto.yml
        ) else (
            echo  [SKIP] _quarto.yml already exists
        )
    )
    :: Copy run_sync_here.bat
    if exist "%CQS_DIR%run_sync_here.bat" (
        if not exist "%COURSE_DIR%run_sync_here.bat" (
            copy /Y "%CQS_DIR%run_sync_here.bat" "%COURSE_DIR%run_sync_here.bat" >nul
            echo  [OK] run_sync_here.bat
        ) else (
            echo  [SKIP] run_sync_here.bat already exists
        )
    )
) else (
    echo  [WARN] CanvasQuartoSync installation not found.
    echo         Copy _quarto.yml and run_sync_here.bat manually.
)

:: Create folder structure
if not exist "%COURSE_DIR%01_Course_Info" mkdir "%COURSE_DIR%01_Course_Info"
if not exist "%COURSE_DIR%graphics" mkdir "%COURSE_DIR%graphics"

:: Generate config.toml
if not exist "%COURSE_DIR%config.toml" (
    (
        echo course_id = 0
        echo course_name = "My Course"
        echo course_code = "CODE"
        echo credits = "7.5 ECTS"
        echo semester = "Spring 2026"
        echo canvas_api_url = "https://your-institution.instructure.com/api/v1"
        echo canvas_token_path = "privateCanvasToken"
        echo language = "english"
    ) > "%COURSE_DIR%config.toml"
    echo  [OK] config.toml
) else (
    echo  [SKIP] config.toml already exists
)

:: Generate example study guide
if not exist "%COURSE_DIR%01_Course_Info\01_StudyGuide.qmd" (
    (
        echo ---
        echo title: "Course PM"
        echo canvas:
        echo   type: study_guide
        echo   preprocess: true
        echo   published: true
        echo   pdf:
        echo     target_module: "Course Documents"
        echo     filename: "KursPM.pdf"
        echo     title: "Course PM (PDF^)"
        echo     published: true
        echo ---
        echo.
        echo # Introduction
        echo.
        echo Welcome to the course.
        echo.
        echo # Schedule
        echo.
        echo ^| Week ^| Topic ^| Activity ^|
        echo ^|:-----^|:------^|:---------^|
        echo ^| 1 ^| Introduction ^| Lecture ^|
        echo ^| 2 ^| Fundamentals ^| Lab 1 ^|
        echo.
        echo # Grading Criteria
        echo.
        echo ^| ILO ^| Fail ^| 3 ^| 4 ^| 5 ^|
        echo ^|:----^|:-----^|:--^|:--^|:--^|
        echo ^| Understanding of core concepts ^| Cannot explain basics ^| Can explain concepts ^| Can relate and compare ^| Can analyze in depth ^|
        echo.
        echo # Teaching Staff
        echo.
        echo ^| Name ^| Role ^| Image ^| Link ^|
        echo ^|:-----^|:-----^|:------^|:-----^|
        echo ^| Your Name ^| Course responsible ^| photo.png ^| https://example.com ^|
        echo.
        echo # Research Connection
        echo.
        echo See the education plan for research connection details.
    ) > "%COURSE_DIR%01_Course_Info\01_StudyGuide.qmd"
    echo  [OK] 01_Course_Info\01_StudyGuide.qmd
) else (
    echo  [SKIP] StudyGuide.qmd already exists
)

echo.
echo  Project ready!
echo.
echo  Next steps:
echo    1. Edit config.toml with your course ID, name, and Canvas credentials
echo    2. Edit 01_Course_Info\01_StudyGuide.qmd with your course content
echo    3. Double-click run_sync_here.bat to sync to Canvas
echo.
pause
