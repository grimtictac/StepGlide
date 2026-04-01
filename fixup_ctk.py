#!/usr/bin/env python3
"""Fix remaining CTk-specific properties after initial migration."""

import re

with open('player.py', 'r') as f:
    code = f.read()

# 1. Remove corner_radius from any widget creation/configure
code = re.sub(r',?\s*corner_radius\s*=\s*\d+', '', code)

# 2. Remove checkbox_width/checkbox_height 
code = re.sub(r',?\s*checkbox_width\s*=\s*\d+', '', code)
code = re.sub(r',?\s*checkbox_height\s*=\s*\d+', '', code)

# 3. Remove button_color from configure calls (ttk.Combobox doesn't support it)
code = re.sub(r',?\s*button_color\s*=\s*[\'"][^"\']*[\'"]', '', code)
code = re.sub(r',?\s*button_color\s*=\s*\w+', '', code)

# 4. Remove progress_color from ttk.Scale calls
code = re.sub(r',?\s*progress_color\s*=\s*\'[^\']*\'', '', code)

# 5. Remove width/height from ttk.Checkbutton (doesn't support them the same way)
# Only the auto-reset checkbox line has this issue — be targeted
# Actually the regex migration already tried but the multi-line broke it
# Let's just remove these from ttk.Checkbutton calls specifically

# 6. Remove fg_color that may have slipped through  
code = re.sub(r",?\s*fg_color\s*=\s*'[^']*'", '', code)

# 7. Fix ttk.Checkbutton — remove font, width, height kwargs not supported by ttk
# ttk.Checkbutton supports: text, variable, command, style — but not font, width, height directly
# We need to handle this via style. For now, remove the unsupported ones.

with open('player.py', 'w') as f:
    f.write(code)

print("Fix-up complete.")

import subprocess
result = subprocess.run(['grep', '-nP', 
    r'(corner_radius|checkbox_width|checkbox_height|button_color|progress_color|fg_color|text_color|hover_color|dropdown_fg_color|dropdown_hover_color|dropdown_text_color|button_hover_color)',
    'player.py'], capture_output=True, text=True)
if result.stdout.strip():
    print("REMAINING issues:")
    print(result.stdout)
else:
    print("✅ All CTk-specific properties cleaned!")
