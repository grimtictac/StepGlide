@echo off
REM Run Music Player from source on Windows

if not exist ".venv" (
    echo Setting up environment for first time...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -q -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

python player.py
pause
