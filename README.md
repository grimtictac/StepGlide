# Python Music Player

Simple GUI music player built with Tkinter and pygame.

Features
- Add individual audio files or an entire folder (recursively).
- Play / Pause / Stop / Next / Previous controls.
- Volume control.

Requirements
- Python 3.8+
- See `requirements.txt` (pygame)

Install
```bash
python3 -m pip install -r requirements.txt
```

Run
```bash
python3 player.py
```

Usage
- Use "Add Files" to add audio files (mp3, wav, ogg, flac).
- Use "Add Folder" to add all audio files from a directory.
- Double-click a track in the list to play it.

Notes
- This is a small demonstration player. It focuses on playback control and a simple playlist UI.
- If you want duration/progress display, we can add the `mutagen` dependency to read file lengths and a seek slider.
