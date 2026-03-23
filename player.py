#!/usr/bin/env python3
"""A small music player using tkinter + pygame.mixer

Features:
- Add files / folders to a playlist
- Play / Pause / Stop / Next / Previous
- Volume control

Run: python3 player.py
"""
import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import pygame
except Exception as e:
    print("Missing dependency: pygame. Install with: pip install pygame")
    raise


class MusicPlayer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Python Music Player')
        self.geometry('640x360')
        self.playlist = []  # list of file paths
        self.current_index = None
        self.is_playing = False
        self.is_paused = False
        self._last_action = None  # 'playing' | 'stopped' | 'paused'

        pygame.mixer.init()

        self._build_ui()
        # poll to detect end of track
        self.after(500, self._poll)

    def _build_ui(self):
        main = ttk.Frame(self)
        main.pack(fill='both', expand=True, padx=8, pady=8)

        left = ttk.Frame(main)
        left.pack(side='left', fill='both', expand=True)

        self.listbox = tk.Listbox(left, activestyle='none')
        self.listbox.pack(side='left', fill='both', expand=True)
        self.listbox.bind('<Double-1>', self._on_double)

        sb = ttk.Scrollbar(left, orient='vertical', command=self.listbox.yview)
        sb.pack(side='left', fill='y')
        self.listbox.config(yscrollcommand=sb.set)

        ctrl = ttk.Frame(main)
        ctrl.pack(side='right', fill='y')

        ttk.Button(ctrl, text='Add Files', command=self.add_files).pack(fill='x', pady=2)
        ttk.Button(ctrl, text='Add Folder', command=self.add_folder).pack(fill='x', pady=2)
        ttk.Separator(ctrl, orient='horizontal').pack(fill='x', pady=6)

        btn_frame = ttk.Frame(ctrl)
        btn_frame.pack(fill='x')
        ttk.Button(btn_frame, text='Prev', command=self.prev_track).grid(row=0, column=0, padx=2)
        self.btn_play = ttk.Button(btn_frame, text='Play', command=self.play_pause)
        self.btn_play.grid(row=0, column=1, padx=2)
        ttk.Button(btn_frame, text='Stop', command=self.stop).grid(row=0, column=2, padx=2)
        ttk.Button(btn_frame, text='Next', command=self.next_track).grid(row=0, column=3, padx=2)

        ttk.Separator(ctrl, orient='horizontal').pack(fill='x', pady=6)

        vol_frame = ttk.Frame(ctrl)
        vol_frame.pack(fill='x')
        ttk.Label(vol_frame, text='Volume').pack(side='left')
        self.vol = tk.DoubleVar(value=0.8)
        vol = ttk.Scale(vol_frame, from_=0.0, to=1.0, orient='horizontal', variable=self.vol, command=self._on_volume)
        vol.pack(side='left', fill='x', expand=True, padx=6)
        pygame.mixer.music.set_volume(self.vol.get())

        ttk.Separator(ctrl, orient='horizontal').pack(fill='x', pady=6)
        self.lbl_status = ttk.Label(ctrl, text='Stopped', wraplength=180)
        self.lbl_status.pack(fill='x', pady=2)

    def add_files(self):
        files = filedialog.askopenfilenames(title='Select audio files', filetypes=[('Audio', '*.mp3 *.wav *.ogg *.flac'), ('All files', '*.*')])
        for f in files:
            if f not in self.playlist:
                self.playlist.append(f)
                self.listbox.insert('end', os.path.basename(f))
        if self.current_index is None and self.playlist:
            self.current_index = 0

    def add_folder(self):
        folder = filedialog.askdirectory(title='Select folder')
        if not folder:
            return
        exts = ('.mp3', '.wav', '.ogg', '.flac')
        added = 0
        for root, _, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(exts):
                    path = os.path.join(root, name)
                    if path not in self.playlist:
                        self.playlist.append(path)
                        self.listbox.insert('end', os.path.basename(path))
                        added += 1
        if added == 0:
            messagebox.showinfo('No files', 'No supported audio files found in folder')
        if self.current_index is None and self.playlist:
            self.current_index = 0

    def _load(self, index):
        if index is None or index < 0 or index >= len(self.playlist):
            return False
        path = self.playlist[index]
        try:
            pygame.mixer.music.load(path)
            self.current_index = index
            # highlight
            self.listbox.select_clear(0, 'end')
            self.listbox.select_set(index)
            self.listbox.see(index)
            self.lbl_status.config(text=f'Loaded: {os.path.basename(path)}')
            return True
        except Exception as e:
            messagebox.showerror('Error', f'Could not load {path}: {e}')
            return False

    def play_pause(self):
        if self.is_playing and not self.is_paused:
            # pause
            pygame.mixer.music.pause()
            self.is_paused = True
            self.is_playing = False
            self._last_action = 'paused'
            self.btn_play.config(text='Play')
            self.lbl_status.config(text='Paused')
            return

        if self.is_paused:
            pygame.mixer.music.unpause()
            self.is_paused = False
            self.is_playing = True
            self._last_action = 'playing'
            self.btn_play.config(text='Pause')
            self.lbl_status.config(text=f'Playing: {os.path.basename(self.playlist[self.current_index])}')
            return

        # not playing -> start
        if not self.playlist:
            messagebox.showinfo('No tracks', 'Add some audio files first')
            return
        if self.current_index is None:
            self.current_index = 0
        loaded = self._load(self.current_index)
        if not loaded:
            return
        try:
            pygame.mixer.music.play()
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self.btn_play.config(text='Pause')
            self.lbl_status.config(text=f'Playing: {os.path.basename(self.playlist[self.current_index])}')
        except Exception as e:
            messagebox.showerror('Playback error', str(e))

    def stop(self):
        pygame.mixer.music.stop()
        self.is_playing = False
        self.is_paused = False
        self._last_action = 'stopped'
        self.btn_play.config(text='Play')
        self.lbl_status.config(text='Stopped')

    def next_track(self):
        if not self.playlist:
            return
        nxt = 0 if self.current_index is None else (self.current_index + 1) % len(self.playlist)
        self._load(nxt)
        pygame.mixer.music.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self.btn_play.config(text='Pause')
        self.lbl_status.config(text=f'Playing: {os.path.basename(self.playlist[self.current_index])}')

    def prev_track(self):
        if not self.playlist:
            return
        prev = 0 if self.current_index is None else (self.current_index - 1) % len(self.playlist)
        self._load(prev)
        pygame.mixer.music.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self.btn_play.config(text='Pause')
        self.lbl_status.config(text=f'Playing: {os.path.basename(self.playlist[self.current_index])}')

    def _on_volume(self, _=None):
        v = float(self.vol.get())
        pygame.mixer.music.set_volume(v)

    def _on_double(self, ev):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self.current_index = idx
        loaded = self._load(idx)
        if loaded:
            pygame.mixer.music.play()
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self.btn_play.config(text='Pause')
            self.lbl_status.config(text=f'Playing: {os.path.basename(self.playlist[self.current_index])}')

    def _poll(self):
        # Called periodically to detect end of track and auto-advance
        busy = pygame.mixer.music.get_busy()
        # If nothing is busy, and we expected playing, advance to next
        if not busy and self._last_action == 'playing' and not self.is_paused:
            # small safety: if playlist has more than one entry
            if self.playlist:
                # advance
                next_idx = (self.current_index + 1) % len(self.playlist) if self.current_index is not None else 0
                # if only one track, stop
                if len(self.playlist) == 1:
                    self.stop()
                else:
                    self._load(next_idx)
                    pygame.mixer.music.play()
                    self.is_playing = True
                    self.is_paused = False
                    self._last_action = 'playing'
                    self.btn_play.config(text='Pause')
                    self.lbl_status.config(text=f'Playing: {os.path.basename(self.playlist[self.current_index])}')

        self.after(500, self._poll)


def main():
    app = MusicPlayer()
    app.mainloop()


if __name__ == '__main__':
    main()
