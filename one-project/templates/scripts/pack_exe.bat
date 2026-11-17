@echo off
rem ---------------------------------------------------------------
rem PyInstaller one-file build of the host GUI (one-project template, lives in host\).
rem This script is the single source of the packaging command; the .spec is not tracked.
rem Output: host\dist\<NAME>.exe   Intermediate: host\build\ (safe to delete)
rem Work/spec paths stay on the project drive (PyInstaller relpath fails across drives).
rem KEEP THIS FILE PURE ASCII.
rem ---------------------------------------------------------------
set "ENTRY=__ENTRY__.py"
set "NAME=__NAME__"
rem Extra PyInstaller options, e.g. --hidden-import serial.tools.list_ports
set "EXTRA=__EXTRA__"
cd /d "%~dp0"

python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] PyInstaller not found. Run: python -m pip install pyinstaller
    pause
    exit /b 1
)

python -m PyInstaller --noconfirm --clean --onefile --windowed --name "%NAME%" %EXTRA% --distpath dist --workpath build\pyi --specpath build "%ENTRY%"
if errorlevel 1 (
    echo [ERROR] PyInstaller failed.
    pause
    exit /b 1
)
echo [OK] dist\%NAME%.exe
exit /b 0
