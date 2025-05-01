# transfer_core/server.py - Main TCP server logic (bind, listen, accept, protocol detection)

import socket
import threading
import time
import sys # Import sys for stderr

# Import config and utils and helpers using relative imports within the package structure
import config # Assuming config is in the project root
import utils # Assuming utils is in the project root
from .helpers import CancelledError, read_header_from_socket # Import custom exception and helper
# Import handlers from the handlers module - these are the connection handlers
from .handlers import handle_client_connection, handle_client_folder_transfer


def run_tcp_server_task(stop_event, gui_callbacks, set_active_server_port_cb, get_receive_buffer_size_cb):
    """
    Thread task for the main TCP server.
    Binds to a port, listens for connections, accepts them,
    detects the protocol (single file or folder), and starts the appropriate handler thread.

    Args:
        stop_event (threading.Event): Event to signal the server to stop its accept loop.
        gui_callbacks (dict): Dictionary of GUI callbacks provided by the GUI.
                              Includes general callbacks (update_status, show_error, etc.) and state change callbacks (on_server_stopped, on_transfer_started/finished).
                              Must also include 'root' for safe_gui_update and 'set_active_server_port' for this task.
                              Must also include 'start_discovery_thread' callback which is a method in GUI to start the discovery thread via root.after.
                              Must include 'is_transfer_active_cb' to check if another transfer is active.
        set_active_server_port_cb (callable): Callback function to inform the GUI which port was successfully bound (or None if stopped/failed).
        get_receive_buffer_size_cb (callable): Callback function to get the selected receive buffer size from the GUI for handlers.
    """
    print("DEBUG: run_tcp_server_task started")

    tcp_socket = None
    port_bound = False
    active_server_port = None

    try: # Outer try block for server binding and accept loop
        # Try binding to available ports from config
        for port in config.FILE_TRANSFER_PORTS:
            if stop_event.is_set():
                 print(f"DEBUG: Stop event set during TCP port binding attempt on {port}")
                 break # Exit loop if stop requested while trying ports
            try:
                print(f"DEBUG: Attempting to bind TCP server to port {port}")
                tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # Allow reuse of the address. This helps in quickly restarting after stopping.
                tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                # Bind to all interfaces (0.0.0.0) to accept connections from any IP on the local network
                tcp_socket.bind(("0.0.0.0", port))
                tcp_socket.listen(5) # Listen for up to 5 incoming connections queued
                # Set a short timeout for accept() to periodically check stop_event
                tcp_socket.settimeout(config.CANCEL_CHECK_INTERVAL) # Use the cancel check interval

                active_server_port = port
                port_bound = True
                # Inform GUI which port was successfully bound.
                # The GUI will update status and start discovery listener via this callback.
                # Use utils.safe_gui_update as this is called from a worker thread
                utils.safe_gui_update(gui_callbacks['root'], set_active_server_port_cb, port)
                print(f"DEBUG: TCP Server successfully bound to port {port}")
                break # Successfully bound, exit the port trial loop

            except OSError as e:
                 print(f"DEBUG: Failed to bind server to port {port}: {e}", file=sys.stderr)
                 # Report specific OS errors related to ports (address in use, permission denied)
                 if e.errno in (98, 10048): # EADDRINUSE (Linux/macOS), WSAEADDRINUSE (Windows)
                      error_msg = f"[!] پورت TCP {port} انتقال فایل در حال استفاده است. در حال تلاش برای پورت بعدی..."
                 elif e.errno == 10013: # WSAEACCES (Windows) - Permission denied by firewall
                      error_msg = f"[!] دسترسی به پورت TCP {port} انتقال فایل مسدود شده (فایروال؟). در حال تلاش برای پورت بعدی..."
                 else:
                     error_msg = f"[!] خطای OSError در پورت {port} انتقال فایل: {e}. در حال تلاش برای پورت بعدی..."
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], error_msg)
                 if tcp_socket:
                     tcp_socket.close()
                     tcp_socket = None # Ensure socket is closed before trying next port

            except Exception as e:
                 print(f"DEBUG: Uncaught Exception during port binding on {port}: {e}", file=sys.stderr)
                 error_msg = f"[!] خطای ناشناخته در پورت {port} انتقال فایل: {e}. در حال تلاش برای پورت بعدی..."
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], error_msg)
                 if tcp_socket:
                     tcp_socket.close()
                     tcp_socket = None


        if not port_bound:
            # If loop finished without binding to any port
            print("DEBUG: Failed to bind server to any specified TCP port")
            error_msg = "[!] خطا: قادر به راه اندازی سرور انتقال فایل TCP روی هیچ یک از پورت های مشخص شده نبود."
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], error_msg)
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای سرور انتقال فایل", "برنامه قادر به راه اندازی سرور TCP روی هیچ پورتی نبود.\nلطفاً مطمئن شوید پورت ها توسط برنامه دیگری استفاده نشده و فایروال اجازه دسترسی داده است.")
            print("DEBUG: run_tcp_server_task finished due to binding failure")
            # Closing socket happens in finally block

            return # Exit thread if binding failed


        # Main server loop to accept connections
        # Server stays in this loop until stop_event is set
        while not stop_event.is_set():
            # Check if a transfer is already active using the CALLBACK provided by GUI.
            # The GUI's is_transfer_active flag is set when ANY transfer (send or receive) is active.
            # This prevents the server from accepting a new connection while another transfer is happening.
            is_transfer_active_cb = gui_callbacks.get('is_transfer_active_cb')
            if is_transfer_active_cb and is_transfer_active_cb():
                # print("DEBUG: Another transfer is active, pausing accept.") # Too verbose
                # Sleep briefly to avoid busy-waiting while waiting for transfer to finish
                time.sleep(config.CANCEL_CHECK_INTERVAL)
                continue # Skip accept iteration if a transfer is already active

            client_socket = None # Initialize client_socket here before accept
            try:
                # Wait for a client connection with a timeout.
                # This timeout allows the loop to periodically check the stop_event.
                client_socket, address = tcp_socket.accept()
                print(f"DEBUG: Accepted connection from {address}")

                # A transfer is potentially starting, signal GUI *before* handler.
                # The GUI flag is set to True. It will be reset by the handler's finally block
                # or by the error handling below if no handler is successfully started.
                utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_transfer_started'])


                # --- Protocol Detection ---
                # Read an initial chunk from the connected client_socket to determine the protocol.
                # Use a buffer large enough to surely contain the start of any defined header.
                initial_buffer = b""
                protocol_detected = None # Will be 'file' or 'folder'
                protocol_detection_failed = False
                try:
                    # Set a short timeout specifically for reading these initial protocol bytes.
                    # This prevents getting stuck here indefinitely if a client connects but sends nothing.
                    client_socket.settimeout(config.DISCOVERY_TIMEOUT) # Use discovery timeout as a reasonable limit
                    initial_buffer = client_socket.recv(config.BUFFER_SIZE_FOR_HEADER)
                    client_socket.settimeout(None) # Remove timeout after reading initial buffer

                    if not initial_buffer:
                         # Peer closed connection immediately after connecting before sending anything
                         raise ConnectionResetError("Connection closed by peer before sending initial data.")

                    # Attempt to decode initial buffer prefix area to check for the folder protocol prefix.
                    # We only need to check a portion of the buffer for the prefix and separator.
                    prefix_check_bytes = f"{config.FOLDER_PROTOCOL_PREFIX}{config.HEADER_SEPARATOR}".encode('utf-8')
                    # Check if the initial buffer starts with the folder protocol prefix + separator bytes
                    if initial_buffer.startswith(prefix_check_bytes):
                         protocol_detected = 'folder'
                         print(f"DEBUG: Detected Folder Transfer protocol from {address}")
                    else:
                         # Assume the old single-file protocol if the initial buffer does not start with the new folder prefix bytes.
                         protocol_detected = 'file'
                         print(f"DEBUG: Detected Single File Transfer protocol (or unknown) from {address}")


                except socket.timeout:
                     # Timeout reading initial data
                     print(f"DEBUG: Timeout reading initial protocol bytes from {address}. Assuming connection abandoned.")
                     utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] زمان انتظار برای دریافت اطلاعات اولیه از {address} تمام شد. اتصال بسته شد.")
                     protocol_detection_failed = True # Mark as failed
                except ConnectionResetError:
                    # Connection closed by peer right away
                    print(f"DEBUG: Connection reset by peer while reading initial protocol bytes from {address}.")
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] اتصال از {address} قبل از ارسال اطلاعات قطع شد.")
                    protocol_detection_failed = True # Mark as failed
                except Exception as e:
                    # Catch any other error during the initial read attempt
                    print(f"DEBUG: Error reading initial protocol bytes from {address}: {e}", file=sys.stderr)
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای خواندن اطلاعات اولیه از {address}: {e}. اتصال بسته شد.")
                    protocol_detection_failed = True # Mark as failed


                # If protocol detection failed (due to timeout, reset, or other error), close the socket and reset the GUI state flag.
                if protocol_detection_failed:
                    if client_socket:
                        try: client_socket.close()
                        except Exception: pass
                    # Signal GUI that the 'transfer' attempt finished (resets the flag set before detection)
                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_transfer_finished'])
                    continue # Go back to the accept loop to wait for the next connection


                # Get the receive buffer size from the GUI using the callback *before* starting the handler thread.
                # This callback retrieves the user's selected buffer size for *file transfer receiving*.
                # It is used by both single file and folder receive handlers for their socket.recv() calls.
                receive_buffer_size = 65536 # Default value if callback fails or returns invalid data
                try:
                    # Call the callback function here using ()
                    # get_receive_buffer_size_cb is passed to this function as an argument by the Orchestrator
                    if get_receive_buffer_size_cb:
                         chosen_recv_buffer = get_receive_buffer_size_cb()
                         # Validate that the returned value is a positive integer
                         if isinstance(chosen_recv_buffer, int) and chosen_recv_buffer > 0:
                              receive_buffer_size = chosen_recv_buffer
                              # print(f"DEBUG: Using configured receive buffer size: {receive_buffer_size}") # Verbose
                         else:
                              print(f"DEBUG: get_receive_buffer_size callback returned invalid value ({chosen_recv_buffer}), using default 64KB", file=sys.stderr)
                    else:
                         print("DEBUG: get_receive_buffer_size callback not found, using default 64KB", file=sys.stderr)

                except Exception as cb_e:
                     # Catch errors during the callback execution itself
                     print(f"DEBUG: Error calling get_receive_buffer_size callback: {cb_e}, using default 64KB", file=sys.stderr)
                     # Default 64KB remains


                # Start the appropriate handler thread based on the detected protocol.
                # Pass the accepted client_socket and other necessary info/callbacks to the handler.
                if protocol_detected == 'folder':
                     client_handler_thread = threading.Thread(
                         target=handle_client_folder_transfer, # <-- Import from transfer_core.handlers
                         args=(
                             client_socket, # The socket for this specific connection
                             address,       # Client address (IP, Port)
                             gui_callbacks, # All GUI callbacks
                             gui_callbacks['cancel_transfer_event'], # Pass the cancel event for ongoing transfers
                             receive_buffer_size, # The buffer size the handler should use for recv()
                             initial_buffer # Pass the initial buffer read during protocol detection
                         ),
                         name=f"FolderHandler-{address[0]}:{address[1]}", # Meaningful thread name
                         daemon=True # Allow main program to exit even if handler thread is still running
                     )
                     client_handler_thread.start()
                     print(f"DEBUG: Started handle_client_folder_transfer thread for {address}")

                elif protocol_detected == 'file':
                     client_handler_thread = threading.Thread(
                         target=handle_client_connection, # <-- Import from transfer_core.handlers
                         args=(
                             client_socket, # The socket for this specific connection
                             address,       # Client address (IP, Port)
                             gui_callbacks, # All GUI callbacks
                             gui_callbacks['cancel_transfer_event'], # Pass the cancel event for ongoing transfers
                             receive_buffer_size, # The buffer size the handler should use for recv()
                             initial_buffer # Pass the initial buffer read during protocol detection
                         ),
                         name=f"FileHandler-{address[0]}:{address[1]}", # Meaningful thread name
                         daemon=True # Allow main program to exit even if handler thread is still running
                     )
                     client_handler_thread.start()
                     print(f"DEBUG: Started handle_client_connection thread for {address}")

                # Note: gui_callbacks['on_transfer_started']() was called *before* protocol detection.
                # If a handler thread was successfully started, its finally block will call on_transfer_finished().
                # If protocol detection failed and socket was closed, on_transfer_finished() was called in the failure block above.
                # So, the state should be handled correctly regardless of success or failure in detection.


            except socket.timeout:
                # This is expected due to settimeout(config.CANCEL_CHECK_INTERVAL) on tcp_socket.accept().
                # It allows the while loop condition (stop_event.is_set()) to be checked.
                continue # Go back to the start of the while loop

            except Exception as e:
                 # Catch any other uncaught exceptions that might occur *after* accept()
                 # but *before* successfully starting a handler thread (e.g., error getting buffer size).
                 # Note: Errors during protocol detection are caught by the inner try/except.
                 if not stop_event.is_set(): # Avoid reporting error if we're just stopping the server
                     utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای پذیرش اتصال TCP یا راه‌اندازی Handler: {e}")
                     print(f"DEBUG: Error accepting TCP connection or starting handler: {e}", file=sys.stderr)
                     # If an error happened *after* accept but *before* starting a handler,
                     # we need to clean up the accepted socket and reset the transfer state flag.
                     if 'client_socket' in locals() and client_socket:
                          try: client_socket.close()
                          except Exception: pass
                     utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_transfer_finished']) # Reset the flag if no handler started
                 # Add a small sleep to prevent busy-waiting in case of repeated errors
                 time.sleep(0.1)

    except Exception as e:
        # Catch any other uncaught exceptions in the server loop (e.g., error from bind loop outside while)
        print(f"DEBUG: Uncaught Exception in TCP server accept loop: {e}", file=sys.stderr)
        error_msg = f"[!] خطای مرگبار در سرور انتقال فایل TCP: {e}"
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], error_msg)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای سرور انتقال فایل", f"خطای ناشناخته سرور TCP:\n{e}")
        # Gui state updates are handled in the finally block


    finally:
        # This block runs when the run_tcp_server_task thread is stopping (either due to stop_event or an unhandled exception)
        print("DEBUG: run_tcp_server_task finally block entered")
        # Clean up the main listening server socket
        if tcp_socket:
            try:
                # Closing the listening socket will unblock any waiting accept() calls
                # and prevent new connections.
                tcp_socket.close() # Close the listening socket
                print("DEBUG: TCP server listening socket closed")
            except Exception as e:
                print(f"DEBUG: Error closing TCP server listening socket in finally: {e}", file=sys.stderr)

        # Signal GUI that the main server thread has stopped and reset its state
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_server_stopped'])
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[-] سوکت اصلی سرور TCP بسته شد.")
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: N/A - Server Stopped")
        utils.safe_gui_update(gui_callbacks['root'], set_active_server_port_cb, None) # Inform GUI that no port is active
        print("DEBUG: run_tcp_server_task finished")