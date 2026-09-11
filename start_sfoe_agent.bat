@echo off
setlocal
title SFOE Open Energy Knowledge Agent

REM ============================================================
REM GOAL
REM ============================================================
REM Double-click this file to start the SFOE Streamlit app.
REM
REM REQUIRED FILES IN THE SAME FOLDER:
REM   streamlit_app.py
REM   agent_app.py
REM   requirements.txt
REM   .env
REM ============================================================

REM Always run from the folder where this BAT file is located.
cd /d "%~dp0"

echo.
echo ============================================================
echo  SFOE Open Energy Knowledge Agent
echo ============================================================
echo.

REM ------------------------------------------------------------
REM Find Python.
REM Prefer "python"; fall back to Windows "py".
REM ------------------------------------------------------------

where python >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=python"
    goto :python_found
)

where py >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=py"
    goto :python_found
)

echo ERROR: Python was not found on this computer.
echo.
echo Please install Python 3.11 or newer and try again.
echo.
pause
exit /b 1

:python_found

echo Using Python:
%PYTHON_CMD% --version
echo.

REM ------------------------------------------------------------
REM Check required project files.
REM ------------------------------------------------------------

if not exist "streamlit_app.py" (
    echo ERROR: streamlit_app.py was not found.
    echo Put this BAT file in the same folder as streamlit_app.py.
    echo.
    pause
    exit /b 1
)

if not exist "agent_app.py" (
    echo ERROR: agent_app.py was not found.
    echo.
    pause
    exit /b 1
)

if not exist "requirements.txt" (
    echo ERROR: requirements.txt was not found.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo WARNING: .env was not found.
    echo The application may not be able to authenticate.
    echo.
)

REM ------------------------------------------------------------
REM Install required packages.
REM pip will normally report "Requirement already satisfied"
REM after the first successful run.
REM ------------------------------------------------------------

echo Checking Python dependencies...
%PYTHON_CMD% -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ERROR: Could not install the required Python packages.
    echo Check your internet connection and Python installation.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting the SFOE Agent...
echo Your browser should open automatically.
echo.
echo Keep this window open while using the application.
echo Close it or press Ctrl+C to stop the app.
echo.

REM ------------------------------------------------------------
REM Start Streamlit.
REM ------------------------------------------------------------

%PYTHON_CMD% -m streamlit run streamlit_app.py

if errorlevel 1 (
    echo.
    echo ERROR: The SFOE Agent stopped because of an error.
    echo.
    pause
)

endlocal
