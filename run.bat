@echo off
cd /d %~dp0
rem NOTE: keep this file pure ASCII. UTF-8 Chinese text in .bat breaks cmd parsing on Chinese Windows.
python --version >nul 2>nul
if %errorlevel%==0 (set "PY=python") else (set "PY=py -3")
echo Checking dependencies (yt-dlp, imageio-ffmpeg, playwright). First run installs them automatically...
%PY% -c "import yt_dlp, imageio_ffmpeg, playwright" 2>nul || %PY% -m pip install -r requirements.txt
echo Starting ParseBox...
%PY% ParseTool.py
pause
