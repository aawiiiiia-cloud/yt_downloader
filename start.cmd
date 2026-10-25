@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
if exist "%~dp0..\启动程序\启动旧版.cmd" goto bundled_launcher
if not defined YT_DOWNLOADER_PYTHON set "YT_DOWNLOADER_PYTHON=D:\python-envs\yt-downloader\Scripts\python.exe"
if not exist "%YT_DOWNLOADER_PYTHON%" (
    echo Python environment not found: %YT_DOWNLOADER_PYTHON%
    echo Set YT_DOWNLOADER_PYTHON to the project's python.exe.
    pause
    exit /b 1
)
if /i "%~1"=="--check-env" goto check_environment
pushd "%~dp0"
if errorlevel 1 exit /b 1
"%YT_DOWNLOADER_PYTHON%" -X utf8 "%~dp0yt_dlp_gui.py"
set "launch_result=%errorlevel%"
popd
if not "%launch_result%"=="0" pause
exit /b %launch_result%

:check_environment
"%YT_DOWNLOADER_PYTHON%" -B -X utf8 -c "import sys, PIL, pillow_heif, dhash, tkinter; print('Python:', sys.executable); print('Pillow:', PIL.__version__); print('Pillow location:', PIL.__file__); print('HEIF:', pillow_heif.__version__); print('Tkinter:', tkinter.TkVersion)"
exit /b %errorlevel%

:bundled_launcher
if /i "%~1"=="--check-env" goto bundled_check
call "%~dp0..\启动程序\启动旧版.cmd" %*
exit /b %errorlevel%

:bundled_check
call "%~dp0..\启动程序\启动旧版.cmd" --check
exit /b %errorlevel%
