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
        self.length_filter_durations = [
            ('< 2 min', 0, 120),
            ('2 – 4 min', 120, 240),
            ('4 – 7 min', 240, 420),
            ('> 7 min', 420, None),
        ]
        self.all_tags = set()
        self.tag_rows = {}  # tag_name → row number
        self.playlists = {}  # name → [file_path, ...]
        self.smart_playlists = {}  # name → {'rules': [...], 'match': 'all'|'any'}
        self.tooltip_texts = dict(DEFAULT_TOOLTIPS)
        self.queue_btn_throb_enabled = True
        self.speed_indicator_visible = True
        self.saved_voter = ''
        self.visible_columns = None  # list or None

        # Volume fade tuning defaults
        self.fade_step = 1
        self.fade_min_interval = 20
        self.fade_max_interval = 200
        self.fade_vel_window = 400       # ms
        self.fade_vel_low = 3.0
        self.fade_vel_high = 30.0
        self.fade_tick_threshold = 120

        # Audio device routing
        self.main_audio_device = ''      # '' = system default
        self.preview_audio_device = ''   # '' = system default

        # Waveform scrub bar
        self.waveform_enabled = True     # False = plain slider fallback

        # Pull-fader tuning defaults
        self.pull_fade_step = 1
        self.pull_min_interval = 20      # fastest (full pull)
        self.pull_max_interval = 200     # slowest (tiny pull)
        self.pull_dead_zone = 5          # pull% below this is ignored

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

        # Smart playlists
        smart_el = root.find('smart_playlists')
        if smart_el is not None:
            self.smart_playlists = {}
            for sp_el in smart_el.findall('smart_playlist'):
                name = sp_el.get('name', '')
                match = sp_el.get('match', 'all')
                rules = []
                for r_el in sp_el.findall('rule'):
                    field = r_el.get('field', '')
                    op = r_el.get('op', '')
                    value = r_el.get('value', '')
                    # Numeric fields → store as int
                    if field in ('Rating', 'Play Count', 'Last Played (days)'):
                        try:
                            value = int(value)
                        except (ValueError, TypeError):
                            pass
                    rules.append({'field': field, 'op': op, 'value': value})
                if name and rules:
                    self.smart_playlists[name] = {
                        'rules': rules, 'match': match}

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
            val2 = iface_el.get('speed_indicator', 'true')
            self.speed_indicator_visible = val2.lower() != 'false'
            self.saved_voter = iface_el.get('voter', '')

        # Visible columns
        vis_el = root.find('visible_columns')
        if vis_el is not None:
            cols_text = vis_el.text
            if cols_text:
                self.visible_columns = [c.strip() for c in cols_text.split(',') if c.strip()]

        # Volume fade tuning
        fade_el = root.find('volume_fade')
        if fade_el is not None:
            for attr, field, conv in [
                ('step', 'fade_step', int),
                ('min_interval', 'fade_min_interval', int),
                ('max_interval', 'fade_max_interval', int),
                ('vel_window', 'fade_vel_window', int),
                ('vel_low', 'fade_vel_low', float),
                ('vel_high', 'fade_vel_high', float),
                ('tick_threshold', 'fade_tick_threshold', int),
            ]:
                val = fade_el.get(attr)
                if val is not None:
                    try:
                        setattr(self, field, conv(val))
                    except (ValueError, TypeError):
                        pass

        # Pull-fader tuning
        pull_el = root.find('pull_fader')
        if pull_el is not None:
            for attr, field, conv in [
                ('step', 'pull_fade_step', int),
                ('min_interval', 'pull_min_interval', int),
                ('max_interval', 'pull_max_interval', int),
                ('dead_zone', 'pull_dead_zone', int),
            ]:
                val = pull_el.get(attr)
                if val is not None:
                    try:
                        setattr(self, field, conv(val))
                    except (ValueError, TypeError):
                        pass

        # Audio device routing
        audio_el = root.find('audio')
        if audio_el is not None:
            md = audio_el.find('main_device')
            if md is not None and md.text:
                self.main_audio_device = md.text
            pd = audio_el.find('preview_device')
            if pd is not None and pd.text:
                self.preview_audio_device = pd.text
            wf = audio_el.find('waveform_enabled')
            if wf is not None and wf.text:
                self.waveform_enabled = wf.text.lower() != 'false'

        return True

    def save(self, voter_name=''):
        """Save all settings to XML config file."""
        root = ET.Element('music_player_config')

        # Library root
        lib_el = ET.SubElement(root, 'library_root')
        lib_el.text = self.library_root or ''

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

        # Smart playlists
        smart_el = ET.SubElement(root, 'smart_playlists')
        for name, sp in self.smart_playlists.items():
            sp_el = ET.SubElement(smart_el, 'smart_playlist',
                                  name=name, match=sp.get('match', 'all'))
            for rule in sp.get('rules', []):
                ET.SubElement(sp_el, 'rule',
                              field=str(rule['field']),
                              op=str(rule['op']),
                              value=str(rule['value']))

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
                      speed_indicator=str(self.speed_indicator_visible).lower(),
                      voter=voter_name or self.saved_voter)

        # Visible columns
        if self.visible_columns is not None:
            vis_el = ET.SubElement(root, 'visible_columns')
            vis_el.text = ','.join(self.visible_columns)

        # Volume fade tuning
        ET.SubElement(root, 'volume_fade',
                      step=str(self.fade_step),
                      min_interval=str(self.fade_min_interval),
                      max_interval=str(self.fade_max_interval),
                      vel_window=str(self.fade_vel_window),
                      vel_low=str(self.fade_vel_low),
                      vel_high=str(self.fade_vel_high),
                      tick_threshold=str(self.fade_tick_threshold))

        # Pull-fader tuning
        ET.SubElement(root, 'pull_fader',
                      step=str(self.pull_fade_step),
                      min_interval=str(self.pull_min_interval),
                      max_interval=str(self.pull_max_interval),
                      dead_zone=str(self.pull_dead_zone))

        # Audio device routing
        audio_el = ET.SubElement(root, 'audio')
        md = ET.SubElement(audio_el, 'main_device')
        md.text = self.main_audio_device or ''
        pd = ET.SubElement(audio_el, 'preview_device')
        pd.text = self.preview_audio_device or ''
        wf = ET.SubElement(audio_el, 'waveform_enabled')
        wf.text = str(self.waveform_enabled).lower()

        ET.indent(root)
        tree = ET.ElementTree(root)
        tree.write(self.config_path, encoding='unicode', xml_declaration=True)
