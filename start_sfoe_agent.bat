@echo off
setlocal
title SFOE Open Energy Knowledge Agent

REM ============================================================
REM SFOE OPEN ENERGY KNOWLEDGE AGENT - WINDOWS LAUNCHER
REM ============================================================
REM
REM GOAL:
REM   1. Use the correct Python interpreter
REM   2. Check/install dependencies
REM   3. Run the MCP gateway preflight test
REM   4. Start Streamlit only if the gateway test succeeds
REM
REM Put this BAT file in the repository root next to:
REM   test_gateway.py
REM   streamlit_app.py
REM   agent_app.py
REM   requirements.txt
REM   .env
REM ============================================================

cd /d "%~dp0"

echo.
echo ============================================================
echo  SFOE Open Energy Knowledge Agent
echo ============================================================
echo.

REM ============================================================
REM STEP 1 - CHOOSE PYTHON
REM ============================================================
REM Prefer the Miniconda Python used successfully for this project.
REM Fall back to python / py only if Miniconda is not available.
REM ============================================================

set "MINICONDA_PYTHON=%LOCALAPPDATA%\miniconda3\python.exe"

if exist "%MINICONDA_PYTHON%" (
    set "PYTHON_CMD=%MINICONDA_PYTHON%"
    goto :python_found
)

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

echo ERROR: Python was not found.
echo.
echo Please install Python or Miniconda and try again.
echo.
pause
exit /b 1


:python_found

echo Using Python:
"%PYTHON_CMD%" --version
echo.
echo Python executable:
"%PYTHON_CMD%" -c "import sys; print(sys.executable)"
echo.


REM ============================================================
REM STEP 2 - CHECK REQUIRED FILES
REM ============================================================

if not exist "streamlit_app.py" (
    echo ERROR: streamlit_app.py was not found.
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

if not exist "test_gateway.py" (
    echo ERROR: test_gateway.py was not found.
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
    echo The application will probably not be able to authenticate.
    echo.
)


REM ============================================================
REM STEP 3 - CHECK DEPENDENCIES
REM ============================================================

echo Checking Python dependencies...
echo.

"%PYTHON_CMD%" -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ERROR: Could not install the required Python packages.
    echo.
    pause
    exit /b 1
)

echo.
echo Dependencies are ready.
echo.


REM ============================================================
REM STEP 4 - GATEWAY PREFLIGHT / WARM-UP
REM ============================================================
REM In the current environment, running test_gateway.py first
REM makes the subsequent Streamlit connection reliable.
REM
REM The UI starts only if this test succeeds.
REM ============================================================

echo ============================================================
echo  Checking SFOE MCP gateway...
echo ============================================================
echo.

"%PYTHON_CMD%" test_gateway.py

if errorlevel 1 (
    echo.
    echo ============================================================
    echo  ERROR: SFOE gateway preflight failed.
    echo ============================================================
    echo.
    echo The Streamlit application was NOT started.
    echo.
    echo Please check:
    echo   - Internet / VPN connection
    echo   - .env configuration
    echo   - AWS / Cognito access
    echo   - SFOE MCP gateway availability
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Gateway check successful.
echo ============================================================
echo.


REM ============================================================
REM STEP 5 - START STREAMLIT
REM ============================================================

echo Starting the SFOE Agent...
echo.
echo Your browser should open automatically.
echo Keep this window open while using the application.
echo Press Ctrl+C to stop the app.
echo.

"%PYTHON_CMD%" -m streamlit run streamlit_app.py

if errorlevel 1 (
    echo.
    echo ERROR: Streamlit stopped because of an error.
    echo.
    pause
)

endlocal
