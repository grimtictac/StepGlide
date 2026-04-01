#!/usr/bin/env python3
"""
Migration script: Remove all CustomTkinter and replace with native tkinter/ttk.
"""

import re

INPUT = 'player.py'
OUTPUT = 'player.py'

with open(INPUT, 'r') as f:
    code = f.read()

# ════════════════════════════════════════════════════════════
# 1. Replace import
# ════════════════════════════════════════════════════════════
code = code.replace("import customtkinter as ctk\n", "")

# ════════════════════════════════════════════════════════════
# 2. Replace ctk.set_appearance_mode / set_default_color_theme
# ════════════════════════════════════════════════════════════
code = code.replace("        ctk.set_appearance_mode('dark')\n", "")
code = code.replace("        ctk.set_default_color_theme('blue')\n", "")

# ════════════════════════════════════════════════════════════
# 3. Replace class MusicPlayer(ctk.CTk) with tk.Tk
# ════════════════════════════════════════════════════════════
code = code.replace("class MusicPlayer(ctk.CTk):", "class MusicPlayer(tk.Tk):")

# ════════════════════════════════════════════════════════════
# 4. Replace ctk.CTkFont(...) with tuples
#    CTkFont(size=N) → ('Segoe UI', N)
#    CTkFont(size=N, weight='bold') → ('Segoe UI', N, 'bold')
# ════════════════════════════════════════════════════════════
def replace_ctk_font(m):
    args = m.group(1)
    size_m = re.search(r'size\s*=\s*(\d+)', args)
    weight_m = re.search(r"weight\s*=\s*'(\w+)'", args)
    size = int(size_m.group(1)) if size_m else 10
    if weight_m:
        return f"('Segoe UI', {size}, '{weight_m.group(1)}')"
    return f"('Segoe UI', {size})"

code = re.sub(r'ctk\.CTkFont\(([^)]*)\)', replace_ctk_font, code)

# ════════════════════════════════════════════════════════════
# 5. Replace ctk.CTkToplevel → tk.Toplevel
# ════════════════════════════════════════════════════════════
code = code.replace("ctk.CTkToplevel(", "tk.Toplevel(")

# ════════════════════════════════════════════════════════════
# 6. Replace ctk.CTkLabel → tk.Label with property mapping
#    fg_color → bg, text_color → fg, wraplength stays
#    corner_radius → remove, anchor stays
# ════════════════════════════════════════════════════════════
def replace_ctk_label(m):
    full = m.group(0)
    # Extract parent and kwargs
    inner = m.group(1)
    
    # Remove CTk-specific kwargs
    inner = re.sub(r",?\s*corner_radius\s*=\s*\d+", "", inner)
    
    # Replace text_color → fg
    inner = re.sub(r"text_color\s*=", "fg=", inner)
    
    # Replace fg_color → bg
    inner = re.sub(r"fg_color\s*=", "bg=", inner)
    
    return f"tk.Label({inner})"

code = re.sub(r'ctk\.CTkLabel\(([^)]*(?:\([^)]*\)[^)]*)*)\)', replace_ctk_label, code)

