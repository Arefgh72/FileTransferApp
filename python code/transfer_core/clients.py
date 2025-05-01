# transfer_core/clients.py - Client-side tasks for initiating file and folder transfers

import socket
import os
import time
import math # Used implicitly by utils.format_bytes, but might be useful if complex calcs added
import sys # Import sys for stderr
import threading # Used implicitly for Events passed as arguments

# Import configuration and utils and helpers using relative imports within the package structure
import config
import utils
from .helpers import CancelledError # Import the custom exception
from .discovery import discover_file_server_task # Import the discovery function
from .handshake import perform_folder_handshake_client # Import the client-side handshake function


# --- File Transfer Client Task (for single files) ---
# This function seems mostly correct based on previous interactions.
# Minor cleanup and consistency checks are added.
def send_file_task(filepath, buffer_size, gui_callbacks, cancel_transfer_event):
    """
    Thread task for the client to send a single file to a discovered server.
    Handles server discovery, connection, sending header, and sending file data.

    Args:
        filepath (str): The full path to the file to send.
        buffer_size (int): The buffer size to use for reading from file and sending data over the socket.
        gui_callbacks (dict): Dictionary of GUI callbacks provided by the GUI.
        cancel_transfer_event (threading.Event): Event provided by GUI to signal the client task to cancel.
    """
    print(f"DEBUG: send_file_task started for {filepath} with buffer size {buffer_size}")
    # Initial status and speed updates are usually done by the caller (GUI)

    client_socket = None
    file_handle = None
    is_cancelled = False # Flag to track if cancellation occurred
    server_info = None # (ip, port) tuple if found
    transfer_success = False # Flag to track if transfer completed successfully


    # --- Prepare File (Initial Validation) ---
    try:
         # Validate file existence, type, and readability (already done in GUI, but double-check for robustness)
         if not os.path.exists(filepath): raise FileNotFoundError(f"فایل '{filepath}' یافت نشد.")
         if not os.path.isfile(filepath): raise IsADirectoryError(f"مسیر '{filepath}' یک پوشه است، نه یک فایل.")
         # Check readability by attempting to open and immediately close
         # This also catches permission errors early
         try:
             with open(filepath, 'rb') as f: pass
         except IOError as e:
             raise IOError(f"قادر به خواندن فایل نیست: {e}")

         # Get file size
         filesize = os.path.getsize(filepath)
         filename = os.path.basename(filepath)

    except (FileNotFoundError, IsADirectoryError, IOError) as e:
         # If initial file prep fails, report error and exit early
         utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای دسترسی به فایل: {e}")
         utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای فایل", f"فایل '{os.path.basename(filepath)}' قابل دسترسی یا خواندن نیست:\n{e}")
         print(f"DEBUG: Error accessing selected file '{filepath}': {e}", file=sys.stderr)
         is_cancelled = True # Mark as cancelled due to error
         # Signal GUI that the transfer is finished (important if error happens before network ops)
         utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_transfer_finished'])
         return # Exit thread early


    try: # Outer try block for the entire send process (network operations)

        # Step 1: Discover the server
        # Call the external discovery task. It handles its own GUI updates/errors and cancel check.
        server_info = discover_file_server_task(gui_callbacks, cancel_transfer_event)

        # discover_file_server_task returns None if no server found or if cancelled.
        # If cancelled, cancel_transfer_event is set by the discovery task itself.
        if server_info is None:
             # If discovery failed (timed out without cancel) or was cancelled, exit.
             # Status message handled by discover_file_server_task itself if timeout occurred without cancel.
             if cancel_transfer_event.is_set():
                  utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] ارسال فایل توسط کاربر پس از کشف سرور لغو شد.")
                  print("DEBUG: File send cancelled after discovery")
                  is_cancelled = True # Ensure cancelled flag is set

             # Exit the try block, which leads to finally block.
             return # Exit if no server found or discovery was cancelled.


        server_ip, server_port = server_info

        # Step 2: Connect to the server
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], f"Speed: Connecting to {server_ip}:{server_port}...")
        print(f"DEBUG: Attempting to connect to TCP server at {server_ip}:{server_port}")
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(config.DISCOVERY_TIMEOUT) # Timeout for connection attempt

        print(f"DEBUG: Attempting socket.connect to {server_ip}:{server_port}")
        try:
            client_socket.connect((server_ip, server_port))
        except Exception as e:
             # Connection failed
             print(f"DEBUG: Socket connection failed: {e}", file=sys.stderr)
             # Determine specific error type for better message
             if isinstance(e, ConnectionRefusedError):
                 raise ConnectionRefusedError(f"اتصال توسط سرور رد شد. آیا گیرنده فعال است؟ {e}") from e
             elif isinstance(e, socket.timeout):
                  raise socket.timeout(f"زمان انتظار برای اتصال به سرور تمام شد. {e}") from e
             else:
                  raise Exception(f"خطا در اتصال به سرور: {e}") from e # Re-raise generic exception

        print("DEBUG: Socket connection established")
        client_socket.settimeout(None) # Remove timeout after connection
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[+] اتصال با سرور برای ارسال فایل برقرار شد.")

        if cancel_transfer_event.is_set():
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] ارسال فایل توسط کاربر پس از اتصال لغو شد.")
             is_cancelled = True
             print("DEBUG: File send cancelled after connection")
             # Exit the try block.
             return # Exit if cancelled


        # Step 3: Send file header (filename|filesize|buffersize|)
        # Using the old protocol format for single file transfer.
        # The server's accept loop will distinguish this from folder transfer by the lack of FOLDER_PROTOCOL_PREFIX.
        # Added trailing separator to match read_header_from_socket expectation on receiver side.
        # Use os.path.basename(filepath) for filename in header, receiver expects just the name.
        filename_in_header = os.path.basename(filepath)
        header_str = f"{filename_in_header}{config.HEADER_SEPARATOR}{filesize}{config.HEADER_SEPARATOR}{buffer_size}{config.HEADER_SEPARATOR}"
        header_bytes = header_str.encode('utf-8')

        # Basic check to prevent excessively large headers
        if len(header_bytes) > config.BUFFER_SIZE_FOR_HEADER:
             error_msg = f"[!] خطای داخلی: هدر فایل خیلی بزرگ است ({len(header_bytes)} بایت > {config.BUFFER_SIZE_FOR_HEADER} بایت). نام فایل خیلی طولانی است؟"
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], error_msg)
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای ارسال هدر", "اطلاعات فایل (نام، حجم) بیش از حد طولانی است.")
             is_cancelled = True
             print(f"DEBUG: Header too large: {len(header_bytes)} > {config.BUFFER_SIZE_FOR_HEADER}")
             # Exit the try block.
             return # Exit if header is too large

        print(f"DEBUG: Sending header: {header_str}")
        try:
            client_socket.sendall(header_bytes)
        except Exception as e:
            # Error sending header
            print(f"DEBUG: Error sending header: {e}", file=sys.stderr)
            raise Exception(f"Error sending header: {e}") from e # Re-raise to be caught by outer except

        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] هدر فایل ارسال شد: {filename_in_header} | {utils.format_bytes(filesize)} | {utils.format_bytes(buffer_size)}")
        print(f"DEBUG: Sent header ({len(header_bytes)} bytes)")


        # Step 4: Send the file data
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] در حال ارسال فایل: {filename_in_header} ({utils.format_bytes(filesize)}) به {server_ip}...")
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], 0)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: 0 B/s") # Initial speed

        # Open file here, use try/finally for closing
        file_handle = None # Initialize file_handle to None before opening
        try: # Inner try block for file reading and socket sending loop
            file_handle = open(filepath, "rb") # Open file in binary read mode
            print(f"DEBUG: File '{filepath}' opened for reading.")

            sent_bytes = 0
            start_time = time.time()
            last_update_time = start_time
            last_update_bytes = 0
            print("DEBUG: Starting file send loop")

            # Use the chosen buffer size for reading from file and sending
            send_buffer_size_for_loop = buffer_size # Use the size passed into this function

            while sent_bytes < filesize:
                if cancel_transfer_event.is_set():
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] ارسال فایل توسط کاربر لغو شد.")
                    is_cancelled = True
                    print("DEBUG: File send cancelled by user")
                    break # Exit loop on cancel

                try:
                    # Read a chunk from the file using the chosen buffer size
                    bytes_to_read_now = min(send_buffer_size_for_loop, filesize - sent_bytes)
                    if bytes_to_read_now <= 0:
                         # Should only happen if filesize was 0 initially or sent_bytes == filesize
                         break # Exit loop if nothing left to read

                    bytes_read_chunk = file_handle.read(bytes_to_read_now)
                except Exception as e: # Catch errors during file read
                     utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای خواندن فایل '{filename_in_header}': {e}")
                     print(f"DEBUG: Error reading file '{filename_in_header}': {e}", file=sys.stderr)
                     # If file reading fails, it's a critical error for this transfer.
                     # Mark as cancelled due to error and break loop.
                     is_cancelled = True
                     break # Exit loop on file read error


                if not bytes_read_chunk:
                    # Should only happen if file was smaller than expected or reached EOF unexpectedly
                     if sent_bytes < filesize:
                          utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] پایان غیرمنتظره فایل '{filename_in_header}' در حین خواندن.")
                          print("DEBUG: Unexpected end of file during read")
                          is_cancelled = True # Mark as cancelled due to incomplete file
                     break # Exit loop if read returns empty bytes (e.g. EOF)

                try:
                    # Send the chunk over the socket
                    # Set a timeout for sending this chunk. Use DATA_TRANSFER_TIMEOUT.
                    client_socket.settimeout(config.DATA_TRANSFER_TIMEOUT) # Changed timeout constant
                    client_socket.sendall(bytes_read_chunk)
                    client_socket.settimeout(None) # Remove timeout after successful send
                except socket.timeout:
                     # This indicates sendall was blocked for too long.
                     # It's a network/peer issue, treat as a connection error.
                     utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] زمان انتظار برای ارسال داده فایل '{filename_in_header}' تمام شد.")
                     print(f"DEBUG: Timeout during socket send for '{filename_in_header}'")
                     is_cancelled = True
                     break
                except Exception as e: # Catch other errors during socket send
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای ارسال داده به سوکت برای فایل '{filename_in_header}': {e}")
                    print(f"DEBUG: Error sending data for '{filename_in_header}': {e}", file=sys.stderr)
                    is_cancelled = True
                    break # Exit loop on socket error


                sent_bytes += len(bytes_read_chunk)

                # Update progress and speed display
                current_time = time.time()
                progress = (sent_bytes / filesize) * 100 if filesize > 0 else 0
                utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], progress)

                if current_time - last_update_time >= config.SPEED_UPDATE_INTERVAL:
                    time_delta = current_time - last_update_time
                    bytes_since_last_update = sent_bytes - last_update_bytes
                    speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                    speed_string = utils.format_bytes_per_second(speed_bps)
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], f"سرعت آپلود: {speed_string}")

                    last_update_time = current_time
                    last_update_bytes = sent_bytes
            print("DEBUG: File send loop finished")


            # Check if loop completed fully without cancellation and sent expected bytes
            if not is_cancelled and sent_bytes >= filesize:
                # If loop finished and all bytes sent, mark as successful for this file.
                # For single file transfer, overall success is file success.
                transfer_success = True
                utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[+] فایل '{filename_in_header}' با موفقیت ارسال شد.")
                print(f"DEBUG: File '{filename_in_header}' sent successfully.")
            # else: if is_cancelled is True or sent_bytes < filesize, it's not successful. Status/error message shown where break occurred.


        except Exception as e: # Catch exceptions occurring after file handle is open but not caught by inner loops
             # This catches errors like issues with file handle operations outside the main loop, etc.
             # If an error occurred while sending data for this file, catch it here.
             # Mark as cancelled due to error.
             if not is_cancelled: # Only report error if not already marked cancelled by user or socket error
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطایی در حین ارسال فایل به {server_ip} رخ داد: {e}")
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای ارسال", f"خطا در حین ارسال فایل به {server_ip}:\n{e}")
                 print(f"DEBUG: Exception during file send loop to {server_ip}: {e}", file=sys.stderr)
                 is_cancelled = True # Mark as cancelled due to error
                 transfer_success = False # Not successful

        finally: # This finally block runs if the inner try block (where file handle is used) exits
            print(f"DEBUG: Inner file send finally block entered for '{filepath}'.")
            # Ensure the file handle is closed
            if file_handle:
                try:
                    file_handle.close()
                    print(f"DEBUG: File handle '{filepath}' closed.")
                except Exception as e:
                    print(f"DEBUG: Error closing file handle in inner finally: {e}", file=sys.stderr)


        # Step 5: Final Status Report (after file handle is closed)
        # For single file transfer, this is the end of the process.
        # The final status message and GUI reset happen in the outer finally block.


    except (socket.timeout, ConnectionRefusedError, ValueError, CancelledError, Exception) as e: # Catch specific expected errors for the outer try block
        # This catches errors from discovery, connection, initial header send,
        # or exceptions re-raised from the inner file send try block.
        # Note: Added general Exception to catch anything re-raised from inner blocks for robustness.
        if not is_cancelled: # Avoid double reporting if already marked cancelled by specific error inside inner loops
            # Use server_info if available for error message
            server_addr_str = f"{server_info[0]}:{server_info[1]}" if server_info else "سرور نامشخص"
            msg = f"[!] خطایی در حین ارسال فایل رخ داد: {e}"
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], msg)
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای ارسال فایل", f"خطا در هنگام ارسال فایل به {server_addr_str}:\n{e}")
            print(f"DEBUG: Specific Error caught in outer except for send_file_task: {e}", file=sys.stderr)
            is_cancelled = True # Mark as cancelled due to error
            transfer_success = False # Not successful
        # Note: If CancelledError is caught here, is_cancelled is already true.


    finally: # This finally block runs after the entire function finishes (outer try/except)
        print("DEBUG: send_file_task finally block entered")
        # Ensure the client socket is closed.
        if client_socket:
            try:
                # Shutdown socket gracefully before closing if possible.
                try: client_socket.shutdown(socket.SHUT_RDWR)
                except OSError as e:
                     if e.errno not in (107, 10057): # 107=Transport endpoint is not connected (Linux), 10057=Socket is not connected (Windows)
                         print(f"DEBUG: Error during socket shutdown: {e}", file=sys.stderr)
                     pass
                except Exception as e:
                     print(f"DEBUG: Unexpected error during socket shutdown: {e}", file=sys.stderr)
                     pass

                client_socket.close()
                print("DEBUG: Client socket closed")
            except Exception as e:
                print(f"DEBUG: Error closing client socket in finally: {e}", file=sys.stderr)


        # Reset GUI elements related to transfer state (Progress bar, Speed display)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], 0) # Reset progress bar
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: N/A - Transfer Finished") # Reset speed display

        # Final status message in status area based on overall outcome
        if transfer_success:
            # Success message for file sent was shown inside the inner try block.
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[-] ارسال فایل به پایان رسید.")
        elif is_cancelled:
             # Message for cancellation/error was shown earlier.
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[-] ارسال فایل لغو شد.")
        else:
             # Error message was shown earlier.
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[-] ارسال فایل با خطا به پایان رسید.")


        # Signal GUI that transfer is finished (resets is_transfer_active)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_transfer_finished'])
        print("DEBUG: send_file_task finished")


