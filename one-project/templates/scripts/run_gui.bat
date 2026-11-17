@echo off
rem ---------------------------------------------------------------
rem Host GUI one-click launcher (one-project template, lives in host\).
rem Double-click to run. Extra args are passed through (e.g. --smoke).
rem KEEP THIS FILE PURE ASCII: cmd mis-parses non-ASCII batch lines
rem whenever the console codepage differs from the file encoding.
rem Chinese messages belong in the Python layer.
rem ---------------------------------------------------------------
set "ENTRY=__ENTRY__.py"
title Host Launcher
cd /d "%~dp0"

if not exist "%ENTRY%" (
    echo [ERROR] %ENTRY% not found. Keep this script next to it in host\.
    pause
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.8+ and check "Add python.exe to PATH".
    pause
    exit /b 1
)

python -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] This Python has no tkinter. Reinstall from python.org with "tcl/tk and IDLE" checked.
    pause
    exit /b 1
)

python -c "import serial" >nul 2>nul
if errorlevel 1 (
    echo [NOTE] pyserial not installed. For real serial ports run: pip install pyserial
    timeout /t 5 >nul
)

rem Prefer pythonw (GUI without a console window); else fall back to python.
where pythonw >nul 2>nul
if not errorlevel 1 (
    start "Host" pythonw "%ENTRY%" %*
    exit /b 0
)
echo [NOTE] pythonw not found, starting with python (this window stays open)...
python "%ENTRY%" %*
if errorlevel 1 pause
exit /b 0
