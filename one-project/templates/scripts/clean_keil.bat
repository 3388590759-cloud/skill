@echo off
rem ---------------------------------------------------------------
rem Delete Keil intermediate build files under firmware\
rem (one-project template, lives in tools\, replaces keilkill.bat).
rem Keeps .hex/.bin, .sct scatter files, .uvprojx/.uvoptx and all sources.
rem KEEP THIS FILE PURE ASCII.
rem ---------------------------------------------------------------
cd /d "%~dp0.."
if not exist "firmware\" (
    echo [ERROR] firmware\ not found. Keep this script in tools\ of a one-project layout.
    exit /b 1
)
for %%E in (o d crf lst map axf lnp dep iex __i tra plg rpt) do del /s /q "firmware\*.%%E" >nul 2>nul
del /s /q "firmware\JLinkLog.txt" >nul 2>nul
echo [OK] Keil intermediate files removed under firmware\
exit /b 0
