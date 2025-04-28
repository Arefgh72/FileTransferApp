# filetransfer.py - Logic for file transfer (server and client)

import socket
import os
import threading
import time
import sys

# Import configuration and utilities (using absolute imports)
import config # Assuming config.py is in the package root
import utils # Assuming utils.py is in the package root

# Note: This file is named filetransfer.py.
# When imported in other files, it should be imported as `import filetransfer`.
# Functions defined within this file (like handle_client_connection) are referred to directly by name
# from other functions within THIS SAME FILE (like run_tcp_server_task).

# --- File Transfer Server Functions (Run in threads) ---

def handle_client_connection(client_socket, address, gui_callbacks, cancel_transfer_event, receive_buffer_size):
    """ Thread task to manage client connection and receive file (updates GUI, speed, checks cancel) """
    print(f"DEBUG: handle_client_connection started for {address}")
    gui_callbacks['update_status'](f"[+] اتصال جدید از {address} برای دریافت فایل")
    gui_callbacks['update_speed']("Speed: Connecting...")

    filesize = 0
    filename = "N/A"
    current_buffer_size_from_header = 4096 # بافر اعلام شده توسط فرستنده (فقط برای نمایش/لاگ)

    received_bytes = 0
    is_cancelled = False

    file_handle = None # Initialize file handle to None
    file_path = None # Initialize file_path to None
    remaining_buffer = b""

    # --- Initialize variables that might be used in except/finally blocks for error reporting ---
    header_buffer = b"" # Keep the buffer for error reporting if needed


    try: # Outer try block covering header parsing and file receive
        client_socket.settimeout(10.0) # Timeout for initial header receive attempt

        header_sep_bytes = config.HEADER_SEPARATOR.encode('utf-8')
        min_separators_needed = 2 # Need at least two separators for filename|filesize|buffersize
        max_header_read_buffer = config.BUFFER_SIZE_FOR_HEADER * 4 # Safety limit for header buffer

        # --- Robust header reading loop ---
        start_header_read_time = time.time()
        header_fully_parsed = False

        while not header_fully_parsed:
            if cancel_transfer_event.is_set():
                 gui_callbacks['update_status']("[*] دریافت فایل توسط کاربر لغو شد.")
                 is_cancelled = True
                 print("DEBUG: File receive cancelled during header receive")
                 break # Exit header reading loop


            if time.time() - start_header_read_time > 30.0: # Overall timeout for header reading process
                 raise socket.timeout("Overall timeout waiting for complete file header.")

            # Read a chunk with a short timeout to allow checking cancel event
            try:
                client_socket.settimeout(config.CANCEL_CHECK_INTERVAL)
                # Read a chunk using a reasonable size. Avoid reading excessively large chunks into header buffer.
                # Use the receive_buffer_size passed to the handler, or a default/config size
                chunk_size_to_read = receive_buffer_size if receive_buffer_size > 0 else config.BUFFER_SIZE_FOR_HEADER
                # Don't read more than max_header_read_buffer into the buffer
                chunk_size_to_read = min(chunk_size_to_read, max_header_read_buffer - len(header_buffer))
                if chunk_size_to_read <= 0:
                     # If buffer is already at max and header isn't parsed, it's a malformed header
                     raise ValueError("Header buffer already at max size, header not found.")

                chunk = client_socket.recv(chunk_size_to_read)
                client_socket.settimeout(10.0) # Restore timeout for potential parsing/error handling below

            except socket.timeout:
                continue # Keep trying to read if timeout is due to CANCEL_CHECK_INTERVAL
            except Exception as e:
                 raise Exception(f"Error receiving header chunk: {e}")

            if not chunk:
                 if len(header_buffer) == 0: raise ConnectionResetError("Connection closed by peer before header receive.")
                 else: raise ValueError("Connection closed by peer during header receive.")

            header_buffer += chunk

            # Check if we have enough data to potentially contain the header parts (at least two separators)
            if header_buffer.count(header_sep_bytes) >= min_separators_needed:
                 try:
                      decoded_buffer_temp = header_buffer.decode('utf-8', errors='ignore')
                      # Find the first two separators
                      idx1 = decoded_buffer_temp.find(config.HEADER_SEPARATOR)
                      idx2 = decoded_buffer_temp.find(config.HEADER_SEPARATOR, idx1 + 1)

                      if idx1 != -1 and idx2 != -1:
                           # Extract potential string parts based on separator positions
                           filename_str = decoded_buffer_temp[:idx1]
                           filesize_str = decoded_buffer_temp[idx1 + 1:idx2]
                           buffersize_str_plus_data = decoded_buffer_temp[idx2 + 1:]

                           # Attempt parsing filesize
                           parsed_filesize = int(filesize_str) # Will raise ValueError if not int

                           # Attempt parsing buffersize - extract only leading digits
                           buffersize_digits = ""
                           for char in buffersize_str_plus_data:
                               if char.isdigit():
                                    buffersize_digits += char
                               else:
                                    break # Stop at the first non-digit

                           if not buffersize_digits:
                                # Buffersize part didn't start with digits or was empty, malformed header
                                raise ValueError("Buffer size part does not start with digits or is empty.")

                           parsed_buffersize = int(buffersize_digits) # Will raise ValueError if not int if digits found


                           # If parsing is successful, the header structure is valid.
                           # Construct the exact header string based on parsed values *using the extracted digits for buffersize*.
                           # Note: Use original filename_str as sender might include path, we take basename later.
                           exact_header_str = f"{filename_str}{config.HEADER_SEPARATOR}{filesize_str}{config.HEADER_SEPARATOR}{buffersize_digits}"
                           exact_header_bytes_to_find = exact_header_str.encode('utf-8')

                           # Find the exact bytes of the parsed header in the original buffer
                           header_start_index = header_buffer.find(exact_header_bytes_to_find)

                           if header_start_index != -1:
                               # Found the exact header bytes! This is the end of the header in the buffer.
                               # Set main header variables and remaining buffer.
                               filename = os.path.basename(filename_str) # Use filename_str here after successful parse
                               filesize = parsed_filesize
                               current_buffer_size_from_header = parsed_buffersize # Store for info, not for recv size
                               remaining_buffer = header_buffer[header_start_index + len(exact_header_bytes_to_find):]
                               # header_data_bytes = exact_header_bytes_to_find # Store bytes for log/debug if needed
                               print(f"DEBUG: Header successfully parsed and validated: '{exact_header_str}'. Remaining buffer size: {len(remaining_buffer)}")
                               header_fully_parsed = True # Found header, exit loop
                           else:
                                # Exact bytes not found, even though split/parse worked. This shouldn't happen
                                # if the logic is correct and buffer isn't corrupted by previous reads within this connection.
                                # Could happen if buffer contains multiple incomplete headers or garbage.
                                print(f"DEBUG: Found header parts ('{filename_str}', '{filesize_str}', '{buffersize_digits}') but could not find exact byte sequence ('{exact_header_str}') in buffer. Buffer content start: {header_buffer[:50]}... Continuing to receive header data.")
                                # Check for excessive buffer growth in case we're stuck
                                if len(header_buffer) >= max_header_read_buffer:
                                     raise ValueError("Header buffer size exceeded limit while searching for exact pattern.")

                       # else: Not enough parts after finding 2 separators? (Shouldn't happen if idx1, idx2 != -1)

                 except ValueError as e:
                        # Catch parsing/validation errors here (filesize, buffersize not integers, empty buffersize digits, etc.)
                        print(f"DEBUG: Header parsing error: {e}. Likely malformed header. Decoded buffer start: {decoded_buffer_temp[:100]}... Continuing to receive header data if possible.", file=sys.stderr)
                        # Continue reading header unless buffer is too large
                        if len(header_buffer) > max_header_read_buffer / 2:
                            raise ValueError(f"Malformed header received during parsing: {e}")
                 except Exception as e:
                        # Catch any other unexpected error during parsing attempt
                        print(f"DEBUG: Unexpected error during header parsing attempt: {e}. Decoded buffer start: {decoded_buffer_temp[:100]}... Continuing to receive header data if possible.", file=sys.stderr)
                        if len(header_buffer) > max_header_read_buffer / 2:
                            raise ValueError(f"Unexpected error during header parsing: {e}")

            # If not fully parsed and buffer isn't excessive, check max buffer size before reading more
            if not header_fully_parsed and len(header_buffer) > max_header_read_buffer:
                 raise ValueError("Header buffer size exceeded limit during header read.")

            # If header not fully parsed and buffer isn't excessive, the loop will continue to read more chunks.


        # --- If header was successfully found and parsed, proceed to receive file ---
        # Check if loop exited due to cancellation
        if is_cancelled:
             print("DEBUG: Exiting handle_client_connection due to cancellation after header loop.")
             return # Exit handler early, finally block will run

        # Check if header was fully parsed. If not, and not cancelled, something went wrong in the loop logic.
        if not header_fully_parsed:
             # This should ideally not be reached if the while loop condition and error handling worked,
             # but as a final safety measure if the loop exits unexpectedly without being cancelled:
             raise ValueError("File header was not fully parsed after header reading loop terminated unexpectedly.")


        client_socket.settimeout(None) # Remove socket timeout for the main transfer loop

        # --- File Receiving ---
        gui_callbacks['update_status'](f"[*] شروع دریافت: {filename} ({utils.format_bytes(filesize)}) از {address}")
        gui_callbacks['update_status'](f"    بافر دریافت سمت گیرنده: {utils.format_bytes(receive_buffer_size)}")
        gui_callbacks['update_status'](f"    بافر اعلام شده فرستنده: {utils.format_bytes(current_buffer_size_from_header)}")


        gui_callbacks['update_progress'](0)
        gui_callbacks['update_speed']("Speed: 0 B/s")


        save_dir = "received_files"
        if not os.path.exists(save_dir):
            try:
                os.makedirs(save_dir)
            except OSError as e:
                 gui_callbacks['update_status'](f"[!] خطا در ایجاد پوشه '{save_dir}': {e}")
                 gui_callbacks['show_error']("خطای پوشه", f"خطا در ایجاد پوشه '{save_dir}':\n{e}")
                 print(f"DEBUG: Error creating directory {save_dir}: {e}")
                 is_cancelled = True # Mark as cancelled due to file system error
                 return # Cannot proceed without save directory


        # Sanitize filename to prevent path traversal or invalid characters
        # Basic sanitization: keep only alphanumeric, underscore, dash, space, dot.
        # Remove leading/trailing spaces and dots. Ensure it's not empty.
        filename_sanitized = "".join(c for c in filename if c.isalnum() or c in ('.', '_', '- '))
        filename_sanitized = filename_sanitized.strip(' .') # Remove leading/trailing space/dot

        if not filename_sanitized: # Prevent empty or names that become empty/dots after sanitization
             filename_sanitized = f"received_file_{int(time.time())}" # Fallback name if sanitization results in empty/invalid

        file_path = os.path.join(save_dir, filename_sanitized)

        # Handle potential filename conflicts (optional but good practice)
        base, ext = os.path.splitext(file_path)
        counter = 1
        original_base = base # Store original base to append counter correctly
        while os.path.exists(file_path):
            file_path = f"{original_base}_{counter}{ext}"
            counter += 1
            if counter > 10000: # Avoid infinite loop with too many duplicates
                 gui_callbacks['update_status'](f"[!] خطا: تعداد تلاش برای یافتن نام فایل تکراری بیش از حد شد.")
                 gui_callbacks['show_error']("خطای نام فایل", f"نتوانست نام فایل مناسبی برای '{filename_sanitized}' پیدا کند.")
                 print(f"DEBUG: Failed to find unique filename for {filename_sanitized}")
                 is_cancelled = True
                 return


        start_time = time.time()
        last_update_time = start_time
        last_update_bytes = 0
        gui_callbacks['update_speed']("Speed: 0 B/s")
        print("DEBUG: Starting file receive loop")

        # Open file here, outside the loop, and use try/finally for closing
        file_handle = None # Ensure file_handle is None if open fails
        try: # Inner try block specifically for file writing and socket receiving loop
            file_handle = open(file_path, "wb")

            # Process remaining buffer first if any
            if remaining_buffer:
                 bytes_to_write_now = min(len(remaining_buffer), filesize - received_bytes)
                 if bytes_to_write_now > 0:
                      file_handle.write(remaining_buffer[:bytes_to_write_now])
                      received_bytes += bytes_to_write_now
                      print(f"DEBUG: Processed {bytes_to_write_now} bytes from header buffer.")
                 remaining_buffer = b"" # Clear the remaining buffer after using it


            # Continue receiving from socket
            # Use the *receiver's configured buffer size* for recv() calls
            recv_buffer_size_for_loop = receive_buffer_size # Use the size passed into this function


            while received_bytes < filesize:
                if cancel_transfer_event.is_set():
                    gui_callbacks['update_status']("[*] دریافت فایل توسط کاربر لغو شد.")
                    is_cancelled = True
                    print("DEBUG: File receive cancelled by user")
                    break # Exit loop on cancel

                try:
                    # Receive data chunk using the INDEPENDENT receive_buffer_size
                    client_socket.settimeout(config.CANCEL_CHECK_INTERVAL)
                    # Ensure we don't read more bytes than remaining if remaining < buffer size
                    bytes_to_read_now = min(recv_buffer_size_for_loop, filesize - received_bytes)
                    if bytes_to_read_now <= 0:
                         # Should only happen if remaining_buffer fulfilled the file, or filesize was 0
                         break # Exit loop if nothing more to read

                    bytes_read_chunk = client_socket.recv(bytes_to_read_now) # Use receive_buffer_size here
                    client_socket.settimeout(None) # Remove timeout after successful read

                except socket.timeout:
                    # This allows cancel event check. Continue receiving.
                    continue
                except Exception as e: # Catch errors during socket read within the loop
                    gui_callbacks['update_status'](f"[!] خطای خواندن داده از سوکت: {e}")
                    print(f"DEBUG: Error reading from socket during receive: {e}")
                    is_cancelled = True
                    break # Exit loop on socket error

                if not bytes_read_chunk:
                    # This means the sender closed the connection prematurely
                    gui_callbacks['update_status'](f"[!] اتصال با {address} قبل از اتمام دریافت قطع شد.")
                    print(f"DEBUG: Connection lost during receive from {address}")
                    is_cancelled = True
                    break # Exit loop on connection loss

                try:
                    file_handle.write(bytes_read_chunk)
                except Exception as e: # Catch errors during file write within the loop
                    gui_callbacks['update_status'](f"[!] خطای نوشتن داده در فایل: {e}")
                    print(f"DEBUG: Error writing data to file during receive: {e}")
                    is_cancelled = True
                    break # Exit loop on file write error

                received_bytes += len(bytes_read_chunk)

                current_time = time.time()
                progress = (received_bytes / filesize) * 100 if filesize > 0 else 0
                gui_callbacks['update_progress'](progress)

                if current_time - last_update_time >= config.SPEED_UPDATE_INTERVAL:
                    time_delta = current_time - last_update_time
                    bytes_since_last_update = received_bytes - last_update_bytes
                    speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                    speed_string = utils.format_bytes_per_second(speed_bps)
                    gui_callbacks['update_speed'](f"سرعت دانلود: {speed_string}")

                    last_update_time = current_time
                    last_update_bytes = received_bytes
            print("DEBUG: File receive loop finished")

            if not is_cancelled and received_bytes < filesize:
                 # Loop ended prematurely without cancellation and without receiving full file
                 gui_callbacks['update_status'](f"[!] دریافت فایل ناقص ماند ({utils.format_bytes(received_bytes)}/{utils.format_bytes(filesize)}) از {address}.")
                 print(f"DEBUG: File receive incomplete, received {received_bytes}/{filesize} bytes")
                 is_cancelled = True # Mark as cancelled due to incomplete receive

            if not is_cancelled and received_bytes >= filesize:
                end_time = time.time()
                total_time = end_time - start_time
                average_speed_bps = received_bytes / total_time if total_time > 0 else 0
                average_speed_string = utils.format_bytes_per_second(average_speed_bps)

                gui_callbacks['update_status'](f"[+] فایل '{filename}' با موفقیت در پوشه '{save_dir}' دریافت شد.")
                gui_callbacks['update_status'](f"    سرعت میانگین دریافت: {average_speed_string}")
                gui_callbacks['show_info']("موفقیت", f"فایل '{filename}' با موفقیت دریافت شد.")
                print(f"DEBUG: File {filename} received successfully")
            # else: Status messages handled where break/return occurred


        except Exception as e: # Catch exceptions that occur *after* the file handle is successfully opened but not caught by inner blocks
             if not is_cancelled: # Only report error if not already cancelled by user or socket error
                 gui_callbacks['update_status'](f"[!] خطایی در حین دریافت فایل از {address} رخ داد: {e}")
                 gui_callbacks['show_error']("خطای دریافت", f"خطا در دریافت فایل از {address}:\n{e}")
                 print(f"DEBUG: Exception during file receive loop with {address}: {e}")
                 is_cancelled = True


        finally: # This finally block runs if the inner try block (where file handle is used) exits
            print(f"DEBUG: Inner receive finally block entered for {address}")
            if file_handle and not file_handle.closed:
                try:
                    file_handle.close()
                    print("DEBUG: File handle closed")
                except Exception as e:
                    print(f"DEBUG: Error closing file handle in inner finally: {e}", file=sys.stderr)

            # Clean up incomplete file only if cancelled and file path was created and file exists
            # Use the sanitized filename for path
            if is_cancelled and file_path and os.path.exists(file_path):
                 try:
                      # Add a small delay to allow file system operations to complete
                      time.sleep(0.01)
                      os.remove(file_path)
                      # Use original filename in status message as sanitized one might be less readable
                      gui_callbacks['update_status'](f"[!] فایل ناقص '{filename}' حذف شد.")
                      print(f"DEBUG: Incomplete file {file_path} removed")
                 except Exception as e:
                      # Use original filename in status message
                      gui_callbacks['update_status'](f"[!] خطا در حذف فایل ناقص '{filename}': {e}")
                      print(f"DEBUG: Error removing incomplete file {file_path}: {e}", file=sys.stderr)


    except (socket.timeout, ConnectionResetError, ValueError) as e: # Catch specific errors for the outer try block (header or initial connection issues)
        # Use header_buffer in error message if available and not too long
        header_snippet = header_buffer.decode('utf-8', errors='ignore')[:100] if header_buffer else ""
        msg = f"[!] خطا در ارتباط یا هدر با {address}: {e}"
        gui_callbacks['update_status'](msg)
        gui_callbacks['show_error']("خطای دریافت فایل", f"خطا در ارتباط یا هدر فایل از فرستنده ({address}):\n{e}\nهدر دریافتی (بخش اول): {header_snippet}...")
        print(f"DEBUG: Connection/Header error during file receive from {address}: {e}. Header snippet: {header_snippet}...")
        is_cancelled = True # Ensure is_cancelled is set on these errors

    except Exception as e: # Catch any other uncaught exceptions from the outer try block
        if not is_cancelled: # Avoid double reporting if already marked cancelled by specific error
            gui_callbacks['update_status'](f"[!] خطایی در حین پردازش اتصال از {address} رخ داد: {e}")
            gui_callbacks['show_error']("خطای پردازش اتصال", f"خطا در پردازش اتصال از {address}:\n{e}")
            print(f"DEBUG: Uncaught Exception in handle_client_connection with {address}: {e}", file=sys.stderr)
            is_cancelled = True


    finally: # This finally block runs after the outer try/except blocks finish
        print(f"DEBUG: handle_client_connection finally block entered for {address}")
        if 'client_socket' in locals() and client_socket: # Check if client_socket was successfully created
            try:
                # Attempt a graceful shutdown if possible, then close
                # client_socket.shutdown(socket.SHUT_RDWR) # Can sometimes causes errors
                client_socket.close()
                print(f"DEBUG: Client socket closed for {address}")
            except Exception as e:
                 print(f"DEBUG: Error closing client socket in outer finally: {e}", file=sys.stderr)


        # Reset GUI elements related to transfer state
        gui_callbacks['update_progress'](0) # Reset progress bar
        gui_callbacks['update_speed']("Speed: N/A - Transfer Finished") # Reset speed display
        gui_callbacks['update_status'](f"[-] اتصال با {address} بسته شد.")

        # Signal GUI that the transfer is finished (resets is_transfer_active)
        gui_callbacks['on_transfer_finished']()
        print(f"DEBUG: handle_client_connection finished for {address}")


