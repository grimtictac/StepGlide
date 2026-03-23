# Python Music Player

Simple GUI music player built with Tkinter and VLC.

## Features
- Add individual audio files or an entire folder (recursively)
- Play / Pause / Stop / Next / Previous controls
- Volume control
- Filter and select tracks by genre (reads ID3 tags with `mutagen`)
- Display track comments in a separate column
- Auto-advance to next track when current song ends
- Support for many audio formats: MP3, WAV, OGG, FLAC, AAC, M4A, and more

## Requirements
- Python 3.11 or 3.12
- **VLC Media Player** (https://www.videolan.org/vlc/) - **REQUIRED**
- See `requirements.txt` for Python dependencies

## Installation

### Step 1: Install VLC
Download and install VLC Media Player from https://www.videolan.org/vlc/

### Step 2: Install Python Dependencies
```bash
python3 -m venv .venv
.venv/Scripts/activate  # On Windows
source .venv/bin/activate  # On Linux/Mac

pip install -r requirements.txt
```

## Run
```bash
python3 player.py
```

Or on Windows, double-click:
```
run_windows.bat
```

## Usage
- Use "Add Files" to add audio files
- Use "Add Folder" to recursively add all audio files from a directory
- Double-click a track to play it
- Use genre dropdown to filter tracks
- Volume slider controls audio volume
- Player auto-advances to next track when current song ends

## Platform Support
- ✅ Windows (10+)
- ✅ Linux
- ✅ macOS

## For Detailed Windows Setup
See [WINDOWS_INSTALL.md](WINDOWS_INSTALL.md) for step-by-step instructions.

## Notes
- Uses VLC media engine for superior audio format support and playback quality
- ID3 tag reading via `mutagen` library
- Cross-platform compatible code
