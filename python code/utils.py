# utils.py - General helper functions and safe GUI update mechanisms

import tkinter as tk
from tkinter import messagebox
import math
import sys
import os
import re # Added for basic filename sanitization

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
        except ValueError: i = 0 # Handles log(0) and potentially negative
        p = math.pow(1024, i)
        s = round(byte_count / p, 2)
        return f"{s} {size_name[i]}"
     else: return "0 B"

def format_bytes_per_second(speed_bps):
    """ فرمت کردن سرعت (بایت بر ثانیه) به KB/s, MB/s, GB/s """
    if speed_bps is None or speed_bps < 0: return "N/A/s" # Consistent with bytes
    if speed_bps == 0: return "0 B/s"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    if speed_bps > 0:
       try: i = int(math.floor(math.log(speed_bps, 1024)))
       except ValueError: i = 0 # Handles log(0) and potentially negative
       p = math.pow(1024, i)
       s = round(speed_bps / p, 2)
       return f"{s} {size_name[i]}/s"
    else: return "0 B/s"

# Modified: Removed Auto logic as per v1.2 config
def get_buffer_size(selected_option):
    """ برگرداندن اندازه بافر بر اساس انتخاب کاربر """
    # Use config directly as it's imported
    # Now expects selected_option to be a key from BUFFER_OPTIONS
    if selected_option in config.BUFFER_OPTIONS:
        return config.BUFFER_OPTIONS[selected_option]
    # Fallback to a default size if the option is not found or BUFFER_OPTIONS is empty
    # A safer fallback is to use a small, known good size like 4096.
    fallback_size = 4096
    print(f"WARNING: Invalid buffer option selected: '{selected_option}'. Using fallback default {fallback_size}.", file=sys.stderr)
    return fallback_size

def sanitize_filename_part(part):
    """
    تکه ای از مسیر (نام فایل یا پوشه) را تمیزکاری می‌کند.
    از '.' و '..'، نام‌های رزرو شده و کاراکترهای نامعتبر جلوگیری می‌کند.
    """
    if not part:
        return "" # Empty parts are handled during join

    # جلوگیری از نام‌های خاص '.' و '..' در هر بخشی از مسیر
    if part in ['.', '..']:
         raise ValueError(f"Invalid path segment: '{part}'")

    # تمیزکاری کاراکترها: اجازه به حروف الفبا-عددی، نقطه، خط فاصله، زیرخط و فاصله
    # Regex جایگزین کاراکترهای غیرمجاز با زیرخط
    sanitized_part = re.sub(r'[^\w\s\.-_]', '_', part)

    # تبدیل فضاهای خالی به زیرخط
    sanitized_part = sanitized_part.replace(' ', '_')

    # حذف نقطه‌های ابتدا و انتها و فضاهای خالی انتهای نام
    sanitized_part = re.sub(r'^\.+', '', sanitized_part) # حذف نقطه‌های ابتدا
    sanitized_part = re.sub(r'[\s.]+$', '', sanitized_part) # حذف فضاهای خالی یا نقطه‌های انتها

    # اگر بعد از تمیزکاری خالی شد (و در اصل خالی نبود), آن را نامعتبر بدانید
    # این چک برای جلوگیری از نام‌هایی که بعد از تمیزکاری خالی می‌شوند مثل "..."
    if not sanitized_part and part:
         raise ValueError(f"Path segment became empty after sanitization from '{part}'")


    # جلوگیری از نام‌های رزرو شده در ویندوز (اختیاری اما توصیه می‌شود)
    if sys.platform == "win32":
        reserved_names = ['CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9']
        # چک کردن با پسوند
        if sanitized_part.split('.')[0].upper() in reserved_names:
            raise ValueError(f"Reserved name not allowed: '{part}' -> '{sanitized_part}'")

    return sanitized_part


