# utils.py - General helper functions and safe GUI update mechanisms

import tkinter as tk
from tkinter import messagebox
import math
import sys
import os

# Import configuration (using absolute import relative to the package root)
import config # Assuming config.py is in the package root

# --- Helper Functions (General purpose) ---
def format_bytes(byte_count):
     """ فرمت کردن تعداد بایت ها به KB, MB, GB """
     if byte_count is None or byte_count < 0: return "N/A"
     if byte_count == 0: return "0 B"
     size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
     if byte_count > 0:
        try: i = int(math.floor(math.log(byte_count, 1024)))
        except ValueError: i = 0
        p = math.pow(1024, i)
        s = round(byte_count / p, 2)
        return f"{s} {size_name[i]}"
     else: return "0 B"

def format_bytes_per_second(speed_bps):
    """ فرمت کردن سرعت (بایت بر ثانیه) به KB/s, MB/s, GB/s """
    if speed_bps is None or speed_bps < 0: return "N/A"
    if speed_bps == 0: return "0 B/s"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    if speed_bps > 0:
       try: i = int(math.floor(math.log(speed_bps, 1024)))
       except ValueError: i = 0
       p = math.pow(1024, i)
       s = round(speed_bps / p, 2)
       return f"{s} {size_name[i]}/s"
    else: return "0 B/s"

# Modified: Removed Auto logic
def get_buffer_size(selected_option):
    """ برگرداندن اندازه بافر بر اساس انتخاب کاربر (حالت Auto حذف شده) """
    # Use config directly as it's imported
    # Now expects selected_option to be a key from BUFFER_OPTIONS
    if selected_option in config.BUFFER_OPTIONS:
        return config.BUFFER_OPTIONS[selected_option]
    # Fallback to a default size if the option is not found
    print(f"WARNING: Invalid buffer option selected: {selected_option}. Using default 4096.", file=sys.stderr)
    return 4096


# --- Safe GUI Update Functions (Called by worker threads, executed in GUI thread via root.after) ---

def _update_status_direct(widget, message):
     """ Directly updates the status text area (must run in GUI thread) """
     if widget and hasattr(widget, 'winfo_exists') and widget.winfo_exists():
        widget.insert(tk.END, message + "\n")
        widget.see(tk.END)

def _update_progress_direct(widget, value):
     """ Directly updates the progress bar value (must run in GUI thread) """
     if widget and hasattr(widget, 'config') and widget.winfo_exists():
         widget.config(value=value)

def _update_speed_direct(widget, speed_string):
     """ Directly updates the speed label textvariable (must run in GUI thread) """
     if widget:
         widget.set(speed_string)

def _update_button_state_direct(widget, state):
    """ Directly updates a button's state (must run in GUI thread) """
    if widget and hasattr(widget, 'config') and widget.winfo_exists():
         widget.config(state=state)

def _update_combobox_state_direct(widget, state):
    """ Directly updates a combobox's state (must run in GUI thread) """
    if widget and hasattr(widget, 'config') and widget.winfo_exists():
        widget.config(state=state)

def _update_entry_var_direct(widget_var, value):
    """ Directly updates an Entry widget's textvariable (must run in GUI thread) """
    if widget_var:
        widget_var.set(value)

def _show_messagebox_direct(type, title, message):
    """ Directly shows a messagebox (must run in GUI thread) """
    if type == 'info': messagebox.showinfo(title, message)
    elif type == 'warning': messagebox.showwarning(title, message)
    elif type == 'error': messagebox.showerror(title, message)
    else: print(f"Unknown messagebox type: {type} - {title}: {message}", file=sys.stderr)


def safe_gui_update(root, command, *args):
    """ Schedules a GUI update command to be run safely in the main Tkinter thread. """
    if root and hasattr(root, 'after') and root.winfo_exists():
        root.after(0, lambda: command(*args))