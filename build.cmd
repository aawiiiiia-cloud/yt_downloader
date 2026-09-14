@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title yt-dlp GUI Builder

set "BUILD_PYTHON="
set "BUILD_ARGS="

rem Prefer the project-managed Python 3.13 environment.
if exist "%USERPROFILE%\.yt_dlp_gui_venv\Scripts\python.exe" goto use_project_venv

rem Then try the standard Python launcher.
where py.exe >nul 2>nul
if errorlevel 1 goto check_local_python
py.exe -3.13 -c "import sys; assert sys.version_info[:2] == (3, 13)" >nul 2>nul
if not errorlevel 1 goto use_python_launcher

:check_local_python
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" goto use_local_python
if exist "%ProgramFiles%\Python313\python.exe" goto use_program_files_python

where python.exe >nul 2>nul
if errorlevel 1 goto python_missing
python.exe -c "import sys; assert sys.version_info[:2] == (3, 13)" >nul 2>nul
if errorlevel 1 goto python_missing
set "BUILD_PYTHON=python.exe"
goto python_ready

:use_project_venv
set "BUILD_PYTHON=%USERPROFILE%\.yt_dlp_gui_venv\Scripts\python.exe"
goto python_ready

:use_python_launcher
set "BUILD_PYTHON=py.exe"
set "BUILD_ARGS=-3.13"
goto python_ready

:use_local_python
set "BUILD_PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
goto python_ready

:use_program_files_python
set "BUILD_PYTHON=%ProgramFiles%\Python313\python.exe"
goto python_ready

:python_missing
echo.
echo [ERROR] Python 3.13 x64 was not found.
echo Install Python 3.13 x64, then run build.cmd again.
echo https://www.python.org/downloads/
echo.
pause
exit /b 1

:python_ready
echo Python: "%BUILD_PYTHON%" %BUILD_ARGS%
"%BUILD_PYTHON%" %BUILD_ARGS% --version
if errorlevel 1 goto python_missing

if /i "%~1"=="--check" (
    echo [OK] Build environment launcher check passed.
    exit /b 0
)

echo.
echo Build started. Do not close this window.
echo.
"%BUILD_PYTHON%" %BUILD_ARGS% build.py
set "BUILD_RESULT=%ERRORLEVEL%"
echo.
if "%BUILD_RESULT%"=="0" goto build_ok

echo [FAILED] Build did not finish. The previous dist output is preserved.
goto finish

:build_ok
echo [OK] Distribution files were generated in the dist folder.

:finish
echo.
pause
exit /b %BUILD_RESULT%
