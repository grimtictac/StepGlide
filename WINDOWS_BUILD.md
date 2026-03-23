# Music Player - Windows Installation & Build Guide

## Quick Start (Pre-built Executable)

If you have a pre-built `MusicPlayer.exe`:
1. Download or copy `MusicPlayer.exe` to your desired location
2. Double-click `MusicPlayer.exe` to launch
3. **No installation needed** - it's a standalone executable!

---

## Building on Windows (From Source)

### Prerequisites
- Windows 10 or later
- **Python 3.11 or 3.12** (download from https://www.python.org/downloads/)
  - ⚠️ **IMPORTANT:** Do NOT use Python 3.14+ yet (pygame not available)
  - ⚠️ **IMPORTANT:** Check "Add Python to PATH" during installation
- **VLC Media Player** (required for audio playback)
  - Download from https://www.videolan.org/vlc/
  - Install normally (just click "Next" through the installer)

### Check Your Python Version
Run this first:
```cmd
check_python.bat
```

Or check manually:
```cmd
python --version
```

If you have Python 3.14:
- Download Python 3.12: https://www.python.org/downloads/release/python-3120/
- Install it with "Add to PATH" checked
- Use `python3.12` instead of `python` in commands below

### Install VLC (Important!)
1. Download VLC from https://www.videolan.org/vlc/
2. Run the installer
3. Click through the setup (defaults are fine)
4. Close the installer when done

**Why VLC is needed:** The music player uses the VLC media engine for audio playback, which supports many more formats than built-in Windows audio.

### Option 1: Automated Build (Easiest)

1. Extract the project folder to your desired location
2. Double-click `build_windows.bat`
3. Wait for the build to complete
4. Your executable will be in the `dist` folder as `MusicPlayer.exe`

### Option 2: Manual Build

Open Command Prompt or PowerShell and run:

```cmd
# Navigate to your project folder
cd path\to\MusicPlayer

# Create virtual environment
python -m venv .venv

# Activate virtual environment
.venv\Scripts\activate.bat

# Install dependencies
pip install -r requirements.txt

# Install PyInstaller
pip install pyinstaller

# Build executable
pyinstaller --onefile --windowed MusicPlayer.spec

# Your executable is now in: dist\MusicPlayer.exe
```

---

## Troubleshooting

### "Python is not installed or not in PATH"
- Reinstall Python from https://www.python.org/downloads/
- **IMPORTANT:** During installation, check the box "Add Python to PATH"

### "ModuleNotFoundError: No module named 'pygame'"
- Make sure you're in the virtual environment: `.venv\Scripts\activate.bat`
- Run: `pip install -r requirements.txt`

### Build takes a long time
- This is normal! PyInstaller bundles Python and all dependencies (~100-150 MB)
- First build takes longer; subsequent rebuilds are faster

### "The system cannot find the path specified"
- Make sure you extracted the folder properly
- Use absolute paths: `cd C:\Users\YourName\Desktop\MusicPlayer`

---

## File Formats Supported

- **Audio:** MP3, WAV, OGG, FLAC
- **Metadata:** ID3 tags (Title, Genre, Comments)

---

## Features

✅ Play/Pause/Stop/Next/Previous playback controls
✅ Volume slider
✅ Add files and folders to playlist
✅ Filter tracks by genre (extracted from ID3 tags)
✅ Display track comments in separate column
✅ Auto-advance to next track

---

## Distributing Your Executable

Once built, you can:

1. **Zip the executable** and share via email/cloud storage
   ```
   dist\MusicPlayer.exe
   ```

2. **Create an installer** (optional - requires NSIS)
   - Download NSIS from https://nsis.sourceforge.io/
   - Create a custom installer script
   - Users can install with "Next > Next > Finish"

3. **Share as-is** on GitHub or your website
   - Users just download and run `MusicPlayer.exe`

---

## System Requirements for Running the Executable

- Windows 10 or later
- ~500 MB free disk space
- Audio device with speakers/headphones
- No Python installation needed!

---

For more help, visit: https://pyinstaller.org/en/stable/