def listen_for_discovery_task(stop_event, gui_callbacks, get_active_server_port_cb):
    """ Thread task to listen for UDP discovery broadcast messages and respond """
    print("DEBUG: listen_for_discovery_task started")

    udp_socket = None
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Bind to an empty string or "0.0.0.0" to listen on all available interfaces
        udp_socket.bind(("", config.DISCOVERY_PORT))
        gui_callbacks['update_status'](f"[*] سرور انتقال فایل در حال گوش دادن روی UDP پورت {config.DISCOVERY_PORT} (کشف سرور)")
        print(f"DEBUG: Discovery server listening on UDP port {config.DISCOVERY_PORT}")


        while not stop_event.is_set():
            try:
                # Set a short timeout to allow checking the stop_event periodically
                udp_socket.settimeout(0.5)
                message, client_address = udp_socket.recvfrom(1024) # Use a reasonable buffer size

                message = message.decode('utf-8', errors='ignore').strip()
                print(f"DEBUG: Received UDP message from {client_address[0]}: {message}")

                if message == config.DISCOVERY_MESSAGE:
                    # Respond with server details if server is running and bound to a port
                    active_server_port = get_active_server_port_cb()
                    if active_server_port is not None:
                        gui_callbacks['update_status'](f"[+] پیام کشف سرور از {client_address[0]} دریافت شد. در حال ارسال پاسخ...")
                        print(f"DEBUG: Discovery message from {client_address[0]}. Sending response.")
                        current_response = f"{config.SERVER_RESPONSE_BASE} {active_server_port}"
                        udp_socket.sendto(current_response.encode('utf-8'), client_address)
                    else:
                         # This case means the TCP server failed to bind, or stopped.
                         print("DEBUG: Cannot respond to discovery, active_server_port is None or server not running.", file=sys.stderr)


            except socket.timeout:
                # Timeout occurred, check stop_event and continue loop
                continue
            except Exception as e:
                 # Handle other potential errors during receive/sendto
                 print(f"DEBUG: Minor error in UDP Discovery loop: {e}", file=sys.stderr)
                 # Add a small sleep to prevent busy-waiting in case of repeated errors
                 time.sleep(0.1)

    except OSError as e:
        print(f"DEBUG: OSError starting discovery server: {e}", file=sys.stderr)
        # Handle address in use or permission errors
        if e.errno in (98, 10048): # EADDRINUSE (Linux/macOS), WSAEADDRINUSE (Windows)
             error_msg = f"[!] خطا: پورت UDP {config.DISCOVERY_PORT} (کشف سرور) در حال استفاده است. آیا برنامه قبلا اجرا شده و بسته نشده یا برنامه دیگری از این پورت استفاده می‌کند؟"
        elif e.errno == 10013: # WSAEACCES (Windows) - Permission denied by firewall
             error_msg = f"[!] خطا: دسترسی به پورت UDP {config.DISCOVERY_PORT} (کشف سرور) مسدود شده است (فایروال؟). لطفاً دسترسی را مجاز کنید."
        else:
            error_msg = f"[!] خطای مرگبار در شنونده کشف سرور UDP: {e}"
        # Only show error if server is still meant to be running (main server thread might have failed already)
        # Access GUI state via callback if available, default to True
        # Assuming you might add this callback later: gui_callbacks.get('is_server_running_cb')
        # For now, simplified check if active_server_port is still None after binding attempts
        if get_active_server_port_cb() is None: # If server didn't bind, discovery listener error is relevant
             gui_callbacks['update_status'](error_msg)
             gui_callbacks['show_error']("خطای شنونده کشف سرور", error_msg + "\nلطفا برنامه را ری‌استارت کنید.")
        # Setting the stop event might not be strictly necessary here as the thread will likely exit anyway,
        # but it's good practice to signal intent.
        stop_event.set()
    except Exception as e:
        print(f"DEBUG: Uncaught Exception in discovery server: {e}", file=sys.stderr)
        error_msg = f"[!] خطای مرگبار ناشناخته در شنونده کشف سرور UDP: {e}"
        if get_active_server_port_cb() is None: # If server didn't bind, discovery listener error is relevant
             gui_callbacks['update_status'](error_msg)
             gui_callbacks['show_error']("خطای شنونده کشف سرور", f"خطای ناشناخته شنونده کشف سرور UDP:\n{e}\nلطفا برنامه را ری‌استارت کنید.")
        stop_event.set()
    finally:
        print("DEBUG: listen_for_discovery_task finally block entered")
        # Ensure socket is closed
        if udp_socket:
            try: udp_socket.close()
            except Exception: pass # Ignore errors on closing
            print("DEBUG: Discovery socket closed")
        gui_callbacks['update_status']("[-] ترد شنونده کشف سرور متوقف شد.")
        print("DEBUG: listen_for_discovery_task finished")


