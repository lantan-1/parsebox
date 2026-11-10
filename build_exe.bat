@echo off
cd /d %~dp0
rem NOTE: keep this file pure ASCII. UTF-8 Chinese text in .bat breaks cmd parsing on Chinese Windows.
python --version >nul 2>nul
if %errorlevel%==0 (set "PY=python") else (set "PY=py -3")
echo [1/3] Installing build deps (yt-dlp, imageio-ffmpeg, playwright, PyInstaller)...
%PY% -m pip install -r requirements.txt pyinstaller
echo [2/3] Building single-file exe with bundled ffmpeg (2-4 minutes)...
%PY% -m PyInstaller --noconfirm --onefile --windowed --name ParseBox --collect-all yt_dlp --collect-all imageio_ffmpeg --collect-all playwright ParseTool.py
echo [3/3] Done. The exe is at dist\ParseBox.exe - double-click to run, no Python or ffmpeg needed.
pause