# --- Folder Transfer Client Task (for folders) ---
def send_folder_task(folder_path, buffer_size, gui_callbacks, cancel_transfer_event):
    """
    Thread task for the client to send a folder to a discovered server.
    Handles server discovery, connection, sending folder structure and data.
    Includes a simple handshake mechanism at the end for verification (Count/Size).

    Args:
        folder_path (str): The full path to the folder to send.
        buffer_size (int): The buffer size to use for reading from files and sending data over the socket.
        gui_callbacks (dict): Dictionary of GUI callbacks provided by the GUI.
        cancel_transfer_event (threading.Event): Event provided by GUI to signal the client task to cancel.
    """
    print(f"DEBUG: send_folder_task started for {folder_path} with buffer size {buffer_size}")
    # Initial status and speed updates are usually done by the caller (GUI)

    client_socket = None
    is_cancelled = False # Flag to track if cancellation occurred
    server_info = None # (ip, port) tuple if found
    total_folder_size = None # Calculate for progress (optional but good)
    total_item_count = None # Calculate total number of files + folders
    sent_bytes_total = 0 # Total bytes sent for the entire folder transfer
    transfer_success = False # Flag to track if transfer completed successfully (including handshake)


    # --- Prepare Folder and Calculate Totals (for Verification) ---
    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] در حال آماده‌سازی پوشه '{os.path.basename(folder_path)}' و محاسبه حجم کل...")
    print(f"DEBUG: Preparing folder and calculating total size/count for: {folder_path}")
    try:
        # Validate folder existence, type, and readability for walking
        if not os.path.exists(folder_path):
             raise FileNotFoundError(f"پوشه '{folder_path}' یافت نشد.")
        if not os.path.isdir(folder_path):
             raise NotADirectoryError(f"مسیر '{folder_path}' یک پوشه است، نه یک پوشه.")
        # Check readability of the folder itself (permission to list contents)
        try: os.listdir(folder_path)
        except OSError as e:
             raise OSError(f"قادر به لیست کردن محتویات پوشه '{folder_path}' نیست: {e}")

        calculated_size = 0
        item_count = 0 # Initialize item count

        # Add check for cancel *before* starting the walk
        if cancel_transfer_event.is_set():
             raise CancelledError("Folder preparation and size calculation cancelled during initial check.")

        # Get the absolute path of the selected folder for calculating relative paths later
        folder_path_abs = os.path.abspath(folder_path)
        # Use folder_path_abs as the base for relative path calculation later
        # Ensure it's normalized to remove trailing slashes etc for consistent relpath calculation
        # Removed: base_path_for_rel = os.path.normpath(folder_path_abs) # This variable was undefined

        for dirpath, dirnames, filenames in os.walk(folder_path):
            # Check cancel during walk *before* processing items in this directory
            if cancel_transfer_event.is_set():
                raise CancelledError("Folder preparation and size calculation cancelled during directory walk.")

            # Skip common system directories (modify list in place for efficiency)
            dirnames[:] = [d for d in dirnames if d.lower() not in ["$recycle.bin", "system volume information"]]
            # Skip processing contents of system directories if dirpath itself matches
            if os.path.basename(dirpath).lower() in ["$recycle.bin", "system volume information"]:
                 print(f"DEBUG: Skipping counting items in system directory: {dirpath}")
                 continue # Skip to the next directory in os.walk

            # Count directories (except the root one which is handled by the first header)
            # A more accurate count would be to count directories encountered *after* the root.
            # But for simplicity in verification, let's count all directories returned by os.walk.
            # os.walk yields (dirpath, dirnames, filenames). For each dirpath, we send a FOLDER header.
            # For each filename in filenames, we send a FILE header.
            # Total items sent = (number of unique dirpaths visited by os.walk) + (total number of files).
            # os.walk visits each directory exactly once.
            item_count += 1 # Count the current directory (dirpath)
            item_count += len(filenames) # Add the files in this directory


            for f in filenames:
                fp = os.path.join(dirpath, f)
                # Use os.path.normpath and os.path.abspath for robust path handling
                fp_abs = os.path.abspath(fp)
                # Check if file still exists and is readable *during* the walk (might be deleted/moved)
                # Also check cancel during file processing within walk
                if cancel_transfer_event.is_set():
                     raise CancelledError("Folder preparation and size calculation cancelled during file check in walk.")

                if os.path.exists(fp_abs) and os.path.isfile(fp_abs):
                     try:
                          # Add size of the file to the total
                          calculated_size += os.path.getsize(fp_abs)
                     except Exception as e:
                          print(f"WARNING: Could not get size of file {fp_abs}: {e}. Skipping size calculation for this file.", file=sys.stderr)
                          # Continue walk even if one file fails size check
                          # Note: If we skip size, total_folder_size is inaccurate.
                          # It's better to either fail loudly or skip the item entirely.
                          # For now, just log warning and continue, accepting inaccurate total size for progress/verification.
                          # A more robust solution would be to exclude the file from the count as well,
                          # or fail the preparation phase. Let's fail the preparation if size cannot be obtained.
                          raise OSError(f"Could not get size of file {fp_abs}: {e}") from e # Fail if size cannot be obtained


            # Check cancel after processing filenames for the current dirpath
            if cancel_transfer_event.is_set():
                 raise CancelledError("Folder preparation and size calculation cancelled after processing files in a directory.")

        total_folder_size = calculated_size # Set the calculated total size
        total_item_count = item_count # Set the calculated total item count

        print(f"DEBUG: Total folder size calculated: {utils.format_bytes(total_folder_size)}")
        print(f"DEBUG: Total item count calculated: {total_item_count}")
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] حجم کل پوشه '{os.path.basename(folder_path)}': {utils.format_bytes(total_folder_size)}")
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] تعداد کل آیتم‌ها (فایل/پوشه) در پوشه: {total_item_count}")


    except (FileNotFoundError, NotADirectoryError, OSError, CancelledError) as e:
         # If initial folder prep or size/count calc fails/cancelled, report error and exit early
         utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطا در آماده‌سازی پوشه و محاسبه حجم: {e}")
         utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای آماده‌سازی پوشه", f"خطا در آماده‌سازی پوشه '{os.path.basename(folder_path)}' یا محاسبه حجم/تعداد:\n{e}")
         print(f"DEBUG: Error preparing folder {folder_path} or calculating totals: {e}", file=sys.stderr)
         is_cancelled = True
         # Signal GUI that operation finished, as it was cancelled or failed before network
         utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_transfer_finished'])
         return # Exit thread early
    except Exception as e:
         # Catch any other unexpected error during size calculation
         utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای غیرمنتظره در محاسبه حجم/تعداد پوشه: {e}")
         utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای حجم/تعداد پوشه", f"خطای غیرمنتظره در محاسبه حجم/تعداد پوشه '{os.path.basename(folder_path)}':\n{e}")
         print(f"DEBUG: Error calculating folder size/count: {e}", file=sys.stderr)
         is_cancelled = True
         utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_transfer_finished'])
         return # Exit thread early


    try: # Outer try block for the entire send process (discovery, connect, send data, handshake)

        # Step 1: Discover the server
        # Call the external discovery task. It handles its own GUI updates/errors and cancel check.
        server_info = discover_file_server_task(gui_callbacks, cancel_transfer_event)

        # discover_file_server_task returns None if no server found or if cancelled.
        # If cancelled, cancel_transfer_event is set by the discovery task itself.
        if server_info is None:
             # If discovery failed (timed out without cancel) or was cancelled, exit.
             # Status message handled by discover_file_server_task itself if timeout occurred without cancel.
             if cancel_transfer_event.is_set():
                  utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] ارسال پوشه توسط کاربر پس از کشف سرور لغو شد.")
                  print("DEBUG: Folder send cancelled after discovery")
                  is_cancelled = True # Ensure cancelled flag is set

             # Exit the try block, which leads to finally block.
             return # Exit if no server found or discovery was cancelled.


        server_ip, server_port = server_info

        # Step 2: Connect to the server
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], f"Speed: Connecting to {server_ip}:{server_port}...")
        print(f"DEBUG: Attempting to connect to TCP server at {server_ip}:{server_port}")
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(config.DISCOVERY_TIMEOUT) # Timeout for connection attempt

        print(f"DEBUG: Attempting socket.connect to {server_ip}:{server_port}")
        try:
            client_socket.connect((server_ip, server_port))
        except Exception as e:
             # Connection failed
             print(f"DEBUG: Socket connection failed: {e}", file=sys.stderr)
             # Determine specific error type for better message
             if isinstance(e, ConnectionRefusedError):
                 raise ConnectionRefusedError(f"اتصال توسط سرور رد شد. آیا گیرنده فعال است؟ {e}") from e
             elif isinstance(e, socket.timeout):
                  raise socket.timeout(f"زمان انتظار برای اتصال به سرور تمام شد. {e}") from e
             else:
                  raise Exception(f"خطا در اتصال به سرور: {e}") from e # Re-raise generic exception

        print("DEBUG: Socket connection established")
        client_socket.settimeout(None) # Remove timeout after connection
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[+] اتصال با سرور برای ارسال پوشه برقرار شد.")

        if cancel_transfer_event.is_set():
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] ارسال پوشه توسط کاربر پس از اتصال لغو شد.")
             is_cancelled = True
             print("DEBUG: Folder send cancelled after connection")
             # Exit the try block.
             return # Exit if cancelled


        # Step 3: Send the folder structure and data using the new protocol
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] در حال ارسال پوشه: '{os.path.basename(folder_path)}' به {server_ip}...")
        # Reset progress bar to 0 at the start of sending phase
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], 0)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: 0 B/s") # Initial speed


        # --- Try block for sending all headers and data ---
        # This block encompasses the os.walk loop and sending of individual items.
        # Exceptions raised within this block (or re-raised from inner blocks) will be caught here.
        try:

             # Send initial FOLDER header for the root directory
             # Get the name of the selected folder. Use abspath to handle "." or ".."
             root_folder_name = os.path.basename(os.path.abspath(folder_path))
             if not root_folder_name:
                  root_folder_name = f"Sent_Folder_{int(time.time())}"
                  utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] نام پوشه مبدا نامعتبر است (مانند ریشه درایو). با نام موقت '{root_folder_name}' ارسال می‌شود.")
                  print(f"WARNING: Could not get root folder name from '{folder_path}'. Using fallback '{root_folder_name}'.", file=sys.stderr)

             # Ensure forward slashes in protocol path and add trailing slash for folder
             protocol_root_path = root_folder_name.replace(os.sep, '/')
             if not protocol_root_path.endswith('/'):
                  protocol_root_path += '/'

             root_header_str = f"{config.FOLDER_PROTOCOL_PREFIX}{config.HEADER_SEPARATOR}{config.FOLDER_HEADER_TYPE_FOLDER}{config.HEADER_SEPARATOR}{protocol_root_path}{config.HEADER_SEPARATOR}"

             # Basic check for header size
             if len(root_header_str) > config.BUFFER_SIZE_FOR_HEADER:
                  raise ValueError(f"Root folder header too large ({len(root_header_str)} bytes). Folder name too long?")

             print(f"DEBUG: Sending root folder header: {root_header_str}")
             client_socket.sendall(root_header_str.encode('utf-8'))
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] هدر پوشه اصلی ارسال شد: '{protocol_root_path}'")


             # --- Send TOTAL_INFO header (new for Count/Size Verification) ---
             if total_item_count is not None and total_folder_size is not None: # Only send if calculation was successful
                  total_info_str = f"{config.FOLDER_PROTOCOL_PREFIX}{config.HEADER_SEPARATOR}{config.FOLDER_HEADER_TYPE_TOTAL_INFO}{config.HEADER_SEPARATOR}{total_item_count}{config.TOTAL_INFO_COUNT_SIZE_SEPARATOR}{total_folder_size}{config.HEADER_SEPARATOR}"
                  if len(total_info_str) > config.BUFFER_SIZE_FOR_HEADER:
                       print(f"WARNING: TOTAL_INFO header too large ({len(total_info_str)} bytes). Skipping.", file=sys.stderr)
                       utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[!] هشدار: هدر اطلاعات کلی پوشه خیلی بزرگ است. ارسال نمی‌شود.")
                       # Continue without sending TOTAL_INFO header
                  else:
                       print(f"DEBUG: Sending TOTAL_INFO header: {total_info_str}")
                       client_socket.sendall(total_info_str.encode('utf-8'))
                       utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] هدر اطلاعات کلی پوشه ارسال شد: {total_item_count} آیتم، {utils.format_bytes(total_folder_size)}")

             # Restart time tracking for overall send speed after initial headers
             start_time = time.time()
             last_update_time = start_time
             last_update_bytes = 0
             sent_bytes_total = 0 # Reset total sent bytes counter for speed calculation


             # Generate dummy data chunk for file sending (re-used). Cap size for memory.
             allocated_chunk_size = min(buffer_size, 4 * 1024 * 1024)
             try: dummy_data_chunk = os.urandom(allocated_chunk_size)
             except NotImplementedError: dummy_data_chunk = b'\xAA' * allocated_chunk_size
             if not dummy_data_chunk: # Fallback if random fails
                  print("DEBUG: Failed to generate any dummy data for send, using minimal byte.", file=sys.stderr)
                  dummy_data_chunk = b'\x00'
                  allocated_chunk_size = 1
                  if buffer_size > 1:
                       utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] هشوار: قادر به تولید داده تست بافر با اندازه {utils.format_bytes(buffer_size)} نبود. از بافر ۱ بایتی استفاده می‌شود.")


             # os.walk loop to iterate through directories and their contents
             # This is the main loop for sending all items.
             # Add check for cancel at the start of the os.walk loop itself.
             # Keep track of item count sent for verification
             items_sent_count = 0 # Counter for items (files + folders) actually sent

             # Get the absolute path of the selected folder for comparison
             folder_path_abs = os.path.abspath(folder_path)
             # Use folder_path_abs as the base for relative path calculation later
             # Ensure it's normalized to remove trailing slashes etc for consistent relpath calculation
             # Removed: base_path_for_rel = os.path.normpath(folder_path_abs) # This variable was undefined


             # Use a separate flag to track if the os.walk loop finished naturally
             walk_completed_naturally = False

             for dirpath, dirnames, filenames in os.walk(folder_path):
                 # Check cancel during walk *before* processing items in this directory
                 if cancel_transfer_event.is_set():
                      is_cancelled = True
                      print("DEBUG: Folder send cancelled during directory walk (os.walk check)")
                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] ارسال پوشه توسط کاربر لغو شد.")
                      break # Break the 'for dirpath' loop

                 # Calculate the path of the current directory relative to the original folder_path
                 # FIX: Use folder_path_abs instead of undefined base_path_for_rel
                 relative_dirpath_from_base = os.path.relpath(dirpath, folder_path_abs)

                 # Skip common system directories (modify list in place for efficiency)
                 dirnames[:] = [d for d in dirnames if d.lower() not in ["$recycle.bin", "system volume information"]]
                 # Skip processing contents of system directories if dirpath itself matches
                 if os.path.basename(dirpath).lower() in ["$recycle.bin", "system volume information"]:
                     print(f"DEBUG: Skipping processing contents of system directory: {dirpath}")
                     continue # Skip to the next directory in os.walk


                 # Process subdirectories found in the current `dirpath`
                 # Send FOLDER headers for these subdirectories.
                 # Add check for cancel at the start of the dirnames loop itself.
                 for dirname in dirnames:
                      # --- Check cancel *inside* the dirname loop ---
                      if cancel_transfer_event.is_set():
                           is_cancelled = True
                           print("DEBUG: Folder send cancelled during subdir iteration")
                           utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] ارسال پوشه توسط کاربر لغو شد.")
                           break # Exit dirnames loop

                      # Construct protocol path relative to the *original* selected folder name
                      # FIX: Use folder_path_abs instead of undefined base_path_for_rel
                      path_relative_to_original_base = os.path.relpath(os.path.join(dirpath, dirname), folder_path_abs)
                      protocol_relative_subdir_path = os.path.join(root_folder_name, path_relative_to_original_base).replace(os.sep, '/')
                      if not protocol_relative_subdir_path.endswith('/'):
                          protocol_relative_subdir_path += '/'

                      try: # Try block for sending a single subdirectory header
                           subdir_header_str = f"{config.FOLDER_PROTOCOL_PREFIX}{config.HEADER_SEPARATOR}{config.FOLDER_HEADER_TYPE_FOLDER}{config.HEADER_SEPARATOR}{protocol_relative_subdir_path}{config.HEADER_SEPARATOR}"
                           # Basic check for header size
                           if len(subdir_header_str) > config.BUFFER_SIZE_FOR_HEADER:
                                print(f"WARNING: Subdir header too large ({len(subdir_header_str)} bytes) for '{protocol_relative_subdir_path}'. Skipping.", file=sys.stderr)
                                utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] هشدار: نام پوشه '{protocol_relative_subdir_path}' خیلی طولانی است. نادیده گرفته می‌شود.")
                                continue # Skip this subdirectory (goes to next dirname)

                           # Send folder header bytes
                           client_socket.sendall(subdir_header_str.encode('utf-8'))
                           # print(f"DEBUG: Sent subdir header: {subdir_header_str}") # Verbose
                           # utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] ارسال هدر پوشه: '{protocol_relative_subdir_path}'") # Verbose - Too verbose for status area
                           items_sent_count += 1 # Count the sent folder header

                      except Exception as e:
                           # If header send fails, it's likely a connection issue.
                           print(f"DEBUG: Error sending subdir header {protocol_relative_subdir_path}: {e}", file=sys.stderr)
                           # Raise the exception to be caught by the main sending phase try block.
                           raise Exception(f"Error sending folder header for '{protocol_relative_subdir_path}': {e}") from e

                 # Check if cancelled *after* processing all dirnames for the current dirpath
                 if is_cancelled:
                      break # Exit os.walk loop


                 # Process files found in the current `dirpath`
                 # Send FILE headers and data for these files.
                 # Add check for cancel at the start of the filenames loop itself.
                 for filename in filenames:
                     # --- Check cancel *inside* the filename loop ---
                     if cancel_transfer_event.is_set():
                          is_cancelled = True
                          print("DEBUG: Folder send cancelled during file iteration (filenames check)")
                          utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] ارسال پوشه توسط کاربر لغو شد.")
                          break # Exit filenames loop

                     full_file_path = os.path.join(dirpath, filename)

                     # Construct protocol path relative to the *original* selected folder name
                     # FIX: Use folder_path_abs instead of undefined base_path_for_rel
                     path_relative_to_original_base = os.path.relpath(full_file_path, folder_path_abs)
                     protocol_relative_file_path = os.path.join(root_folder_name, path_relative_to_original_base).replace(os.sep, '/')


                     # --- Try block for handling a single file send (header and data) ---
                     # This block encompasses getting file info, sending header, opening file, and sending data loop.
                     # Exceptions raised here will be caught by the except block below it, within the filenames loop.
                     try: # This try block covers getting size, sending header, opening and sending file data
                          # Get file size
                          # Check if file exists before getting size (might be deleted after walk listed it)
                          if not os.path.exists(full_file_path) or not os.path.isfile(full_file_path):
                               print(f"WARNING: File '{full_file_path}' disappeared or is no longer a file during transfer. Skipping.", file=sys.stderr)
                               utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] هشوار: فایل '{protocol_relative_file_path}' در حین ارسال حذف شد یا تغییر کرد. نادیده گرفته می‌شود.")
                               # Skip this file by continuing the filenames loop
                               continue # Go to the next filename


                          file_size = os.path.getsize(full_file_path)

                          # Send FILE header
                          file_header_str = f"{config.FOLDER_PROTOCOL_PREFIX}{config.HEADER_SEPARATOR}{config.FOLDER_HEADER_TYPE_FILE}{config.HEADER_SEPARATOR}{protocol_relative_file_path}{config.HEADER_SEPARATOR}{file_size}{config.HEADER_SEPARATOR}"

                          # Basic check for header size
                          if len(file_header_str) > config.BUFFER_SIZE_FOR_HEADER:
                               print(f"WARNING: File header too large ({len(file_header_str)} bytes) for '{protocol_relative_file_path}'. Skipping.", file=sys.stderr)
                               utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] هشدار: نام فایل '{protocol_relative_file_path}' خیلی طولانی است. نادیده گرفته می‌شود.")
                               # Skip this file by continuing the filenames loop
                               continue # Go to the next filename

                          # Send file header bytes
                          client_socket.sendall(file_header_str.encode('utf-8'))
                          # print(f"DEBUG: Sent file header: {file_header_str}") # Verbose
                          utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] در حال ارسال فایل: '{protocol_relative_file_path}' ({utils.format_bytes(file_size)})...")
                          items_sent_count += 1 # Count the sent file header


                          # Open and Send file data
                          file_handle = None # Initialize file_handle to None before opening
                          try: # Inner try block for the file data sending loop
                              file_handle = open(full_file_path, "rb") # Open file in binary read mode
                              # print(f"DEBUG: File '{full_file_path}' opened for reading data.") # Verbose

                              sent_bytes_for_file = 0 # Bytes sent for the current file

                              # Loop to send data for the current file
                              while sent_bytes_for_file < file_size:
                                   # --- Check cancel *inside* the data send loop ---
                                   if cancel_transfer_event.is_set(): # Check cancel during data send
                                        is_cancelled = True
                                        print("DEBUG: Folder send cancelled during file data send")
                                        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] ارسال پوشه توسط کاربر لغو شد.")
                                        break # Exit file data send loop

                                   bytes_to_read_now = min(buffer_size, file_size - sent_bytes_for_file)
                                   if bytes_to_read_now <= 0: break # Should not happen if loop condition is correct

                                   # Read from file using the chosen buffer size
                                   try:
                                       bytes_read_chunk = file_handle.read(bytes_to_read_now)
                                   except Exception as e: # Catch errors during file read
                                       print(f"DEBUG: Error reading file chunk '{full_file_path}': {e}", file=sys.stderr)
                                       utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای خواندن فایل '{protocol_relative_file_path}': {e}")
                                       is_cancelled = True # Mark as cancelled due to error
                                       break # Exit file data send loop


                                   # Check if read returned empty bytes prematurely
                                   if not bytes_read_chunk and sent_bytes_for_file < file_size:
                                       print(f"DEBUG: Unexpected end of file while reading '{full_file_path}'. Sent {sent_bytes_for_file}/{file_size}", file=sys.stderr)
                                       utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] پایان غیرمنتظره فایل '{protocol_relative_file_path}' در حین خواندن.")
                                       is_cancelled = True # Mark as cancelled due to incomplete file
                                       break # Exit file data send loop


                                   # Send chunk over socket
                                   try:
                                        # Set a timeout for sending this chunk. Use DATA_TRANSFER_TIMEOUT.
                                        client_socket.settimeout(config.DATA_TRANSFER_TIMEOUT) # Changed timeout constant
                                        client_socket.sendall(bytes_read_chunk)
                                        client_socket.settimeout(None) # Remove timeout after successful send
                                   except socket.timeout:
                                         # This indicates sendall was blocked for too long.
                                         # It's a network/peer issue, treat as a connection error.
                                         utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] زمان انتظار برای ارسال داده فایل '{protocol_relative_file_path}' تمام شد.")
                                         print(f"DEBUG: Timeout during socket send for '{protocol_relative_file_path}'", file=sys.stderr)
                                         is_cancelled = True # Mark as cancelled due to error
                                         break # Exit file data send loop
                                   except Exception as e: # Catch other errors during socket send
                                        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای ارسال داده به سوکت برای فایل '{protocol_relative_file_path}': {e}")
                                        print(f"DEBUG: Error sending data for '{protocol_relative_file_path}': {e}", file=sys.stderr)
                                        is_cancelled = True # Mark as cancelled due to error
                                        break # Exit loop on socket error


                                   sent_bytes_for_file += len(bytes_read_chunk)
                                   sent_bytes_total += len(bytes_read_chunk) # Update total sent bytes for the whole folder

                                   # Update progress (if total size is known) and speed
                                   current_time = time.time()
                                   if total_folder_size is not None and total_folder_size > 0:
                                        progress = (sent_bytes_total / total_folder_size) * 100
                                        # Cap progress at 99.99 to avoid showing 100% before END_TRANSFER is sent
                                        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], min(progress, 99.99))


                                   if current_time - last_update_time >= config.SPEED_UPDATE_INTERVAL:
                                       time_delta = current_time - last_update_time
                                       bytes_since_last_update = sent_bytes_total - last_update_bytes
                                       speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                                       speed_string = utils.format_bytes_per_second(speed_bps)
                                       utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], f"سرعت آپلود پوشه: {speed_string}")

                                       last_update_time = current_time
                                       last_update_bytes = sent_bytes_total

                              # --- End of inner while loop for sending file data ---

                              # After the inner while loop finishes, check if file was sent completely
                              if not is_cancelled and sent_bytes_for_file == file_size:
                                  # File sent successfully. Status message already shown inside loop.
                                  pass
                              # else: File was not sent completely (due to break/cancel/error). Error message shown inside loop.


                          except Exception as e: # Catch errors during file open or errors re-raised from data send loop
                              # If an error occurred while sending data for this file, catch it here.
                              # The specific error message was already shown inside the data send loop.
                              print(f"DEBUG: Error sending file '{protocol_relative_file_path}': {e}", file=sys.stderr)
                              # Mark as cancelled due to error. This might be redundant if already set in inner loop.
                              is_cancelled = True

                          finally: # This finally block runs if the inner try block (where file handle is used) exits
                               # Ensure the file handle for the current file is closed
                               if file_handle:
                                   try:
                                       file_handle.close()
                                       # print(f"DEBUG: File handle '{full_file_path}' closed.") # Verbose
                                   except Exception as e:
                                        print(f"DEBUG: Error closing file handle in file loop finally: {e}", file=sys.stderr)

                         # --- End of Inner try/except/finally for file data send ---


                     except Exception as e: # Catch exceptions during getting file info, sending header (outer try for the file item)
                          # This catches errors related to a specific file item *before* or *during* its processing.
                          # Error message for specific file already shown inside inner blocks.
                          # Mark as cancelled due to error.
                          print(f"DEBUG: Error processing or sending file '{protocol_relative_file_path}': {e}", file=sys.stderr)
                          is_cancelled = True # Ensure cancelled flag is set


                     # --- Check cancel after processing EACH file (after its try/except/finally) ---
                     # This break belongs to the 'for filename in filenames' loop.
                     # It must be inside the loop and after the try/except/finally for processing a single file.
                     if is_cancelled: # Check if cancelled during the file processing
                          break # Exit filenames loop


                 # Check if cancelled *after* processing all filenames for the current dirpath
                 if is_cancelled:
                      break # Exit os.walk loop

             # If the os.walk loop finished without the is_cancelled flag being set
             # (meaning it iterated through all items or broke due to error/cancel inside inner loops
             # and is_cancelled was set there), set walk_completed_naturally.
             # This flag helps distinguish between os.walk finishing all items vs. breaking early.
             # Check if is_cancelled is still False after the os.walk loop completes.
             if not is_cancelled:
                 walk_completed_naturally = True
                 print("DEBUG: os.walk loop finished naturally.")
             else:
                 print("DEBUG: os.walk loop exited early due to cancellation or error.")


             # --- End of os.walk loop ---
             # The breaks inside the loops handle exiting the os.walk loop if is_cancelled becomes True.
             # If os.walk completed fully (walk_completed_naturally is True), we proceed.


             # Step 4: After walking all directories and sending all items (if walk completed naturally)
             # This code runs if the os.walk loop completed without being marked as cancelled internally.
             # Only send END_TRANSFER if the entire walk finished and we are not cancelled.
             if walk_completed_naturally: # Check this flag
                 # Send END_TRANSFER header
                 end_transfer_header_str = f"{config.FOLDER_PROTOCOL_PREFIX}{config.HEADER_SEPARATOR}{config.FOLDER_HEADER_TYPE_END_TRANSFER}{config.HEADER_SEPARATOR}"

                 # Basic check for header size
                 if len(end_transfer_header_str) > config.BUFFER_SIZE_FOR_HEADER:
                      print(f"WARNING: END_TRANSFER header too large ({len(end_transfer_header_str)} bytes). This shouldn't happen.", file=sys.stderr)
                      # This is an internal error, but let's still try to send it
                      # raise ValueError(f"END_TRANSFER header too large.")

                 try: # Try block for sending END_TRANSFER header
                      client_socket.sendall(end_transfer_header_str.encode('utf-8'))
                      print(f"DEBUG: Sent END_TRANSFER header: {end_transfer_header_str}")
                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[+] پایان انتقال پوشه به سرور ارسال شد.")
                      # Set progress to 100% upon sending END_TRANSFER
                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], 100)
                      # Note: transfer_success is set to True *after* successful handshake response from receiver.
                      # Don't set transfer_success = True here yet.


                 except Exception as e:
                      # Error sending END_TRANSFER is also a failure
                      print(f"DEBUG: Error sending END_TRANSFER header: {e}", file=sys.stderr)
                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطا در ارسال پیام پایان انتقال: {e}")
                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_warning'], "هشدار ارسال", "خطا در ارسال پیام پایان انتقال.")
                      is_cancelled = True # Treat as partially failed or errored

             # If cancelled before sending END_TRANSFER (e.g., during os.walk or file send), is_cancelled is already True.


        # --- Except block for the main sending phase (headers and data) ---
        # This block catches exceptions raised during the os.walk loop or sending items (and re-raised from inner try blocks).
        except (socket.timeout, ConnectionRefusedError, ValueError, CancelledError, OSError, RuntimeError, Exception) as e:
             # If not already cancelled by event, mark as cancelled due to error
             if not is_cancelled: # Avoid double reporting if already marked cancelled by specific error inside inner loops
                 # Error message should have been shown where the exception was raised/caught first.
                 # But as a fallback, show a generic one here.
                 # Use server_info if available for error message (already checked if server_info is None before this try block)
                 server_addr_str = f"{server_info[0]}:{server_info[1]}" if server_info else "سرور نامشخص"
                 msg = f"[!] خطایی در حین ارسال پوشه رخ داد: {e}"
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], msg)
                 # Show a show_error dialog for critical errors during send phase
                 if isinstance(e, (ConnectionRefusedError, socket.timeout, OSError, RuntimeError, Exception)): # Specific critical types
                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای ارسال پوشه", f"خطا در حین ارسال پوشه:\n{e}")
                 else: # Less critical errors like ValueErrors from headers
                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_warning'], "هشدار ارسال پوشه", f"خطا در حین ارسال پوشه:\n{e}")

                 print(f"DEBUG: Specific Error caught during send_folder_task (sending phase): {e}", file=sys.stderr)
                 is_cancelled = True # Mark as cancelled due to error
                 # transfer_success remains False

             # If cancelled, exit the try block and proceed to the finally block.


        # --- Finally block for the main sending phase ---
        # This ensures cleanup related to the sending phase is done (like file handles)
        # It runs after the try or except block above finishes.
        finally:
             print("DEBUG: Finished sending phase (data and headers).")
             # Note: File handles are closed in their inner finally blocks.
             # Socket closing happens in the outer finally.
             # No specific cleanup needed here before handshake begins (if not cancelled).


        # --- Handshake Phase (after sending all data and END_TRANSFER) ---
        # This phase is only reached if the sending phase completed successfully (walk completed, END_TRANSFER sent)
        # OR if it was cancelled before/during the walk/send.
        # If cancelled, perform_folder_handshake_client checks the cancel event and exits.
        # If walk finished but END_TRANSFER failed, is_cancelled is True.
        # If walk didn't complete naturally, is_cancelled is True.

        # Only attempt handshake if the walk completed naturally AND we are not cancelled.
        # (The is_cancelled check inside perform_folder_handshake_client provides a secondary check).
        if walk_completed_naturally and not is_cancelled:
             print("DEBUG: Attempting Client Handshake.")
             # Call the client-side handshake function
             handshake_success = perform_folder_handshake_client(
                 client_socket,
                 cancel_transfer_event,
                 gui_callbacks
             )
             # The perform_folder_handshake_client function sets GUI status messages and shows dialogs.
             # It returns True if the server responded OK, False otherwise (including if cancelled during handshake).
             transfer_success = handshake_success # Overall transfer success depends on handshake success
             if not transfer_success:
                  print("DEBUG: Client Handshake failed.")
                  is_cancelled = True # Mark as cancelled if handshake failed
             else:
                  print("DEBUG: Client Handshake successful.")

        else: # If walk didn't complete naturally or was cancelled
            print("DEBUG: Skipping Client Handshake because sending phase did not complete naturally or was cancelled.")
            # is_cancelled is already true if it was cancelled before handshake.
            # transfer_success remains False.


    # --- Outer Exception Handling for the entire function ---
    # This block catches exceptions from initial setup, discovery, connection,
    # or exceptions re-raised from the sending phase or handshake phase.
    # Note: Added general Exception to catch anything re-raised from inner blocks for robustness.
    except (FileNotFoundError, NotADirectoryError, OSError, ConnectionRefusedError, socket.timeout, ValueError, CancelledError, RuntimeError, Exception) as e:
        # If not already cancelled by event, mark as cancelled due to error
        if not is_cancelled: # Avoid double reporting if already marked cancelled by specific error inside inner loops
            # Use server_info if available for error message
            server_addr_str = f"{server_info[0]}:{server_info[1]}" if server_info else "سرور نامشخص"
            msg = f"[!] خطایی در حین ارسال پوشه رخ داد: {e}"
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], msg)
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای ارسال پوشه", f"خطا در هنگام ارسال پوشه به {server_addr_str}:\n{e}")
            print(f"DEBUG: Specific Error caught in OUTER except for send_folder_task: {e}", file=sys.stderr)
            is_cancelled = True # Mark as cancelled due to error
            transfer_success = False # Not successful
        # Note: If CancelledError is caught here, is_cancelled is already true.


    finally: # This finally block runs after the entire function finishes (outer try/except)
        print("DEBUG: send_folder_task finally block entered")
        # Ensure the client socket is closed.
        if client_socket:
            try:
                # Shutdown socket gracefully before closing if possible.
                try: client_socket.shutdown(socket.SHUT_RDWR)
                except OSError as e:
                     if e.errno not in (107, 10057): # Ignore common errors if socket is already closed or reset
                         print(f"DEBUG: Error during socket shutdown: {e}", file=sys.stderr)
                     pass
                except Exception as e:
                     print(f"DEBUG: Unexpected error during socket shutdown: {e}", file=sys.stderr)
                     pass

                client_socket.close()
                print("DEBUG: Client socket closed")
            except Exception as e:
                print(f"DEBUG: Error closing client socket in finally: {e}", file=sys.stderr)


        # Reset GUI elements related to transfer state (Progress bar, Speed display)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], 0) # Reset progress bar
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: N/A - Transfer Finished") # Reset speed display

        # Final status message in status area based on overall outcome
        if transfer_success:
            # Success message for Handshake/overall transfer was shown in Handshake phase.
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[-] ارسال پوشه به پایان رسید.")
        elif is_cancelled:
             # Message for cancellation/error was shown earlier.
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[-] ارسال پوشه لغو شد.")
        else:
             # It failed due to an error that wasn't explicitly handled by a more specific status message
             # The error message should have been shown by the except blocks
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[-] ارسال پوشه با خطا به پایان رسید.")


        # Signal GUI that transfer is finished (resets is_transfer_active)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_transfer_finished'])
        print("DEBUG: send_folder_task finished")