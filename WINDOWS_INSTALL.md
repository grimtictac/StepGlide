# Windows Installation Steps - Detailed

Follow these steps **in order** to get the Music Player working on Windows:

## Step 1: Install VLC Media Player (REQUIRED)

1. Go to https://www.videolan.org/vlc/
2. Click the big "Download VLC" button
3. Run the installer (VLC-3.x.x-win64.exe or similar)
4. Click "Next" through all steps (default settings are fine)
5. Click "Finish" when complete
6. **Close/exit VLC** if it launches after installation

✅ **VLC is now installed!**

---

## Step 2: Check Python Version

Run this command in PowerShell or Command Prompt:

```cmd
python --version
```

**You should see Python 3.11 or 3.12** (NOT 3.14)

If you see Python 3.14:
- Download Python 3.12 from: https://www.python.org/downloads/release/python-3120/
- Install it
- Make sure "Add Python to PATH" is checked
- After install, restart PowerShell/Command Prompt and try `python --version` again

---

## Step 3: Set Up Virtual Environment

```cmd
cd path\to\MusicPlayer
python -m venv .venv
.venv\Scripts\activate
```

You should see `(.venv)` in your terminal prompt.

---

## Step 4: Install Dependencies

```cmd
pip install -r requirements.txt
```

This installs:
- python-vlc (interface to VLC)
- mutagen (reads music metadata)

---

## Step 5: Run the Music Player

```cmd
python player.py
```

Or double-click:
```cmd
run_windows.bat
```

✅ **Music Player should open!**

---

## Troubleshooting

### "libvlc.dll not found" error
- **Solution:** Install VLC Media Player from https://www.videolan.org/vlc/
- Make sure to use the standard installer, not the portable version

### "ModuleNotFoundError: No module named 'vlc'"
- **Solution:** Make sure you're in the virtual environment
  ```cmd
  .venv\Scripts\activate
  ```
- Then reinstall: `pip install python-vlc`

### "Python 3.14 not compatible"
- **Solution:** Download and install Python 3.12
- Use `python3.12` instead of `python` in commands

### "Permission denied" or "Cannot find path"
- **Solution:** Make sure the folder path doesn't have spaces or special characters
- Extract to: `C:\Users\YourName\Music\MusicPlayer`

---

## Building the Windows Executable

Once everything is working:

```cmd
.venv\Scripts\activate
pip install pyinstaller
pyinstaller --onefile --windowed --name "MusicPlayer" player.py
```

Your `MusicPlayer.exe` will be in the `dist\` folder.

---

**Questions?** Check the main README.md or WINDOWS_BUILD.md