def run_tcp_server_task(stop_event, gui_callbacks, set_active_server_port_cb):
    """ Thread task for the main TCP server (bind, listen, accept connections) """
    print("DEBUG: run_tcp_server_task started")

    tcp_socket = None
    port_bound = False
    active_server_port = None

    try: # Outer try block for server binding and accept loop
        # Try binding to available ports
        for port in config.FILE_TRANSFER_PORTS:
            if stop_event.is_set():
                 print(f"DEBUG: Stop event set during TCP port binding attempt on {port}")
                 break # Exit loop if stop requested while trying ports
            try:
                print(f"DEBUG: Attempting to bind TCP server to port {port}")
                tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # Allow reuse of the address. This helps in quickly restarting after stopping.
                tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                # Bind to all interfaces
                tcp_socket.bind(("0.0.0.0", port))
                tcp_socket.listen(5) # Listen for up to 5 incoming connections
                # Set a short timeout for accept to periodically check stop_event
                tcp_socket.settimeout(0.5)

                active_server_port = port
                port_bound = True
                # Inform GUI which port was successfully bound.
                # The GUI will update status and start discovery listener via this callback.
                set_active_server_port_cb(port)
                print(f"DEBUG: TCP Server successfully bound to port {port}")
                break # Successfully bound, exit the port trial loop

            except OSError as e:
                 print(f"DEBUG: Failed to bind server to port {port}: {e}", file=sys.stderr)
                 if e.errno in (98, 10048): gui_callbacks['update_status'](f"[!] پورت TCP {port} انتقال فایل در حال استفاده است. در حال تلاش برای پورت بعدی...")
                 elif e.errno == 10013: gui_callbacks['update_status'](f"[!] دسترسی به پورت TCP {port} انتقال فایل مسدود شده (فایروال؟). در حال تلاش برای پورت بعدی...")
                 else:
                     gui_callbacks['update_status'](f"[!] خطای OSError در پورت {port} انتقال فایل: {e}. در حال تلاش برای پورت بعدی...")
                 if tcp_socket:
                     tcp_socket.close()
                     tcp_socket = None # Ensure socket is closed before trying next port

            except Exception as e:
                 print(f"DEBUG: Uncaught Exception during port binding on {port}: {e}", file=sys.stderr)
                 gui_callbacks['update_status'](f"[!] خطای ناشناخته در پورت {port} انتقال فایل: {e}. در حال تلاش برای پورت بعدی...")
                 if tcp_socket:
                     tcp_socket.close()
                     tcp_socket = None


        if not port_bound:
            # If loop finished without binding to any port
            print("DEBUG: Failed to bind server to any specified TCP port")
            error_msg = "[!] خطا: قادر به راه اندازی سرور انتقال فایل TCP روی هیچ یک از پورت های مشخص شده نبود."
            gui_callbacks['update_status'](error_msg)
            gui_callbacks['show_error']("خطای سرور انتقال فایل", "برنامه قادر به راه اندازی سرور TCP روی هیچ پورتی نبود.\nلطفاً مطمئن شوید پورت ها توسط برنامه دیگری استفاده نشده و فایروال اجازه دسترسی داده است.")
            # These are called in the finally block now
            # gui_callbacks['on_server_stopped']() # Signal GUI that server failed to start
            # gui_callbacks['update_speed']("Speed: Server Failed")
            print("DEBUG: run_tcp_server_task finished due to binding failure")
            # Closing socket happens in finally

            return # Exit thread if binding failed


        # Main server loop to accept connections
        while not stop_event.is_set():
            # Check if a transfer is already active using the CALLBACK
            # The GUI's is_transfer_active flag is set when ANY transfer (send or receive) is active.
            # This prevents the server from accepting a new connection while another transfer is happening.
            # Check if callback exists before calling it
            is_transfer_active_cb = gui_callbacks.get('is_transfer_active_cb')
            if is_transfer_active_cb and is_transfer_active_cb():
                print("DEBUG: Another transfer is active, pausing accept.")
                time.sleep(config.CANCEL_CHECK_INTERVAL) # Wait a bit before re-checking
                continue # Skip accept if a transfer is active

            try:
                # Wait for a client connection with a timeout
                client_socket, address = tcp_socket.accept()
                print(f"DEBUG: Accepted connection from {address}")

                # A transfer is starting, signal GUI
                # This happens AFTER accepting the connection but BEFORE starting the handler thread.
                # This ensures the GUI flag is set immediately upon a new connection being handled.
                gui_callbacks['on_transfer_started']()


                # Get the receive buffer size from the GUI using the callback
                receive_buffer_size = 65536 # Default value
                try:
                    # Call the callback function here using ()
                    get_receive_buffer_size_cb = gui_callbacks.get('get_receive_buffer_size')
                    if get_receive_buffer_size_cb:
                         chosen_recv_buffer = get_receive_buffer_size_cb()
                         if isinstance(chosen_recv_buffer, int) and chosen_recv_buffer > 0:
                              receive_buffer_size = chosen_recv_buffer
                              print(f"DEBUG: Using configured receive buffer size: {receive_buffer_size}")
                         else:
                              print(f"DEBUG: get_receive_buffer_size callback returned invalid value ({chosen_recv_buffer}), using default 64KB", file=sys.stderr)
                    else:
                         print("DEBUG: get_receive_buffer_size callback not found, using default 64KB", file=sys.stderr)

                except Exception as cb_e:
                     print(f"DEBUG: Error calling get_receive_buffer_size callback: {cb_e}, using default 64KB", file=sys.stderr)
                     # Default 64KB remains


                # Start a new thread to handle the client connection
                client_handler_thread = threading.Thread(
                    target=handle_client_connection,
                    args=(
                        client_socket,
                        address,
                        gui_callbacks,
                        gui_callbacks['cancel_transfer_event'], # Pass the cancel event
                        receive_buffer_size # Pass the configured receive buffer size
                    ),
                    daemon=True # Allow main program to exit even if thread is running
                )
                client_handler_thread.start()

            except socket.timeout:
                # This is expected due to settimeout(0.5), allows checking stop_event
                continue
            except Exception as e:
                 # Handle errors during accept
                 if not stop_event.is_set(): # Avoid reporting error if we're just stopping
                     gui_callbacks['update_status'](f"[!] خطای پذیرش اتصال TCP: {e}")
                     print(f"DEBUG: Error accepting TCP connection: {e}", file=sys.stderr)
                 # Add a small sleep to prevent busy-waiting in case of repeated errors
                 time.sleep(0.1)

    except Exception as e:
        # Handle any other uncaught exceptions in the server loop
        print(f"DEBUG: Uncaught Exception in TCP server accept loop: {e}", file=sys.stderr)
        error_msg = f"[!] خطای مرگبار در سرور انتقال فایل TCP: {e}"
        gui_callbacks['update_status'](error_msg)
        gui_callbacks['show_error']("خطای سرور انتقال فایل", f"خطای ناشناخته سرور TCP:\n{e}")
        # Gui state updates are in finally block now.
        # gui_callbacks['on_server_stopped']() # Signal GUI that server stopped due to error
        # gui_callbacks['update_speed']("Speed: Server Error")

    finally:
        print("DEBUG: run_tcp_server_task finally block entered")
        # Clean up the server socket
        if tcp_socket:
            try:
                # Closing the socket unblocks the accept call.
                tcp_socket.close() # Close the listening socket
                print("DEBUG: TCP server listening socket closed")
            except Exception as e:
                print(f"DEBUG: Error closing TCP server socket in finally: {e}", file=sys.stderr)

        # Signal GUI that server has stopped and reset state
        gui_callbacks['on_server_stopped']()
        gui_callbacks['update_status']("[-] سوکت اصلی سرور TCP بسته شد.")
        gui_callbacks['update_speed']("Speed: N/A - Server Stopped")
        set_active_server_port_cb(None) # Inform GUI that no port is active
        print("DEBUG: run_tcp_server_task finished")


