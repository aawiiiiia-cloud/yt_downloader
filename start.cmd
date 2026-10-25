@echo off
chcp 65001 >nul
setlocal
title media-dl
set "media_flags="
if /i "%~1"=="--check" set "media_flags=-CheckOnly"
if /i "%~1"=="--check-env" set "media_flags=-CheckOnly"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0distribution\ensure-runtime.ps1" -Mode legacy %media_flags%
set "media_result=%errorlevel%"
if not "%media_result%"=="0" pause
exit /b %media_result%
