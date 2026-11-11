#!/bin/sh
# ParseBox launcher for Linux / macOS. Keep this file ASCII-only.
cd "$(dirname "$0")"
PY=python3
command -v $PY >/dev/null 2>&1 || PY=python
echo "Checking dependencies (yt-dlp, imageio-ffmpeg, playwright)..."
$PY -c "import yt_dlp, imageio_ffmpeg, playwright" 2>/dev/null || $PY -m pip install -r requirements.txt
echo "Starting ParseBox..."
$PY ParseTool.py
