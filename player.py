#!/usr/bin/env python3
"""A small music player using tkinter + VLC

Features:
- Add files / folders to a playlist
- Play / Pause / Stop / Next / Previous
- Volume control
- Filter by genre (reads tags via mutagen if available)

Run: python3 player.py
"""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import vlc
except Exception:
    print("Missing dependency: python-vlc. Install with: pip install python-vlc")
    raise

try:
    from mutagen import File as MutagenFile
except Exception:
    MutagenFile = None


class MusicPlayer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Python Music Player')
        self.geometry('640x360')

        # playlist: list of dicts {path, title, basename, genre, comment}
        self.playlist = []
        # display_indices maps the current listbox positions -> playlist indices
        self.display_indices = []
        self.genres = set()

        self.current_index = None
        self.is_playing = False
        self.is_paused = False
        self._last_action = None  # 'playing' | 'stopped' | 'paused'

        # VLC instance and player
        self.vlc_instance = vlc.Instance()
        self.vlc_player = self.vlc_instance.media_list_player_new()
        self.vlc_media_list = self.vlc_instance.media_list_new()

        self._build_ui()
        # poll to detect end of track
        self.after(500, self._poll)

    def _build_ui(self):
        main = ttk.Frame(self)
        main.pack(fill='both', expand=True, padx=8, pady=8)

        left = ttk.Frame(main)
        left.pack(side='left', fill='both', expand=True)

        # Create Treeview with three columns: Title, Genre, Comment
        self.tree = ttk.Treeview(left, columns=('Title', 'Genre', 'Comment'), show='headings', height=15)
        self.tree.column('Title', width=200, anchor='w')
        self.tree.column('Genre', width=80, anchor='w')
        self.tree.column('Comment', width=200, anchor='w')
        self.tree.heading('Title', text='Title')
        self.tree.heading('Genre', text='Genre')
        self.tree.heading('Comment', text='Comment')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<Double-1>', self._on_double)

        sb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        sb.pack(side='left', fill='y')
        self.tree.config(yscrollcommand=sb.set)

        ctrl = ttk.Frame(main)
        ctrl.pack(side='right', fill='y')

        ttk.Button(ctrl, text='Add Files', command=self.add_files).pack(fill='x', pady=2)
        ttk.Button(ctrl, text='Add Folder', command=self.add_folder).pack(fill='x', pady=2)
        ttk.Separator(ctrl, orient='horizontal').pack(fill='x', pady=6)

        # Genre filter
        ttk.Label(ctrl, text='Genre').pack(fill='x')
        self.genre_var = tk.StringVar(value='All')
        self.genre_box = ttk.Combobox(ctrl, textvariable=self.genre_var, state='readonly')
        self.genre_box.pack(fill='x', pady=2)
        self.genre_box.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())
        self._update_genre_options()

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
        # Initial volume set is deferred until VLC player is ready
        self._on_volume()

        ttk.Separator(ctrl, orient='horizontal').pack(fill='x', pady=6)
        self.lbl_status = ttk.Label(ctrl, text='Stopped', wraplength=180)
        self.lbl_status.pack(fill='x', pady=2)

    def add_files(self):
        files = filedialog.askopenfilenames(title='Select audio files', filetypes=[('Audio', '*.mp3 *.wav *.ogg *.flac'), ('All files', '*.*')])
        for f in files:
            self._add_path(f)
        if self.current_index is None and self.playlist:
            self.current_index = 0
        self._update_genre_options()
        self._apply_filter()

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
                    if self._add_path(path):
                        added += 1
        if added == 0:
            messagebox.showinfo('No files', 'No supported audio files found in folder')
        if self.current_index is None and self.playlist:
            self.current_index = 0
        self._update_genre_options()
        self._apply_filter()

    def _add_path(self, path):
        # returns True if added
        if any(t['path'] == path for t in self.playlist):
            return False
        title = os.path.basename(path)
        genre = 'Unknown'
        comment = ''
        if MutagenFile is not None:
            try:
                tags = MutagenFile(path, easy=True)
                if tags is not None:
                    title = tags.get('title', [title])[0]
                    genre = tags.get('genre', [genre])[0]
                    # Extract comment (may be a list or single value)
                    comment_val = tags.get('comment', [''])[0]
                    comment = str(comment_val) if comment_val else ''
            except Exception:
                # ignore metadata read errors
                pass
        entry = {'path': path, 'title': title, 'basename': os.path.basename(path), 'genre': genre, 'comment': comment}
        self.playlist.append(entry)
        self.genres.add(genre)
        return True

    def _update_genre_options(self):
        vals = ['All'] + sorted(x for x in self.genres if x)
        self.genre_box['values'] = vals
        if self.genre_var.get() not in vals:
            self.genre_var.set('All')

    def _apply_filter(self):
        sel = self.genre_var.get()
        # Clear all items from tree
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.display_indices = []
        for idx, entry in enumerate(self.playlist):
            if sel == 'All' or not sel or entry.get('genre') == sel:
                title = entry.get('title', entry['basename'])
                genre = entry.get('genre', '')
                comment = entry.get('comment', '')
                self.tree.insert('', 'end', values=(title, genre, comment))
                self.display_indices.append(idx)

    def _load(self, index):
        if index is None or index < 0 or index >= len(self.playlist):
            return False
        path = self.playlist[index]['path']
        try:
            # Create VLC media and add to player
            media = self.vlc_instance.media_new(path)
            # Clear existing media by recreating the media list
            self.vlc_media_list = self.vlc_instance.media_list_new()
            self.vlc_media_list.add_media(media)
            self.vlc_player.set_media_list(self.vlc_media_list)
            
            self.current_index = index
            # highlight in current view if present
            # Clear all selections first
            for item in self.tree.selection():
                self.tree.selection_remove(item)
            try:
                pos = self.display_indices.index(index)
                all_items = self.tree.get_children()
                if pos < len(all_items):
                    item = all_items[pos]
                    self.tree.selection_set(item)
                    self.tree.see(item)
            except ValueError:
                # not in filtered view
                pass
            self.lbl_status.config(text=f'Loaded: {os.path.basename(path)}')
            return True
        except Exception as e:
            messagebox.showerror('Error', f'Could not load {path}: {e}')
            return False

    def play_pause(self):
        if self.is_playing and not self.is_paused:
            # pause
            self.vlc_player.pause()
            self.is_paused = True
            self.is_playing = False
            self._last_action = 'paused'
            self.btn_play.config(text='Play')
            self.lbl_status.config(text='Paused')
            return

        if self.is_paused:
            self.vlc_player.play()
            self.is_paused = False
            self.is_playing = True
            self._last_action = 'playing'
            self.btn_play.config(text='Pause')
            if self.current_index is not None:
                self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")
            return

        # not playing -> start
        if not self.playlist:
            messagebox.showinfo('No tracks', 'Add some audio files first')
            return
        if self.current_index is None:
            # start with first visible track in filtered view
            if self.display_indices:
                self.current_index = self.display_indices[0]
            else:
                self.current_index = 0
        loaded = self._load(self.current_index)
        if not loaded:
            return
        try:
            self.vlc_player.play()
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self.btn_play.config(text='Pause')
            self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")
        except Exception as e:
            messagebox.showerror('Playback error', str(e))

    def stop(self):
        self.vlc_player.stop()
        self.is_playing = False
        self.is_paused = False
        self._last_action = 'stopped'
        self.btn_play.config(text='Play')
        self.lbl_status.config(text='Stopped')

    def next_track(self):
        if not self.playlist:
            return
        # If a genre filter is active, advance within the filtered list
        if self.genre_var.get() != 'All' and self.display_indices:
            try:
                pos = self.display_indices.index(self.current_index)
            except ValueError:
                pos = 0
            next_pos = (pos + 1) % len(self.display_indices)
            nxt = self.display_indices[next_pos]
        else:
            nxt = 0 if self.current_index is None else (self.current_index + 1) % len(self.playlist)
        self._load(nxt)
        self.vlc_player.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self.btn_play.config(text='Pause')
        self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")

    def prev_track(self):
        if not self.playlist:
            return
        if self.genre_var.get() != 'All' and self.display_indices:
            try:
                pos = self.display_indices.index(self.current_index)
            except ValueError:
                pos = 0
            prev_pos = (pos - 1) % len(self.display_indices)
            prev = self.display_indices[prev_pos]
        else:
            prev = 0 if self.current_index is None else (self.current_index - 1) % len(self.playlist)
        self._load(prev)
        self.vlc_player.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self.btn_play.config(text='Pause')
        self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")

    def _on_volume(self, _=None):
        v = float(self.vol.get())
        # VLC volume is 0-100
        self.vlc_player.get_media_player().audio_set_volume(int(v * 100))

    def _on_double(self, ev):
        sel = self.tree.selection()
        if not sel:
            return
        # sel is a tuple of item IDs; get the first selected item
        item = sel[0]
        # Find the index of this item in the tree's children
        all_items = self.tree.get_children()
        try:
            idx = all_items.index(item)
            playlist_idx = self.display_indices[idx]
        except Exception:
            return
        self.current_index = playlist_idx
        loaded = self._load(playlist_idx)
        if loaded:
            self.vlc_player.play()
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self.btn_play.config(text='Pause')
            self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")

    def _poll(self):
        # Called periodically to detect end of track and auto-advance
        # VLC returns -1 when no media is playing
        is_playing = self.vlc_player.is_playing()
        # If nothing is playing, and we expected playing, advance to next
        if not is_playing and self._last_action == 'playing' and not self.is_paused:
            # small safety: if playlist has more than one entry
            if self.playlist:
                # advance respecting filter
                if self.genre_var.get() != 'All' and self.display_indices:
                    try:
                        pos = self.display_indices.index(self.current_index)
                    except ValueError:
                        pos = 0
                    next_pos = (pos + 1) % len(self.display_indices)
                    next_idx = self.display_indices[next_pos]
                else:
                    next_idx = (self.current_index + 1) % len(self.playlist) if self.current_index is not None else 0
                # if only one track, stop
                if len(self.playlist) == 1:
                    self.stop()
                else:
                    self._load(next_idx)
                    self.vlc_player.play()
                    self.is_playing = True
                    self.is_paused = False
                    self._last_action = 'playing'
                    self.btn_play.config(text='Pause')
                    self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")

        self.after(500, self._poll)


def main():
    app = MusicPlayer()
    app.mainloop()


if __name__ == '__main__':
    main()