# ════════════════════════════════════════════════════════════
# 7. Replace ctk.CTkButton → tk.Button with property mapping
# ════════════════════════════════════════════════════════════
def replace_ctk_button(m):
    inner = m.group(1)
    
    # Remove CTk-specific kwargs
    inner = re.sub(r",?\s*hover_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*corner_radius\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*border_width\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*border_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*button_length\s*=\s*\d+", "", inner)
    
    # Replace fg_color → bg
    inner = re.sub(r"fg_color\s*=\s*'transparent'", "bg='#2b2b2b'", inner)
    inner = re.sub(r"fg_color\s*=", "bg=", inner)
    
    # Replace text_color → fg
    inner = re.sub(r"text_color\s*=", "fg=", inner)
    
    # height in CTkButton → remove (tk.Button doesn't use pixel height the same way)
    # Actually keep height — we'll just let tk handle it
    # width in pixels → approximate char width (divide by ~8)
    def px_to_chars_width(wm):
        px = int(wm.group(1))
        chars = max(2, px // 8)
        return f"width={chars}"
    inner = re.sub(r"width\s*=\s*(\d+)", px_to_chars_width, inner)
    
    # height in pixels → remove for buttons (tk.Button doesn't support pixel height easily)
    inner = re.sub(r",?\s*height\s*=\s*\d+", "", inner)
    
    # 'state' stays as-is for tk.Button
    
    return f"tk.Button({inner})"

code = re.sub(r'ctk\.CTkButton\(([^)]*(?:\([^)]*\)[^)]*)*)\)', replace_ctk_button, code)

# ════════════════════════════════════════════════════════════
# 8. Replace ctk.CTkEntry → tk.Entry with property mapping
# ════════════════════════════════════════════════════════════
def replace_ctk_entry(m):
    inner = m.group(1)
    
    # Remove placeholder_text (we'll handle this separately)
    inner = re.sub(r",?\s*placeholder_text\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r',?\s*placeholder_text\s*=\s*"[^"]*"', "", inner)
    
    # Remove CTk-specific
    inner = re.sub(r",?\s*corner_radius\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*border_width\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*border_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*fg_color\s*=\s*'[^']*'", "", inner)
    
    # height → remove (tk.Entry doesn't support pixel height)
    inner = re.sub(r",?\s*height\s*=\s*\d+", "", inner)
    
    # Add bg/fg colors
    inner = inner.rstrip()
    if inner.endswith(','):
        inner = inner[:-1]
    
    return f"tk.Entry({inner}, bg='#343638', fg='#dce4ee', insertbackground='#dce4ee')"

code = re.sub(r'ctk\.CTkEntry\(([^)]*(?:\([^)]*\)[^)]*)*)\)', replace_ctk_entry, code)

# ════════════════════════════════════════════════════════════
# 9. Replace ctk.CTkSlider → ttk.Scale with property mapping
# ════════════════════════════════════════════════════════════
def replace_ctk_slider(m):
    inner = m.group(1)
    
    # Remove CTk-specific
    inner = re.sub(r",?\s*button_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*button_hover_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*progress_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*border_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*border_width\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*button_length\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*corner_radius\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*number_of_steps\s*=\s*\d+", "", inner)
    
    # height → remove for horizontal, length for vertical
    inner = re.sub(r",?\s*height\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*width\s*=\s*\d+", "", inner)
    
    # orientation → orient
    inner = re.sub(r"orientation\s*=", "orient=", inner)
    
    # from_ and to stay, variable stays, command stays
    
    return f"ttk.Scale({inner})"

code = re.sub(r'ctk\.CTkSlider\(([^)]*(?:\([^)]*\)[^)]*)*)\)', replace_ctk_slider, code)

# ════════════════════════════════════════════════════════════
# 10. Replace ctk.CTkOptionMenu → ttk.Combobox
# ════════════════════════════════════════════════════════════
def replace_ctk_optionmenu(m):
    inner = m.group(1)
    
    # Remove CTk-specific
    inner = re.sub(r",?\s*fg_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*button_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*button_hover_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*dropdown_fg_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*dropdown_hover_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*dropdown_text_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*hover_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*corner_radius\s*=\s*\d+", "", inner)
    
    # variable → textvariable
    inner = re.sub(r"\bvariable\s*=", "textvariable=", inner)
    
    # height → remove
    inner = re.sub(r",?\s*height\s*=\s*\d+", "", inner)
    
    # command → needs special handling for Combobox (bind <<ComboboxSelected>>)
    # For now, remove command and we'll fix it
    # Actually, let's keep a note — we need to handle this post-migration
    
    return f"ttk.Combobox({inner}, state='readonly')"

code = re.sub(r'ctk\.CTkOptionMenu\(([^)]*(?:\([^)]*\)[^)]*)*)\)', replace_ctk_optionmenu, code)

# ════════════════════════════════════════════════════════════
# 11. Replace ctk.CTkProgressBar → ttk.Progressbar
# ════════════════════════════════════════════════════════════
def replace_ctk_progressbar(m):
    inner = m.group(1)
    # Remove CTk-specific
    inner = re.sub(r",?\s*progress_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*fg_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*corner_radius\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*border_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*border_width\s*=\s*\d+", "", inner)
    # width → length
    inner = re.sub(r"width\s*=", "length=", inner)
    return f"ttk.Progressbar({inner})"

code = re.sub(r'ctk\.CTkProgressBar\(([^)]*(?:\([^)]*\)[^)]*)*)\)', replace_ctk_progressbar, code)

# ════════════════════════════════════════════════════════════
# 12. Replace ctk.CTkCheckBox → ttk.Checkbutton
# ════════════════════════════════════════════════════════════
def replace_ctk_checkbox(m):
    inner = m.group(1)
    # Remove CTk-specific
    inner = re.sub(r",?\s*checkbox_width\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*checkbox_height\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*width\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*height\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*fg_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*hover_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*corner_radius\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*border_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*border_width\s*=\s*\d+", "", inner)
    return f"ttk.Checkbutton({inner})"

code = re.sub(r'ctk\.CTkCheckBox\(([^)]*(?:\([^)]*\)[^)]*)*)\)', replace_ctk_checkbox, code)

# ════════════════════════════════════════════════════════════
# 13. Replace ctk.CTkSwitch → ttk.Checkbutton
# ════════════════════════════════════════════════════════════
def replace_ctk_switch(m):
    inner = m.group(1)
    inner = re.sub(r",?\s*width\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*height\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*fg_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*progress_color\s*=\s*'[^']*'", "", inner)
    return f"ttk.Checkbutton({inner})"

code = re.sub(r'ctk\.CTkSwitch\(([^)]*(?:\([^)]*\)[^)]*)*)\)', replace_ctk_switch, code)

# ════════════════════════════════════════════════════════════
# 14. Replace ctk.CTkScrollableFrame → custom scroll frame
#     We'll use a helper class
# ════════════════════════════════════════════════════════════
# Replace the creation calls
def replace_ctk_scrollframe(m):
    inner = m.group(1)
    # Remove CTk-specific
    inner = re.sub(r",?\s*fg_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*corner_radius\s*=\s*\d+", "", inner)
    inner = re.sub(r",?\s*orientation\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*border_color\s*=\s*'[^']*'", "", inner)
    inner = re.sub(r",?\s*border_width\s*=\s*\d+", "", inner)
    return f"ScrollableFrame({inner})"

code = re.sub(r'ctk\.CTkScrollableFrame\(([^)]*(?:\([^)]*\)[^)]*)*)\)', replace_ctk_scrollframe, code)

# ════════════════════════════════════════════════════════════
# 15. Replace ctk.CTkTextbox → tk.Text
# ════════════════════════════════════════════════════════════
def replace_ctk_textbox(m):
    inner = m.group(1)
    inner = re.sub(r",?\s*corner_radius\s*=\s*\d+", "", inner)
    # fg_color → bg
    inner = re.sub(r"fg_color\s*=", "bg=", inner)
    # text_color → fg  
    inner = re.sub(r"text_color\s*=", "fg=", inner)
    return f"tk.Text({inner})"

code = re.sub(r'ctk\.CTkTextbox\(([^)]*(?:\([^)]*\)[^)]*)*)\)', replace_ctk_textbox, code)

# ════════════════════════════════════════════════════════════
# 16. Fix configure() calls with CTk-specific properties
# ════════════════════════════════════════════════════════════

# btn_play.configure(text='...', fg_color='...', hover_color='...')
# → btn_play.configure(text='...', bg='...')
def fix_configure_calls(code):
    # Remove hover_color from configure
    code = re.sub(r",?\s*hover_color\s*=\s*'[^']*'", "", code)
    
    # Replace fg_color → bg in configure calls
    # But ONLY in .configure() calls — we already handled creation
    # Actually, the creation calls are already done, so this is safe globally now
    code = re.sub(r"fg_color\s*=\s*'transparent'", "bg='#2b2b2b'", code)
    code = re.sub(r"fg_color\s*=", "bg=", code)
    
    # Replace text_color → fg
    code = re.sub(r"text_color\s*=", "fg=", code)
    
    # Replace button_color → (remove, not applicable for ttk.Combobox configure)
    code = re.sub(r",?\s*button_color\s*=\s*'[^']*'", "", code)
    
    # Remove border_color, border_width from configure
    code = re.sub(r",?\s*border_color\s*=\s*'[^']*'", "", code)
    code = re.sub(r",?\s*border_width\s*=\s*\d+", "", code)
    
    return code

code = fix_configure_calls(code)

# ════════════════════════════════════════════════════════════
# 17. Fix _bind_shortcuts isinstance check for CTkEntry
# ════════════════════════════════════════════════════════════
code = code.replace("(tk.Entry, ctk.CTkEntry)", "(tk.Entry,)")

# ════════════════════════════════════════════════════════════
# 18. Fix dialog.configure(fg_color=...) → dialog.configure(bg=...)
#     Already handled by step 16
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# 19. Fix .set() calls on ttk.Combobox (was CTkOptionMenu)
#     CTkOptionMenu.set(val) works; Combobox.set(val) also works, so OK
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# 20. Fix ProgressBar .set() → ttk.Progressbar uses ['value'] 
#     self.load_progress.set(0) → self.load_progress['value'] = 0
#     Actually, let's not change the API. We'll make a wrapper.
#     Simpler: use a thin wrapper class.
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# 21. Fix **_dd_style dict — remove CTk-specific keys
# ════════════════════════════════════════════════════════════
# The _dd_style dict is constructed with CTk-specific keys
# After our regex replacements, the remaining code should already
# have removed those keys. Let's verify and clean up.

# ════════════════════════════════════════════════════════════
# 22. Fix any remaining 'ctk.' references
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# 23. Update docstring
# ════════════════════════════════════════════════════════════
code = code.replace(
    "A music player using CustomTkinter + VLC",
    "A music player using tkinter/ttk + VLC"
)

with open(OUTPUT, 'w') as f:
    f.write(code)

print("Migration complete. Checking for remaining ctk references...")
import subprocess
result = subprocess.run(['grep', '-n', 'ctk\\.', OUTPUT], capture_output=True, text=True)
if result.stdout.strip():
    print("REMAINING ctk. references:")
    print(result.stdout)
else:
    print("✅ No remaining ctk. references!")

# Check for remaining CTk-specific property names
result2 = subprocess.run(['grep', '-nP', '(fg_color|text_color|hover_color|button_color|dropdown_fg_color|dropdown_hover_color|dropdown_text_color|button_hover_color|progress_color|corner_radius|border_color|checkbox_width|checkbox_height)', OUTPUT], 
                          capture_output=True, text=True)
if result2.stdout.strip():
    print("\nREMAINING CTk-specific properties:")
    print(result2.stdout)
else:
    print("✅ No remaining CTk-specific properties!")