def sanitize_path(base_dir, relative_path):
    """
    مسیر نسبی دریافتی را تمیزکاری و اعتبارسنجی می‌کند تا از Path Traversal جلوگیری شود.
    مسیر نهایی باید حتماً زیرشاخه base_dir باشد.
    مسیر نسبی (relative_path) باید با استفاده از جداکننده '/' پروتکل باشد.

    Args:
        base_dir (str): دایرکتوری پایه‌ای که فایل‌ها/پوشه‌ها باید در آن ذخیره شوند.
        relative_path (str): مسیر آیتم (فایل یا پوشه) نسبت به پوشه اصلی ارسالی توسط فرستنده، با جداکننده '/'.

    Returns:
        str: مسیر کامل و تمیزکاری شده برای ذخیره‌سازی.

    Raises:
        ValueError: اگر مسیر تمیزکاری شده خارج از base_dir باشد یا شامل کاراکترهای نامعتبر پس از تمیزکاری باشد.
        OSError: اگر ایجاد base_dir با مشکل مواجه شود.
        RuntimeError: برای خطاهای غیرمنتظره در ایجاد base_dir.
    """
    if not base_dir:
        raise ValueError("Base directory for sanitization cannot be empty.")

    # مرحله 1: اطمینان از وجود base_dir و تبدیل آن به مسیر مطلق و نرمالیزه
    try:
        base_dir_abs = os.path.abspath(base_dir)
        # اطمینان از وجود base_dir قبل از استفاده
        if not os.path.exists(base_dir_abs):
             print(f"DEBUG: Base directory '{base_dir_abs}' does not exist, attempting to create.")
             try: os.makedirs(base_dir_abs, exist_ok=True)
             except OSError as e:
                  raise OSError(f"Failed to create base directory '{base_dir_abs}': {e}") from e # Re-raise OS error with context
             except Exception as e:
                  raise RuntimeError(f"Unexpected error creating base directory '{base_dir_abs}': {e}") from e

    except Exception as e:
         # Catch errors related to abspath or exists on base_dir itself
         raise ValueError(f"Invalid base directory path '{base_dir}': {e}") from e


    # مرحله 2: تمیزکاری تکه‌های مسیر نسبی
    # استفاده از جداکننده '/' پروتکل، نه os.sep
    # Split by protocol separator '/'
    parts_protocol = relative_path.split('/')
    sanitized_parts_local = []

    for i, part in enumerate(parts_protocol):
        # Allow empty part only for the trailing slash indicating a directory
        if i == len(parts_protocol) - 1 and part == "" and len(parts_protocol) > 1:
            sanitized_parts_local.append("")
        elif part == "." or part == "..":
            # Explicitly disallow "." and ".." parts after splitting protocol path
            raise ValueError(f"Invalid path segment '.' or '..' not allowed after splitting protocol path: '{relative_path}'")
        elif part == "":
            # Ignore empty parts from double slashes "//" but log a warning
            print(f"WARNING: Ignoring empty path segment from double slashes in '{relative_path}'")
            continue # Skip this empty part
        else:
            # Sanitize the actual name part
            try:
                sanitized_name = sanitize_filename_part(part)
                # sanitize_filename_part raises ValueError for invalid names like "..."
                sanitized_parts_local.append(sanitized_name)
            except ValueError as e:
                 # Re-raise with more context
                 raise ValueError(f"Error sanitizing path segment '{part}' in '{relative_path}': {e}") from e

    # Join the sanitized local parts using os.sep
    # os.path.join handles empty strings in the parts list for creating paths,
    # e.g., os.path.join('base', 'dir', '', 'file') -> 'base/dir/file'
    # os.path.join('base', 'dir', '') -> 'base/dir/'
    # os.path.join('base', '') -> 'base\' (on Windows) or 'base/' (on Linux/macOS)
    # This correctly preserves the directory-ness indicated by a trailing empty part.
    if not sanitized_parts_local and relative_path.strip() not in ["", ".", "/"]:
         # This case should be covered by the checks inside the loop, but defensive check.
         # If relative_path was e.g. "...", sanitize_filename_part would raise ValueError.
         # If relative_path was e.g. "//", the loop would skip empty parts, resulting in [].
         # If relative_path was e.g. ".", sanitize_filename_part would raise ValueError.
         # If relative_path was e.g. "/", split gives ["", ""]. First "" is ignored, second is trailing. sanitized_parts_local becomes [""]
         # os.path.join('base', '') -> 'base\'
         # This case should only result in an error if relative_path was invalid but didn't raise inside the loop.
         raise ValueError(f"Sanitized parts list is unexpectedly empty for relative path: '{relative_path}'")


    # Construct the full path by joining base_dir_abs with the sanitized relative path parts
    # Using *sanitized_parts_local unpacks the list as arguments to os.path.join
    full_path = os.path.join(base_dir_abs, *sanitized_parts_local)


    # Step 3: نرمالیزه کردن مسیر نهایی (حل کردن . و ..) - این مرحله ضروری است.
    normalized_full_path = os.path.normpath(full_path)

    # Step 4: اعتبارسنجی نهایی - مطمئن شوید مسیر نرمالیزه شده همچنان در base_dir_abs قرار دارد
    # نیاز است که base_dir_abs هم نرمالایز شود تا چک مقایسه درست انجام شود.
    base_dir_abs_norm = os.path.normpath(base_dir_abs)

    # Use os.path.commonpath to check if normalized_full_path is within base_dir_abs_norm
    # Compare using normcase for case-insensitive file systems (Windows).
    common_path = os.path.commonpath([base_dir_abs_norm, normalized_full_path])

    if os.path.normcase(common_path) != os.path.normcase(base_dir_abs_norm):
         print(f"DEBUG: Path Traversal Attempt Detected! Normalized path '{normalized_full_path}' is outside base dir '{base_dir_abs_norm}'. Common path: '{common_path}'", file=sys.stderr)
         raise ValueError(f"Sanitized path '{normalized_full_path}' is outside base directory '{base_dir}'. Path Traversal attempt?")

    # Additional check: Ensure the resulting path is NOT the base dir itself IF the original relative_path
    # was intended to be a sub-item.
    # If the original relative_path was empty or just slashes, normalized_full_path might legitimately be the base_dir_abs_norm.
    # But if relative_path pointed to a sub-item (e.g., "subdir/file.txt"), the result should be a subdirectory.
    # Let's check if the input `relative_path` was *not* intended to be the base directory itself.
    # Assume "","." and "/" are the only relative_path values that *could* mean the base directory itself.
    if relative_path.strip() not in ["", ".", "/"]:
         if os.path.normcase(normalized_full_path) == os.path.normcase(base_dir_abs_norm):
              # This means the sanitized relative path, when joined with the base_dir_abs, resolved back to the base_dir.
              # This implies the relative path was something like "../" or a complex form that normalized incorrectly,
              # or the sanitization process collapsed valid segments too aggressively (unlikely with current logic).
              # The commonpath check *should* catch actual traversal attempts like "../".
              # This check is more for cases where the relative path resolves *to* the base unexpectedly.
              print(f"DEBUG: Sanitized path '{normalized_full_path}' resolved unexpectedly to the base directory '{base_dir_abs_norm}' for non-base relative path '{relative_path}'. Sanitized parts: {sanitized_parts_local}", file=sys.stderr)
              # This case might indicate an issue with the input path format or an edge case in sanitization/normalization.
              # It's safer to disallow resolving to the base when a sub-path was intended.
              raise ValueError(f"Sanitized path resolves to base directory unexpectedly for relative path '{relative_path}'.")


    print(f"DEBUG: Sanitized path for '{relative_path}' resulted in safe path: '{normalized_full_path}' relative to base '{base_dir_abs}'")
    return normalized_full_path