def discover_file_server_task(gui_callbacks):
    """ Thread task for the client to discover available file servers using UDP broadcast """
    print("DEBUG: discover_file_server_task started")

    udp_socket = None
    found_server_info = None # (ip, port) tuple if found

    try:
        gui_callbacks['update_status'](f"[*] در حال جستجو برای سرور فایل در شبکه روی UDP پورت {config.DISCOVERY_PORT}...")
        gui_callbacks['update_speed']("Speed: Discovering Server...")
        print(f"DEBUG: Broadcasting discovery message on UDP port {config.DISCOVERY_PORT}")

        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # No overall timeout on socket, use loop timeout

        message = config.DISCOVERY_MESSAGE.encode('utf-8')
        try:
            # Send broadcast message to the discovery port
            udp_socket.sendto(message, ('<broadcast>', config.DISCOVERY_PORT))
        except Exception as e:
            # Catch send errors specifically
            raise Exception(f"Error sending discovery broadcast: {e}")


        # Wait for a response
        # Use a loop that checks for the cancel event and also respects an overall timeout
        start_discover_time = time.time()
        while not gui_callbacks['cancel_transfer_event'].is_set() and (time.time() - start_discover_time) < config.DISCOVERY_TIMEOUT:
             try:
                  # Set a short timeout within the loop to allow checking cancel event
                  udp_socket.settimeout(config.CANCEL_CHECK_INTERVAL) # Use CANCEL_CHECK_INTERVAL

                  response, server_address = udp_socket.recvfrom(1024)
                  response = response.decode('utf-8', errors='ignore').strip()
                  print(f"DEBUG: Received UDP response from {server_address[0]}: {response}")

                  # Check if the response starts with the expected base and contains a port number
                  if response.startswith(config.SERVER_RESPONSE_BASE):
                       parts = response.split()
                       if len(parts) == 2:
                            try:
                                server_port = int(parts[1])
                                # Found a valid server response
                                found_server_info = (server_address[0], server_port)
                                gui_callbacks['update_status'](f"[+] سرور فایل پیدا شد در {server_address[0]}:{server_port}")
                                print(f"DEBUG: File server found: {found_server_info}")
                                break # Exit the response loop (found server)

                            except ValueError:
                                print(f"DEBUG: Invalid port number in discovery response from {server_address[0]}: {parts[1]}", file=sys.stderr)
                                continue # Continue listening for other responses
                       else:
                            print(f"DEBUG: Malformed discovery response from {server_address[0]}: {response}", file=sys.stderr)
                            continue # Continue listening for other responses

             except socket.timeout:
                 # This is expected due to settimeout(config.CANCEL_CHECK_INTERVAL), just loop again
                 continue
             except Exception as e:
                 # Handle other errors during receive
                 print(f"DEBUG: Error during UDP discovery response receive: {e}", file=sys.stderr)
                 # Continue listening if there are errors, unless it's a fatal socket error
                 time.sleep(0.05) # Small sleep to avoid busy loop on errors

        # If loop exited because of timeout and no server was found, AND NOT cancelled
        if not gui_callbacks['cancel_transfer_event'].is_set() and found_server_info is None:
             gui_callbacks['update_status']("[*] جستجوی سرور انتقال فایل به پایان رسید اما سروری پیدا نشد.")
             gui_callbacks['show_warning']("سرور یافت نشد", f"سرور فایلی در شبکه پیدا نشد ({config.DISCOVERY_TIMEOUT} ثانیه زمان انتظار). لطفا مطمئن شوید برنامه در حالت دریافت روی کامپیوتر دیگر در حال اجرا است و فایروال اجازه ارتباط UDP را می‌دهد.")
             print("DEBUG: No file server found within timeout.")


    except OSError as e:
         # Handle errors during socket creation or sendto
         print(f"DEBUG: OSError during discovery broadcast: {e}", file=sys.stderr)
         if e.errno == 10013: # WSAEACCES (Windows)
             error_msg = f"[!] خطا: دسترسی به پورت UDP {config.DISCOVERY_PORT} برای ارسال پیام کشف سرور مسدود شده است (فایروال؟). لطفاً دسترسی را مجاز کنید."
         else:
             error_msg = f"[!] خطای OSError در حین کشف سرور: {e}"
         gui_callbacks['update_status'](error_msg)
         gui_callbacks['show_error']("خطای کشف سرور", error_msg)

    except Exception as e:
        # Handle any other uncaught exceptions
        print(f"DEBUG: Uncaught Exception during file server discovery: {e}", file=sys.stderr)
        error_msg = f"[!] خطای ناشناخته در حین کشف سرور: {e}"
        gui_callbacks['update_status'](error_msg)
        gui_callbacks['show_error']("خطای کشف سرور", f"خطای ناشناخته کشف سرور:\n{e}")


    finally:
        print("DEBUG: discover_file_server_task finally block entered")
        # Ensure socket is closed
        if udp_socket:
            try: udp_socket.close()
            except Exception: pass # Ignore errors on closing
            print("DEBUG: Discovery socket closed")

        # Return the found server info (or None) so the client thread can use it
        return found_server_info


