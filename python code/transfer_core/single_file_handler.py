# transfer_core/single_file_handler.py - Server-side connection handler for single file transfer

import socket
import os
import time
import re # Used for basic filename sanitization
import sys # Import sys for stderr


# Import config and utils and helpers using relative imports within the package structure
import config # Assuming config is in the package root
import utils # Assuming utils is in the package root
from .helpers import CancelledError, read_header_from_socket # Import custom exception and helper


# --- File Transfer Server Handler (for single files) ---
# Modified handle_client_connection to accept optional initial_buffer and use read_header_from_socket
def handle_client_connection(client_socket, address, gui_callbacks, cancel_transfer_event, receive_buffer_size, initial_buffer=b""):
    """
    Thread task to manage a single client connection and receive a single file.
    (Updates GUI status, speed, checks for cancellation).

    Args:
        client_socket (socket.socket): The accepted socket for this connection.
        address (tuple): The client's address (IP, Port).
        gui_callbacks (dict): Dictionary of GUI callbacks.
        cancel_transfer_event (threading.Event): Event to signal this task to cancel.
        receive_buffer_size (int): The buffer size to use for socket.recv().
        initial_buffer (bytes): Any initial data already read from the socket before starting this handler.
    """
    print(f"DEBUG: handle_client_connection started for {address} (Single File) with initial buffer size {len(initial_buffer)}")
    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[+] اتصال جدید از {address} برای دریافت فایل تکی")
    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: Connecting...") # Initial speed status

    filesize = 0
    filename_from_header = "N/A" # Store the potentially non-sanitized filename from header
    save_filename = "N/A" # Store the sanitized filename used for saving
    current_buffer_size_from_header = 4096 # بافر اعلام شده توسط فرستنده (فقط برای نمایش/لاگ)

    received_bytes = 0
    is_cancelled = False # Flag to track if cancellation occurred
    transfer_success = False # Flag to track if transfer completed without errors/cancel

    file_handle = None # Initialize file handle to None
    file_path = None # Initialize file_path to None

    # --- Initialize variables that might be used in except/finally blocks for error reporting ---
    header_str_for_debug = "N/A"
    current_remaining_buffer = initial_buffer # Use a separate variable for the buffer passed between header reads


    try: # Outer try block covering header parsing and file receive

        # --- Robust header reading ---
        # We expect the old protocol format: filename|filesize|buffersize|
        # Use read_header_from_socket helper for each segment.

        # Part 1: filename
        # Use a slightly longer timeout for the very first header part read
        header_part1, current_remaining_buffer = read_header_from_socket(
            client_socket, gui_callbacks, cancel_transfer_event,
            initial_buffer=current_remaining_buffer, timeout=config.DISCOVERY_TIMEOUT * 2 # Allow more time for first header
        )
        filename_from_header = header_part1 # This is the raw filename string including path from sender
        print(f"DEBUG: Received header part 1 (filename): '{filename_from_header}'. Remaining buffer size: {len(current_remaining_buffer)}")

        # Part 2: filesize
        header_part2, current_remaining_buffer = read_header_from_socket(
            client_socket, gui_callbacks, cancel_transfer_event,
            initial_buffer=current_remaining_buffer
        )
        print(f"DEBUG: Received header part 2 (filesize): '{header_part2}'. Remaining buffer size: {len(current_remaining_buffer)}")
        try:
            filesize = int(header_part2)
            if filesize < 0:
                raise ValueError(f"Negative filesize received: {filesize}")
            # Add a sanity check for file size (e.g., against a very large number)
            if filesize > config.TEST_FILE_SIZE * 10000: # Example: 10000 times the test file size
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] هشدار: اندازه فایل اعلام شده ({utils.format_bytes(filesize)}) برای '{filename_from_header}' بسیار بزرگ است. ممکن است خطا باشد.")
                 print(f"WARNING: Declared file size {filesize} seems excessively large for '{filename_from_header}'. Aborting receive.", file=sys.stderr)
                 raise ValueError(f"Declared file size ({filesize}) is excessively large for '{filename_from_header}'. Aborting transfer.")

        except ValueError as e:
            raise ValueError(f"Invalid filesize in header: '{header_part2}'. {e}")


        # Part 3: buffersize (optional to use, but required by protocol)
        header_part3, current_remaining_buffer = read_header_from_socket(
            client_socket, gui_callbacks, cancel_transfer_event,
            initial_buffer=current_remaining_buffer
        )
        print(f"DEBUG: Received header part 3 (buffersize): '{header_part3}'. Remaining buffer size: {len(current_remaining_buffer)}")
        try:
            current_buffer_size_from_header = int(header_part3)
            if current_buffer_size_from_header <= 0:
                 raise ValueError("Non-positive buffersize")
        except ValueError:
             # If buffersize part is not a valid positive integer, log warning but use a default
             print(f"WARNING: Invalid buffersize in header: '{header_part3}'. Using default 4096.", file=sys.stderr)
             current_buffer_size_from_header = 4096


        # Reconstruct header for debugging/status based on successfully parsed parts
        header_str_for_debug = f"{filename_from_header}{config.HEADER_SEPARATOR}{filesize}{config.HEADER_SEPARATOR}{current_buffer_size_from_header}"
        print(f"DEBUG: Single file header fully parsed: '{header_str_for_debug}'")


        # Check if cancelled after header receive
        if cancel_transfer_event.is_set():
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] دریافت فایل توسط کاربر لغو شد.")
             is_cancelled = True
             print("DEBUG: File receive cancelled after header receive")
             # Exit the try block, which will lead to the outer finally block
             return # Using return exits the function immediately


        # --- File Receiving ---
        # Use the BASE NAME of the filename from header for local saving
        # It's crucial to sanitize the filename to prevent path traversal attacks.
        save_filename_raw = os.path.basename(filename_from_header)

        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] شروع دریافت: {save_filename_raw} ({utils.format_bytes(filesize)}) از {address}")
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"    بافر دریافت سمت گیرنده: {utils.format_bytes(receive_buffer_size)}")
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"    بافر اعلام شده فرستنده: {utils.format_bytes(current_buffer_size_from_header)}")


        # Ensure progress is reset and speed display is ready
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], 0)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: 0 B/s")


        save_dir = "received_files" # Base directory for received files
        # Use sanitize_path to get a safe full path for the file
        # For a single file, the "relative path" is just the sanitized filename itself.
        try:
             # Use utils.sanitize_path with "." as the relative path base to just sanitize the base directory name itself.
             # This ensures the main 'received_files' directory is valid and created.
             # Then use sanitize_path again with 'save_dir' as base and 'save_filename_raw' as the relative path.

             # First, ensure the base directory 'received_files' is safe and exists.
             # utils.sanitize_path expects a base dir and a path *relative to that base*.
             # To validate and create the 'received_files' folder, we can use os.makedirs directly
             # or use sanitize_path with "." as the base and "received_files" as the 'relative' part.
             # Let's use os.makedirs which is simpler for just the base directory.
             received_files_base_abs = os.path.abspath(save_dir)
             if not os.path.exists(received_files_base_abs):
                  print(f"DEBUG: Base received directory '{received_files_base_abs}' does not exist, attempting to create.")
                  try: os.makedirs(received_files_base_abs, exist_ok=True)
                  except OSError as e:
                       raise OSError(f"Failed to create base received directory '{received_files_base_abs}': {e}") from e # Re-raise
                  except Exception as e:
                       raise RuntimeError(f"Unexpected error creating base received directory '{received_files_base_abs}': {e}") from e
             else:
                  print(f"DEBUG: Base received directory '{received_files_base_abs}' already exists.")


             # Now sanitize the filename part relative to the base directory
             # Pass the base directory (which is now ensured to be valid) and the raw filename as the relative path.
             file_path_base = utils.sanitize_path(received_files_base_abs, save_filename_raw)


             # Handle potential filename conflicts (optional but good practice)
             # Append counter if file already exists.
             final_file_path = file_path_base # Start with the sanitized path
             base, ext = os.path.splitext(file_path_base)
             counter = 1
             original_base = base # Store original base for appending counter
             while os.path.exists(final_file_path):
                 final_file_path = f"{original_base}_{counter}{ext}"
                 counter += 1
                 if counter > 10000: # Avoid infinite loop with too many duplicates
                      raise ValueError(f"Exceeded attempts to find unique filename for {os.path.basename(file_path_base)}")

             file_path = final_file_path # Set the final path for the file
             print(f"DEBUG: Final save path determined: {file_path}")

        except (ValueError, OSError, RuntimeError) as e:
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای نام فایل یا مسیر: {e}")
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای امنیتی/نام فایل", f"خطا در تمیزکاری یا اعتبارسنجی نام فایل دریافتی:\n{e}\nدریافت لغو شد.")
             print(f"DEBUG: Error sanitizing filename '{save_filename_raw}' or preparing path: {e}", file=sys.stderr)
             is_cancelled = True
             # Exit the try block
             return


        start_time = time.time()
        last_update_time = start_time
        last_update_bytes = 0
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: 0 B/s") # Start speed display here
        print(f"DEBUG: Starting file receive loop into {file_path}")

        # Open file here, outside the loop, and use try/finally for closing
        file_handle = None # Ensure file_handle is None if open fails
        try: # Inner try block specifically for file writing and socket receiving loop
            # Handle 0-byte files: create the file and immediately skip data receive.
            if filesize == 0:
                 try:
                      with open(file_path, "wb") as f:
                           pass # Just create an empty file
                      print(f"DEBUG: Empty file '{file_path}' created.")
                      transfer_success = True # 0-byte file creation is successful transfer
                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[+] دریافت فایل خالی کامل شد: '{save_filename_raw}'")
                      # Skip the receive loop and proceed to inner finally and then outer finally
                      return # Exit the function early for 0-byte files

                 except Exception as e:
                      # Error creating empty file
                      print(f"DEBUG: Error creating empty file '{file_path}': {e}", file=sys.stderr)
                      raise Exception(f"Error creating empty file '{save_filename_raw}': {e}") from e


            # If file size > 0, open the file handle and proceed to receive data.
            file_handle = open(file_path, "wb")
            print(f"DEBUG: File '{file_path}' opened for writing.")

            # Process remaining buffer first if any (this data came after the header)
            if current_remaining_buffer:
                 bytes_to_write_now = min(len(current_remaining_buffer), filesize - received_bytes)
                 if bytes_to_write_now > 0:
                      try:
                           file_handle.write(current_remaining_buffer[:bytes_to_write_now])
                           print(f"DEBUG: Wrote {bytes_to_write_now} bytes from initial buffer.")
                      except Exception as e:
                           print(f"DEBUG: Error writing initial buffer to file '{file_path}': {e}", file=sys.stderr)
                           raise Exception(f"Error writing initial buffer to file '{file_path}': {e}") from e # Re-raise to be caught below

                      received_bytes += bytes_to_write_now
                      # print(f"DEBUG: Processed {bytes_to_write_now} bytes from remaining buffer. Total received: {received_bytes}") # Verbose
                 current_remaining_buffer = b"" # Clear the remaining buffer after using it


            # Continue receiving from socket until expected size is reached or cancelled
            # Use the *receiver's configured buffer size* for recv() calls (passed to handle_client_connection)
            recv_buffer_size_for_loop = receive_buffer_size # Use the size passed into this function


            while received_bytes < filesize:
                if cancel_transfer_event.is_set():
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] دریافت فایل توسط کاربر لغو شد.")
                    is_cancelled = True
                    print("DEBUG: File receive cancelled by user")
                    break # Exit loop on cancel

                try:
                    # Receive data chunk using the INDEPENDENT receive_buffer_size
                    # Set a timeout for receiving this chunk. Use DATA_TRANSFER_TIMEOUT.
                    client_socket.settimeout(config.DATA_TRANSFER_TIMEOUT) # Changed timeout constant
                    # Ensure we don't read more bytes than remaining if remaining < buffer size
                    bytes_to_read_now = min(recv_buffer_size_for_loop, filesize - received_bytes)
                    if bytes_to_read_now <= 0:
                         # Should only happen if remaining_buffer fulfilled the file, or filesize was 0 (handled above)
                         break # Exit loop if nothing more to read (file fully received)

                    bytes_read_chunk = client_socket.recv(bytes_to_read_now) # Use receive_buffer_size here
                    client_socket.settimeout(None) # Remove timeout after successful read

                except socket.timeout:
                    # This allows cancel event check. Continue receiving.
                    continue # Go back to the start of the while loop
                except Exception as e: # Catch errors during socket read within the loop
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای خواندن داده از سوکت: {e}")
                    print(f"DEBUG: Error reading from socket during receive: {e}")
                    is_cancelled = True
                    break # Exit loop on socket error

                if not bytes_read_chunk:
                    # This means the sender closed the connection prematurely
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] اتصال با {address} قبل از اتمام دریافت قطع شد.")
                    print(f"DEBUG: Connection lost during receive from {address}")
                    is_cancelled = True
                    break # Exit loop on connection loss

                try:
                    file_handle.write(bytes_read_chunk)
                except Exception as e: # Catch errors during file write within the loop
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای نوشتن داده در فایل: {e}")
                    print(f"DEBUG: Error writing data to file: {e}")
                    is_cancelled = True
                    break # Exit loop on file write error

                received_bytes += len(bytes_read_chunk)

                # Update progress and speed display
                current_time = time.time()
                progress = (received_bytes / filesize) * 100 if filesize > 0 else 0
                utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], progress)

                if current_time - last_update_time >= config.SPEED_UPDATE_INTERVAL:
                    time_delta = current_time - last_update_time
                    bytes_since_last_update = received_bytes - last_update_bytes
                    speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                    speed_string = utils.format_bytes_per_second(speed_bps)
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], f"سرعت دانلود: {speed_string}")

                    last_update_time = current_time
                    last_update_bytes = received_bytes
            print("DEBUG: File receive loop finished")

            # Check if loop completed fully without cancellation and received expected bytes
            if not is_cancelled and received_bytes >= filesize:
                # If loop finished and all bytes received, mark as successful
                transfer_success = True
                print(f"DEBUG: File '{file_path}' seems fully received based on byte count.")
            # else: if is_cancelled is True or received_bytes < filesize, it's not successful


        except Exception as e: # Catch exceptions that occur *after* the file handle is successfully opened but not caught by inner blocks
             # This catches errors like issues with file handle operations outside the main loop,
             # or exceptions raised by `read_header_from_socket` within the outer try.
             if not is_cancelled: # Only report error if not already marked cancelled by user or socket error
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطایی در حین دریافت فایل از {address} رخ داد: {e}")
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای دریافت", f"خطا در دریافت فایل از {address}:\n{e}")
                 print(f"DEBUG: Exception during file receive loop with {address}: {e}")
                 is_cancelled = True # Mark as cancelled due to error
                 transfer_success = False # Not successful

        finally: # This finally block runs if the inner try block (where file handle is used) exits
            print(f"DEBUG: Inner receive file handle finally block entered for {address}")
            # Ensure the file handle is closed
            if file_handle and not file_handle.closed:
                try:
                    file_handle.close()
                    print(f"DEBUG: File handle '{file_path}' closed.")
                except Exception as e:
                    print(f"DEBUG: Error closing file handle in inner finally for '{file_path}': {e}", file=sys.stderr)

            # Clean up incomplete file only if cancelled or error occurred AND file path was created AND file exists AND it wasn't a 0-byte file
            # Only remove if transfer_success is False (meaning it was cancelled or failed) AND file_path exists AND its size > 0
            # The path should only be cleaned up if it was for the file that was being received when error/cancel happened.
            # We need to check current file size received vs expected to confirm it was incomplete.
            # Also need to check if file_path was actually set and exists.
            if not transfer_success and file_path and os.path.exists(file_path) and filesize > 0 and received_bytes < filesize: # Check if file was incomplete and expected bytes > 0
                 try:
                      # Add a small delay to allow file system operations to complete
                      time.sleep(0.01) # Give OS a moment
                      os.remove(file_path)
                      # Use original filename in status message as sanitized one might be less readable
                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] فایل ناقص '{save_filename_raw}' حذف شد.")
                      print(f"DEBUG: Incomplete file '{file_path}' removed.")
                 except Exception as e:
                      # Use original filename in status message
                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطا در حذف فایل ناقص '{save_filename_raw}': {e}")
                      print(f"DEBUG: Error removing incomplete file '{file_path}': {e}", file=sys.stderr)


    # --- Outer Exception Handling ---
    except (socket.timeout, ConnectionResetError, ValueError, CancelledError, OSError, RuntimeError) as e: # Catch specific expected errors for the outer try block (header or initial connection issues)
        # This catches errors from the header reading phase or initial setup before file is opened.
        # Use header_str_for_debug in error message if available
        msg = f"[!] خطا در ارتباط یا هدر با {address}: {e}"
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], msg)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای دریافت فایل", f"خطا در ارتباط یا هدر فایل از فرستنده ({address}):\n{e}\nهدر دریافتی (حدود): {header_str_for_debug}")
        print(f"DEBUG: Connection/Header error during single file receive from {address}: {e}. Header snippet: {header_str_for_debug}...")
        is_cancelled = True # Ensure is_cancelled is set on these errors
        transfer_success = False # Not successful

    except Exception as e: # Catch any other uncaught exceptions from the outer try block
        if not is_cancelled: # Avoid double reporting if already marked cancelled by specific error
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطایی در حین پردازش اتصال از {address} رخ داد: {e}")
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای پردازش اتصال", f"خطا در پردازش اتصال از {address}:\n{e}")
            print(f"DEBUG: Uncaught Exception in handle_client_connection with {address}: {e}", file=sys.stderr)
            is_cancelled = True # Mark as cancelled due to error
            transfer_success = False # Not successful


    finally: # This finally block runs after the outer try/except blocks finish
        print(f"DEBUG: handle_client_connection outer finally block entered for {address}")
        # Ensure the client socket is closed.
        # The main server accept loop might also close the socket, but closing it here
        # ensures it's closed by the handler thread that used it.
        if client_socket:
            try:
                # Shutdown socket gracefully before closing if possible.
                try: client_socket.shutdown(socket.SHUT_RDWR) # Shutdown both read and write
                except OSError as e:
                     # Ignore expected errors if the socket is already partly closed or reset
                     if e.errno != 107: # Skip "Transport endpoint is not connected" error (Linux/macOS)
                          if e.errno != 10057: # Skip "Socket is not connected" error (Windows)
                             print(f"DEBUG: Error during socket shutdown for {address}: {e}", file=sys.stderr)
                     pass # Ignore shutdown errors if socket is already in a bad state
                except Exception as e:
                     print(f"DEBUG: Unexpected error during socket shutdown for {address}: {e}", file=sys.stderr)
                     pass # Ignore other errors


                client_socket.close()
                print(f"DEBUG: Client socket closed for {address}")
            except Exception as e:
                print(f"DEBUG: Error closing client socket in finally for {address}: {e}", file=sys.stderr)


        # Final status update based on whether it was successful or cancelled/failed
        if transfer_success:
             # Final success messages were already shown in the inner try block
             pass
        elif is_cancelled:
             # Cancellation message was already shown
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] انتقال فایل با {address} لغو شد.")
             # No need for show_warning/error if it was a user-initiated cancel
        else:
             # It failed due to an error that wasn't explicitly handled by a more specific status message
             # The error message should have been shown by the except blocks
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] انتقال فایل با {address} با خطا پایان یافت.")


        # Reset GUI elements related to transfer state (Progress bar, Speed display)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], 0) # Reset progress bar
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: N/A - Transfer Finished") # Reset speed display
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[-] هندلر اتصال با {address} پایان یافت.")


        # Signal GUI that the transfer is finished (resets is_transfer_active flag in GUI)
        # This is crucial for allowing the server to accept new connections or enabling other GUI actions.
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_transfer_finished'])
        print(f"DEBUG: handle_client_connection finished for {address}")