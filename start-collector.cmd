@echo off
setlocal DisableDelayedExpansion
title media-dl
call "%~dp0distribution\start-runtime.cmd" --mode collector %*
exit /b %errorlevel%