def send_file_task(filepath, buffer_size, gui_callbacks, cancel_transfer_event):
    """ Thread task for the client to send a file to a discovered server """
    print(f"DEBUG: send_file_task started for {filepath} with buffer size {buffer_size}")

    client_socket = None
    file_handle = None
    is_cancelled = False
    server_info = None # Initialize server_info

    try: # Outer try block for the entire send process

        # Step 1: Discover the server
        # The discovery task runs and returns the result. It handles its own UI updates/errors.
        server_info = discover_file_server_task(gui_callbacks)

        if cancel_transfer_event.is_set():
             gui_callbacks['update_status']("[*] ارسال فایل توسط کاربر پس از کشف سرور لغو شد.")
             is_cancelled = True
             print("DEBUG: File send cancelled after discovery")
             return # Exit if cancelled


        if server_info is None:
             # If discovery failed (timed out without cancel), the discovery_file_server_task already reported it.
             # If discovery was cancelled, is_cancelled is True.
             # Just exit here.
             if not is_cancelled: # Only print if not already cancelled by event
                 print("DEBUG: File send cannot proceed, no server found.")
             return # Exit if no server found or discovery was cancelled


        server_ip, server_port = server_info

        # Step 2: Connect to the server
        gui_callbacks['update_speed'](f"Speed: Connecting to {server_ip}:{server_port}...")
        print(f"DEBUG: Attempting to connect to TCP server at {server_ip}:{server_port}")
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(10) # Timeout for connection attempt

        print(f"DEBUG: Attempting socket.connect to {server_ip}:{server_port}")
        client_socket.connect((server_ip, server_port))
        print("DEBUG: Socket connection established")
        client_socket.settimeout(None) # Remove timeout after connection
        gui_callbacks['update_status']("[+] اتصال با سرور برای ارسال فایل برقرار شد.")

        if cancel_transfer_event.is_set():
             gui_callbacks['update_status']("[*] ارسال فایل توسط کاربر پس از اتصال لغو شد.")
             is_cancelled = True
             print("DEBUG: File send cancelled after connection")
             return # Exit if cancelled

        # Step 3: Send file header (filename|filesize|buffersize)
        filename = os.path.basename(filepath)
        try:
             filesize = os.path.getsize(filepath)
        except Exception as e:
             # This check was already done in send_file_ui, but keep a safeguard here
             raise IOError(f"Could not get size of file {filepath}: {e}") # Raise IO error if cannot get file size


        # Header format: filename|filesize|buffersize
        header_str = f"{filename}{config.HEADER_SEPARATOR}{filesize}{config.HEADER_SEPARATOR}{buffer_size}"
        header_bytes = header_str.encode('utf-8')

        # Basic check to prevent excessively large headers
        if len(header_bytes) > config.BUFFER_SIZE_FOR_HEADER:
             error_msg = f"[!] خطای داخلی: هدر فایل خیلی بزرگ است ({len(header_bytes)} بایت > {config.BUFFER_SIZE_FOR_HEADER} بایت). نام فایل خیلی طولانی است؟"
             gui_callbacks['update_status'](error_msg)
             gui_callbacks['show_error']("خطای ارسال هدر", "اطلاعات فایل (نام، حجم) بیش از حد طولانی است.")
             is_cancelled = True
             print(f"DEBUG: Header too large: {len(header_bytes)} > {config.BUFFER_SIZE_FOR_HEADER}")
             return # Exit if header is too large

        print(f"DEBUG: Sending header: {header_str}")
        client_socket.sendall(header_bytes)
        gui_callbacks['update_status'](f"[*] هدر فایل ارسال شد: {filename} | {utils.format_bytes(filesize)} | {utils.format_bytes(buffer_size)}")
        print(f"DEBUG: Sent header ({len(header_bytes)} bytes)")


        # Step 4: Send the file data
        gui_callbacks['update_status'](f"[*] در حال ارسال فایل: {filename} ({utils.format_bytes(filesize)}) به {server_ip}...")
        gui_callbacks['update_progress'](0)
        gui_callbacks['update_speed']("Speed: 0 B/s")

        # Open file here, use try/finally for closing
        file_handle = None
        try: # Inner try block for file reading and socket sending loop
            file_handle = open(filepath, "rb") # Open file in binary read mode

            sent_bytes = 0
            start_time = time.time()
            last_update_time = start_time
            last_update_bytes = 0
            print("DEBUG: Starting file send loop")

            # Use the chosen buffer size for reading from file and sending
            send_buffer_size_for_loop = buffer_size # Use the size passed into this function

            while sent_bytes < filesize:
                if cancel_transfer_event.is_set():
                    gui_callbacks['update_status']("[*] ارسال فایل توسط کاربر لغو شد.")
                    is_cancelled = True
                    print("DEBUG: File send cancelled by user")
                    break # Exit loop on cancel

                try:
                    # Read a chunk from the file using the chosen buffer size
                    bytes_to_read_now = min(send_buffer_size_for_loop, filesize - sent_bytes)
                    if bytes_to_read_now <= 0:
                         # Should only happen if filesize was 0 initially or sent_bytes == filesize
                         break
                    bytes_read_chunk = file_handle.read(bytes_to_read_now)
                except Exception as e: # Catch errors during file read
                     gui_callbacks['update_status'](f"[!] خطای خواندن فایل '{filename}': {e}")
                     print(f"DEBUG: Error reading file '{filename}': {e}", file=sys.stderr)
                     is_cancelled = True
                     break # Exit loop on file read error


                if not bytes_read_chunk:
                    # Should only happen if file was smaller than expected or reached EOF
                     if sent_bytes < filesize:
                          gui_callbacks['update_status'](f"[!] پایان غیرمنتظره فایل '{filename}' در حین خواندن.")
                          print("DEBUG: Unexpected end of file during read")
                          is_cancelled = True
                     break # Exit loop if read returns empty bytes (e.g. EOF)

                try:
                    # Send the chunk over the socket
                    client_socket.settimeout(config.CANCEL_CHECK_INTERVAL) # Short timeout to check cancel event
                    client_socket.sendall(bytes_read_chunk)
                    client_socket.settimeout(None) # Remove timeout after successful send
                except socket.timeout:
                     gui_callbacks['update_status']("[!] زمان انتظار برای ارسال داده تمام شد.")
                     print("DEBUG: Timeout during socket send")
                     is_cancelled = True
                     break
                except Exception as e: # Catch errors during socket send
                    gui_callbacks['update_status'](f"[!] خطای ارسال داده به سوکت: {e}")
                    print(f"DEBUG: Error sending data: {e}", file=sys.stderr)
                    is_cancelled = True
                    break # Exit loop on socket error


                sent_bytes += len(bytes_read_chunk)

                # Update progress and speed display
                current_time = time.time()
                progress = (sent_bytes / filesize) * 100 if filesize > 0 else 0
                gui_callbacks['update_progress'](progress)

                if current_time - last_update_time >= config.SPEED_UPDATE_INTERVAL:
                    time_delta = current_time - last_update_time
                    bytes_since_last_update = sent_bytes - last_update_bytes
                    speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                    speed_string = utils.format_bytes_per_second(speed_bps)
                    gui_callbacks['update_speed'](f"سرعت آپلود: {speed_string}")

                    last_update_time = current_time
                    last_update_bytes = sent_bytes
            print("DEBUG: File send loop finished")


            # Step 5: Completion Status
            # Check if loop completed fully without cancellation
            if not is_cancelled and sent_bytes < filesize:
                 # Loop ended prematurely without cancellation and without sending full file
                 gui_callbacks['update_status'](f"[!] ارسال فایل ناقص ماند ({utils.format_bytes(sent_bytes)}/{utils.format_bytes(filesize)}) به {server_ip}.")
                 print(f"DEBUG: File send incomplete, sent {sent_bytes}/{filesize} bytes")
                 is_cancelled = True # Mark as cancelled due to incomplete send


            if not is_cancelled and sent_bytes >= filesize:
                end_time = time.time()
                total_time = end_time - start_time
                average_speed_bps = sent_bytes / total_time if total_time > 0 else 0
                average_speed_string = utils.format_bytes_per_second(average_speed_bps)

                gui_callbacks['update_status'](f"[+] فایل '{filename}' با موفقیت ارسال شد.")
                gui_callbacks['update_status'](f"    سرعت میانگین آپلود: {average_speed_string}")
                gui_callbacks['show_info']("موفقیت", f"فایل '{filename}' با موفقیت ارسال شد.")
                print(f"DEBUG: File {filename} sent successfully")
            # else: Status messages handled where break conditions were met


        except Exception as e: # Catch exceptions occurring after file handle is open but not in inner loops
             if not is_cancelled:
                 gui_callbacks['update_status'](f"[!] خطایی در حین ارسال فایل به {server_ip} رخ داد: {e}")
                 gui_callbacks['show_error']("خطای ارسال", f"خطا در حین ارسال فایل به {server_ip}:\n{e}")
                 print(f"DEBUG: Exception during file send loop to {server_ip}: {e}", file=sys.stderr)
                 is_cancelled = True


    except FileNotFoundError:
        # This should ideally not happen if validation in UI worked, but as a safeguard
        gui_callbacks['update_status'](f"[!] خطای ارسال فایل: فایل '{filepath}' پیدا نشد.")
        gui_callbacks['show_error']("خطای فایل", f"فایل '{os.path.basename(filepath)}' پیدا نشد.")
        print(f"DEBUG: File not found: {filepath}")
        is_cancelled = True # Mark as cancelled due to file error
    except IOError as e:
         gui_callbacks['update_status'](f"[!] خطای دسترسی به فایل: {e}")
         gui_callbacks['show_error']("خطای فایل", f"خطا در دسترسی به فایل '{os.path.basename(filepath)}':\n{e}")
         print(f"DEBUG: IOError accessing file: {e}", file=sys.stderr)
         is_cancelled = True
    except ConnectionRefusedError:
        # server_ip and server_port should be set if discovery was successful
        server_ip, server_port = server_info if server_info else ("N/A", "N/A")
        msg = f"[!] خطا: اتصال به سرور {server_ip}:{server_port} رد شد. آیا سرور هنوز فعال است؟"
        gui_callbacks['update_status'](msg)
        gui_callbacks['show_error']("خطای اتصال", f"سرور در {server_ip}:{server_port} اتصال را رد کرد.\nممکن است متوقف شده باشد.")
        print(f"DEBUG: Connection refused to {server_ip}:{server_port}")
        is_cancelled = True
    except socket.timeout:
        # This catches timeout during client_socket.connect()
        msg = "[!] خطا: زمان انتظار برای اتصال به سرور تمام شد."
        gui_callbacks['update_status'](msg)
        gui_callbacks['show_error']("خطای اتصال", "زمان انتظار برای اتصال به سرور تمام شد.")
        print("DEBUG: Socket timeout during connection attempt")
        is_cancelled = True
    except Exception as e:
        # Catch any other uncaught exceptions during the process
        if not is_cancelled: # Avoid reporting error if already marked cancelled by specific error
            msg = f"[!] خطایی در حین ارسال فایل رخ داد: {e}"
            gui_callbacks['update_status'](msg)
            gui_callbacks['show_error']("خطای ارسال", f"خطایی در هنگام ارسال فایل رخ داد:\n{e}")
            print(f"DEBUG: Uncaught Exception in send_file_task: {e}", file=sys.stderr)
            is_cancelled = True

    finally:
        print("DEBUG: send_file_task finally block entered")
        # Clean up resources
        if file_handle:
            try:
                file_handle.close()
                print("DEBUG: File handle closed")
            except Exception as e:
                print(f"DEBUG: Error closing file handle in finally: {e}", file=sys.stderr)

        if client_socket:
            try:
                # Shutdown the socket gracefully if possible before closing
                # client_socket.shutdown(socket.SHUT_RDWR) # Sometimes causes errors
                client_socket.close()
                print("DEBUG: Client socket closed")
            except Exception as e:
                print(f"DEBUG: Error closing client socket in finally: {e}", file=sys.stderr)


        # Reset GUI elements related to transfer state
        gui_callbacks['update_progress'](0) # Reset progress bar
        gui_callbacks['update_speed']("Speed: N/A - Transfer Finished") # Reset speed display
        gui_callbacks['update_status']("[-] اتصال کلاینت بسته شد.")

        # Signal GUI that transfer is finished (resets is_transfer_active)
        gui_callbacks['on_transfer_finished']()
        print("DEBUG: send_file_task finished")