# --- Safe GUI Update Functions (Called by worker threads, executed in GUI thread via root.after) ---
# (These functions are the same as before)
def _update_status_direct(widget, message):
     """ Directly updates the status text area (must run in GUI thread) """
     # Check if widget exists before trying to interact with it
     if widget and hasattr(widget, 'winfo_exists') and widget.winfo_exists():
        widget.configure(state=tk.NORMAL) # Enable writing
        widget.insert(tk.END, message + "\n")
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED) # Disable editing


def _update_progress_direct(widget, value):
     """ Directly updates the progress bar value (must run in GUI thread) """
     if widget and hasattr(widget, 'config') and widget.winfo_exists():
         widget.config(value=value)

def _update_speed_direct(widget_var, speed_string):
     """ Directly updates the speed label textvariable (must run in GUI thread) """
     # Check if widget_var object is still valid (StringVar doesn't have winfo_exists)
     if widget_var:
         widget_var.set(speed_string)

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
    # Check if widget_var object is still valid
    if widget_var:
        widget_var.set(value)

def _show_messagebox_direct(type, title, message):
    """ Directly shows a messagebox (must run in GUI thread) """
    # Message boxes don't need a widget check as they are modal windows
    # but ensuring root window exists might be safer in some contexts, though usually not necessary.
    if type == 'info': messagebox.showinfo(title, message)
    elif type == 'warning': messagebox.showwarning(title, message)
    elif type == 'error': messagebox.showerror(title, message)
    else: print(f"Unknown messagebox type: {type} - {title}: {message}", file=sys.stderr)


def safe_gui_update(root, command, *args):
    """ Schedules a GUI update command to be run safely in the main Tkinter thread. """
    # Check if the root window still exists before scheduling the command.
    # This prevents errors if threads try to update after the GUI is closed.
    if root and hasattr(root, 'after') and root.winfo_exists():
        root.after(0, lambda: command(*args))
    # else: print(f"DEBUG: GUI root window closed, skipping GUI update: {command.__name__}", file=sys.stderr)