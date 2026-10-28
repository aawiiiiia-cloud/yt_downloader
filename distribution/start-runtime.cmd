@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "MEDIA_DL_CMD_LAUNCH=1"
set "PYTHONHOME="
set "PYTHONPATH="
set "VIRTUAL_ENV="
set "TCL_LIBRARY="
set "TK_LIBRARY="
set "media_python="
for /d %%D in ("%~dp0..\..\*") do if exist "%%~fD\python\python.exe" set "media_python=%%~fD\python\python.exe"
if defined media_python call :probe
if defined media_python if not errorlevel 1 goto launch
set "media_python=%YT_DOWNLOADER_PYTHON%"
if defined media_python call :probe
if defined media_python if not errorlevel 1 goto launch
set "media_python=D:\python-envs\yt-downloader\Scripts\python.exe"
call :probe
if not errorlevel 1 goto launch
set "media_tools=%YT_DOWNLOADER_TOOLS_DIR%"
if not defined media_tools set "media_tools=%USERPROFILE%\.yt_dlp_tools"
set "media_python=%media_tools%\python\python.exe"
call :probe
if not errorlevel 1 goto launch
set "media_mode=collector"
set "media_flags="
for %%A in (%*) do if /i "%%~A"=="legacy" set "media_mode=legacy"
for %%A in (%*) do if /i "%%~A"=="--legacy" set "media_mode=legacy"
for %%A in (%*) do if /i "%%~A"=="--check" set "media_flags=-CheckOnly"
for %%A in (%*) do if /i "%%~A"=="--check-env" set "media_flags=-CheckOnly"
for %%A in (%*) do if /i "%%~A"=="--test-electron" set "media_flags=-DesktopTest"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure-runtime.ps1" -Mode %media_mode% %media_flags%
goto finish
:launch
"%media_python%" -I -B -X utf8 "%~dp0launcher.py" %*
:finish
set "media_result=%errorlevel%"
if not "%media_result%"=="0" pause
exit /b %media_result%
:probe
if not exist "%media_python%" exit /b 1
"%media_python%" -I -B -c "import sys,ssl,json,pathlib,subprocess; assert sys.version_info[:2]==(3,13)" >nul 2>nul
exit /b %errorlevel%
