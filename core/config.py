"""
XML configuration load/save.
Pure Python, no UI dependencies.
"""

import os
import xml.etree.ElementTree as ET

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'music_player_config.xml')


# ── Default tooltip texts ────────────────────────────────
DEFAULT_TOOLTIPS = {
    'mute': 'Mute / Unmute',
    'menu': 'Menu — Add Files / Folders',
    'thumbs_up': 'Like (Shift+click for voter picker)',
    'thumbs_down': 'Dislike (Shift+click for voter picker)',
    'voter': 'Select who is voting',
    'play': 'Play / Pause',
    'stop': 'Stop',
    'play_now': 'Play selected track now',
    'play_next': 'Add selected track to front of queue',
    'speed_down': 'Decrease speed',
    'speed_reset': 'Reset speed to 1×',
    'speed_up': 'Increase speed',
    'auto_reset_speed': 'Auto-reset speed to 1× when song changes',
    'equalizer': 'Equalizer',
    'clear_queue': 'Clear queue',
    'queue_up': 'Move up in queue',
    'queue_down': 'Move down in queue',
    'queue_top': 'Jump to top of queue',
    'queue_remove': 'Remove from queue',
    'queue_random': 'Random queue generator',
    'send_to_queue': 'Add selected tracks to queue',
    'settings': 'Settings',
    'new_playlist': 'New playlist',
    'reset_filters': 'Reset all filters',
    'jump_to_playing': 'Jump to now playing track',
}


class AppConfig:
    """Holds all persistent configuration. No UI references."""

    def __init__(self, config_path=None):
        self.config_path = config_path or CONFIG_PATH
        self.library_root = ''
        self.genre_groups = {}
        self.length_filter_durations = [
            ('< 2 min', 0, 120),
            ('2 – 4 min', 120, 240),
            ('4 – 7 min', 240, 420),
            ('> 7 min', 420, None),
        ]
        self.all_tags = set()
        self.tag_rows = {}  # tag_name → row number
        self.playlists = {}  # name → [file_path, ...]
        self.tooltip_texts = dict(DEFAULT_TOOLTIPS)
        self.queue_btn_throb_enabled = True
        self.saved_voter = ''
        self.visible_columns = None  # list or None

    def load(self):
        """Load settings from XML config file. Returns True if file existed."""
        if not os.path.exists(self.config_path):
            return False
        tree = ET.parse(self.config_path)
        root = tree.getroot()

        # Library root
        lib_el = root.find('library_root')
        if lib_el is not None and lib_el.text:
            self.library_root = lib_el.text

        # Genre groups
        self.genre_groups = {}
        groups_el = root.find('genre_groups')
        if groups_el is not None:
            for group_el in groups_el.findall('group'):
                gname = group_el.get('name', '')
                members = [m.text for m in group_el.findall('member') if m.text]
                self.genre_groups[gname] = members

        # Length filter durations
        durations_el = root.find('length_filter_durations')
        if durations_el is not None:
            durations = []
            for dur_el in durations_el.findall('duration'):
                label = dur_el.get('label', '')
                lo = dur_el.get('lo')
                hi = dur_el.get('hi')
                lo = int(lo) if lo else None
                hi = int(hi) if hi else None
                durations.append((label, lo, hi))
            if durations:
                self.length_filter_durations = durations

        # Tags
        tags_el = root.find('tags')
        if tags_el is not None:
            for tag_el in tags_el.findall('tag'):
                name = tag_el.get('name', '').strip().lower()
                if name:
                    self.all_tags.add(name)
                    row = tag_el.get('row')
                    if row is not None:
                        self.tag_rows[name] = int(row)

        # Playlists
        playlists_el = root.find('playlists')
        if playlists_el is not None:
            self.playlists = {}
            for pl_el in playlists_el.findall('playlist'):
                name = pl_el.get('name', '')
                paths = [t.text for t in pl_el.findall('track') if t.text]
                self.playlists[name] = paths

        # Tooltips
        tooltips_el = root.find('tooltips')
        if tooltips_el is not None:
            for tip_el in tooltips_el.findall('tip'):
                key = tip_el.get('key', '')
                text = tip_el.get('text', '')
                if key and text:
                    self.tooltip_texts[key] = text

        # Interface settings
        iface_el = root.find('interface')
        if iface_el is not None:
            val = iface_el.get('queue_btn_throb', 'true')
            self.queue_btn_throb_enabled = val.lower() != 'false'
            self.saved_voter = iface_el.get('voter', '')

        # Visible columns
        vis_el = root.find('visible_columns')
        if vis_el is not None:
            cols_text = vis_el.text
            if cols_text:
                self.visible_columns = [c.strip() for c in cols_text.split(',') if c.strip()]

        return True

    def save(self, voter_name=''):
        """Save all settings to XML config file."""
        root = ET.Element('music_player_config')

        # Library root
        lib_el = ET.SubElement(root, 'library_root')
        lib_el.text = self.library_root or ''

        # Genre groups
        groups_el = ET.SubElement(root, 'genre_groups')
        for gname, members in self.genre_groups.items():
            group_el = ET.SubElement(groups_el, 'group', name=gname)
            for member in members:
                m_el = ET.SubElement(group_el, 'member')
                m_el.text = member

        # Length filter durations
        durations_el = ET.SubElement(root, 'length_filter_durations')
        for label, lo, hi in self.length_filter_durations:
            attrs = {'label': label}
            if lo is not None:
                attrs['lo'] = str(lo)
            if hi is not None:
                attrs['hi'] = str(hi)
            ET.SubElement(durations_el, 'duration', **attrs)

        # Tags
        tags_el = ET.SubElement(root, 'tags')
        for tag in sorted(self.all_tags):
            attrs = {'name': tag}
            if tag in self.tag_rows:
                attrs['row'] = str(self.tag_rows[tag])
            ET.SubElement(tags_el, 'tag', **attrs)

        # Playlists
        playlists_el = ET.SubElement(root, 'playlists')
        for name, paths in self.playlists.items():
            pl_el = ET.SubElement(playlists_el, 'playlist', name=name)
            for path in paths:
                t_el = ET.SubElement(pl_el, 'track')
                t_el.text = path

        # Tooltips (only overrides)
        tooltips_el = ET.SubElement(root, 'tooltips')
        for key in sorted(self.tooltip_texts):
            text = self.tooltip_texts[key]
            default = DEFAULT_TOOLTIPS.get(key, '')
            if text != default:
                ET.SubElement(tooltips_el, 'tip', key=key, text=text)

        # Interface settings
        ET.SubElement(root, 'interface',
                      queue_btn_throb=str(self.queue_btn_throb_enabled).lower(),
                      voter=voter_name or self.saved_voter)

        # Visible columns
        if self.visible_columns is not None:
            vis_el = ET.SubElement(root, 'visible_columns')
            vis_el.text = ','.join(self.visible_columns)

        ET.indent(root)
        tree = ET.ElementTree(root)
        tree.write(self.config_path, encoding='unicode', xml_declaration=True)
