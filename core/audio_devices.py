"""
Audio device enumeration via python-vlc.

Provides a list of (device_id, display_name) tuples for each audio output
module.  Used by the Audio menu and preview dialog.
"""

import vlc


def list_audio_devices(instance=None):
    """Return a list of ``(device_id, display_name)`` for all audio outputs.

    *instance* is an optional ``vlc.Instance``; one is created temporarily if
    not supplied.

    The first entry is always ``('', 'System Default')``.
    """
    own_instance = instance is None
    if own_instance:
        instance = vlc.Instance()

    devices = [('', 'System Default')]

    try:
        mp = instance.media_player_new()
        mods = mp.audio_output_device_enum()
        if mods:
            mod = mods
            while mod:
                dev_id = mod.contents.device
                desc = mod.contents.description
                if isinstance(dev_id, bytes):
                    dev_id = dev_id.decode('utf-8', errors='replace')
                if isinstance(desc, bytes):
                    desc = desc.decode('utf-8', errors='replace')
                devices.append((dev_id, desc or dev_id))
                mod = mod.contents.next
        vlc.libvlc_audio_output_device_list_release(mods)
        mp.release()
    except Exception:
        pass  # graceful fallback — just "System Default"

    return devices
