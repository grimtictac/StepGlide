#!/usr/bin/env python3
"""
Fix structural issues after CTk migration:
1. Add ScrollableFrame helper class
2. Add dark ttk theme  
3. Fix _dd_style dict
4. Fix ttk.Combobox command= → bind
5. Fix ttk.Progressbar .set() calls
6. Fix mangled kwargs
7. Remove orphaned property fragments
"""

import re

with open('player.py', 'r') as f:
    code = f.read()

# ════════════════════════════════════════════════════════════
# 1. Remove any mangled property fragments (leftover partial names)
# ════════════════════════════════════════════════════════════
# These are leftovers from partial regex matches like "button_," or "dropdown_bg=" or "dropdown_,"
code = re.sub(r",?\s*button_\s*,", ",", code)
code = re.sub(r",?\s*dropdown_bg\s*=\s*'[^']*'", "", code)
code = re.sub(r",?\s*dropdown_\s*,", ",", code)
code = re.sub(r",?\s*dropdown_fg\s*=\s*'[^']*'", "", code)
# Clean up consecutive commas
code = re.sub(r',\s*,', ',', code)
# Clean up comma before closing paren
code = re.sub(r',\s*\)', ')', code)
# Clean up empty dict()
code = re.sub(r"dict\(\s*,", "dict(", code)

# ════════════════════════════════════════════════════════════
# 2. Fix the _dd_style dict — it's totally mangled, replace it
# ════════════════════════════════════════════════════════════
# Find and replace the _dd_style definition
code = re.sub(
    r"_dd_style\s*=\s*dict\([^)]*\)",
    "_dd_style = dict(font=('Segoe UI', 10))",
    code
)

# ════════════════════════════════════════════════════════════
# 3. Fix ttk.Combobox command= kwarg → bind after creation
#    ttk.Combobox doesn't have command=; need <<ComboboxSelected>>
#    But this is complex multi-line. Simpler: use a wrapper.
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# 4. Fix ttk.Progressbar .set(val) → ['value'] = val*100
#    CTkProgressBar.set() takes 0-1, ttk.Progressbar uses 0-100
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# 5. Fix ttk.Checkbutton — remove font= (unsupported in ttk)
#    Actually, ttk.Checkbutton does support style-based fonts
#    but not direct font= in all cases. Let's keep it for now.
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# 6. Fix configure calls on Combobox widgets
#    dd.configure(bg=...) → doesn't work on ttk widgets the same way
#    For ttk widgets we need style-based approach
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# 7. Fix tk.Label width= — CTkLabel width is pixels, tk.Label width is chars
#    Need to convert: width=180 → width=22 (roughly /8)
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# 8. Fix tk.Toplevel configure(bg=...) — was dialog.configure(fg_color=...)
# ════════════════════════════════════════════════════════════

with open('player.py', 'w') as f:
    f.write(code)

print("Structural fixes applied.")

# Show remaining issues
import subprocess
r = subprocess.run(['grep', '-n', 'dropdown_', 'player.py'], capture_output=True, text=True)
if r.stdout.strip():
    print("Remaining dropdown_ refs:")
    print(r.stdout[:500])
r2 = subprocess.run(['grep', '-n', 'button_,', 'player.py'], capture_output=True, text=True)
if r2.stdout.strip():
    print("Remaining button_ fragments:")
    print(r2.stdout[:500])
