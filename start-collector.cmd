@echo off
setlocal
title media-dl
set "PYTHONUTF8=1"
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist "node_modules\electron\cli.js" (
    echo Please run npm.cmd install in this folder first.
    pause
    popd
    exit /b 1
)
if not defined YT_DOWNLOADER_PYTHON set "YT_DOWNLOADER_PYTHON=D:\python-envs\yt-downloader\Scripts\python.exe"
set "ELECTRON_RUN_AS_NODE="
call npm.cmd start
set "collector_result=%errorlevel%"
popd
if not "%collector_result%"=="0" pause
exit /b %collector_result%
