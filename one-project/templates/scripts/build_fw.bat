@echo off
rem ---------------------------------------------------------------
rem Command-line build of every Keil project firmware\*\MDK-ARM\*.uvprojx
rem (one-project template, lives in firmware\).
rem UV4.exe comes from the UV4 environment variable, else the usual install paths.
rem Build logs go to %TEMP% - process files never live in the project.
rem Exit code: 0 = every project built without errors, 1 = something failed.
rem KEEP THIS FILE PURE ASCII.
rem ---------------------------------------------------------------
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "UV4EXE=%UV4%"
if not defined UV4EXE if exist "%LOCALAPPDATA%\Keil_v5\UV4\UV4.exe" set "UV4EXE=%LOCALAPPDATA%\Keil_v5\UV4\UV4.exe"
if not defined UV4EXE if exist "C:\Keil_v5\UV4\UV4.exe" set "UV4EXE=C:\Keil_v5\UV4\UV4.exe"
if not defined UV4EXE (
    echo [ERROR] UV4.exe not found. Set the UV4 environment variable to its full path.
    exit /b 1
)

set "FAIL=0"
set "COUNT=0"
for /d %%B in ("%~dp0*") do (
    for %%P in ("%%~fB\MDK-ARM\*.uvprojx") do (
        set /a COUNT+=1
        set "LOG=%TEMP%\build_fw_%%~nP.log"
        "!UV4EXE!" -b "%%~fP" -j0 -o "!LOG!"
        if errorlevel 2 (
            echo [FAIL] %%~nxP  log: !LOG!
            set "FAIL=1"
        ) else (
            echo [OK]   %%~nxP
        )
    )
)
if "%COUNT%"=="0" (
    echo [ERROR] No firmware\*\MDK-ARM\*.uvprojx found.
    exit /b 1
)
exit /b %FAIL%
