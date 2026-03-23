@echo off
REM Build script for creating Windows executable with PyInstaller

echo Creating Music Player Windows executable...
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation
    pause
    exit /b 1
)

REM Create virtual environment if it doesn't exist
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM Activate virtual environment
echo Activating virtual environment...
call .venv\Scripts\activate.bat

REM Install dependencies
echo Installing dependencies...
pip install -q -r requirements.txt

REM Install PyInstaller
echo Installing PyInstaller...
pip install -q pyinstaller

REM Build executable
echo Building Windows executable...
.venv\Scripts\pyinstaller.exe --onefile --windowed --name "MusicPlayer" player.py

echo.
echo Build complete!
echo Your executable is ready in: dist\MusicPlayer.exe
echo.
pause