# --- Public functions to be called by GUI ---

def start_file_server(gui_callbacks, stop_event, discovery_stop_event):
    """ Starts the file transfer server components in separate threads """
    print("DEBUG: filetransfer.start_file_server called")

    # Start the TCP server thread (bind, listen, accept connections)
    server_thread = threading.Thread(
        target=run_tcp_server_task,
        args=(
            stop_event,
            gui_callbacks,
            gui_callbacks['set_active_server_port'] # Pass callback to report bound port
        ),
        daemon=True # Allow main GUI thread to exit even if server is running
    )
    server_thread.start()
    # The discovery listener is started *after* the TCP server successfully binds,
    # via a callback from run_tcp_server_task (_start_discovery_thread in GUI).

    return server_thread

def stop_file_server(stop_event, discovery_stop_event, cancel_transfer_event, active_port):
    """ Stops the file transfer server components """
    print("DEBUG: filetransfer.stop_file_server called")
    # Signal all relevant threads to stop
    discovery_stop_event.set()
    stop_event.set()
    cancel_transfer_event.set() # Cancel any ongoing transfer handled by the server

    # Attempt to unblock the server's accept call if it's blocking
    # Connect to the active port from localhost
    if active_port is not None:
        try:
            print(f"DEBUG: Attempting to connect to localhost:{active_port} to unblock TCP accept")
            # Using 127.0.0.1 (localhost) is usually sufficient to unblock a listener bound to 0.0.0.0
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1) # Short timeout for the connection attempt
            sock.connect(('127.0.0.1', active_port))
            # Sending a small amount of data might sometimes be necessary, but close is usually enough
            # sock.sendall(b'stop') # Note: If you send data here, handle_client_connection needs to expect it or discard it safely
            sock.close()
            print("DEBUG: Unblock connection sent.")
        except ConnectionRefusedError:
             # This is okay, means the server socket was likely already closed or failed
             print(f"DEBUG: Unblock connection to {active_port} refused, server likely already stopped or stopping.")
        except Exception as e:
            print(f"DEBUG: Failed to connect to localhost:{active_port} for unblock: {e}", file=sys.stderr)


def start_file_client(filepath, buffer_size, gui_callbacks, cancel_transfer_event):
    """ Starts the file transfer client task in a separate thread """
    print("DEBUG: filetransfer.start_file_client called")
    client_thread = threading.Thread(
        target=send_file_task,
        args=(
            filepath,
            buffer_size,
            gui_callbacks,
            cancel_transfer_event # Pass the cancel event
        ),
        daemon=True # Allow main GUI thread to exit
    )
    client_thread.start()
    return client_thread

def start_file_discovery_listener(stop_event, gui_callbacks, get_active_server_port_cb):
    """ Starts the UDP discovery listener task in a separate thread """
    print("DEBUG: filetransfer.start_file_discovery_listener called")
    discovery_thread = threading.Thread(
        target=listen_for_discovery_task,
        args=(
            stop_event,
            gui_callbacks,
            get_active_server_port_cb # Pass callback to get active port
        ),
        daemon=True
    )
    discovery_thread.start()
    return discovery_thread