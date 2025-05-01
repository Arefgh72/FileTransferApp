# filetransfer.py - Orchestrator and public API for file/folder transfer operations
# This file should be in the project root directory, alongside gui.py, config.py, utils.py, etc.

import socket # Needed for unblocking sockets in stop_file_server
import threading # Needed to interact with threading events
import sys # Needed for accessing sys.stderr in FATAL ERROR print

# Import configuration and utilities (using absolute imports from project root)
import config # Assuming config is in the project root
import utils # Assuming utils is in the project root

# Import the core transfer logic modules from the subpackage (transfer_core)
# Note: This orchestrator file imports specific functions/classes from the subpackage.
# Modules within the subpackage should use relative imports (from .module import ...).
try:
    # Import the main server task function
    from transfer_core.server import run_tcp_server_task
    # Import the discovery task functions (listener for server, discoverer for client)
    from transfer_core.discovery import listen_for_discovery_task, discover_file_server_task
    # Import the client send task functions (single file and folder)
    from transfer_core.clients import send_file_task, send_folder_task
    # Import the custom exception class if needed for error handling or passing
    from transfer_core.helpers import CancelledError # Import CancelledError from helpers

except ImportError as e:
    # This block executes if the transfer_core package or its modules cannot be imported.
    print(f"FATAL ERROR: Could not import core transfer modules from transfer_core package.", file=sys.stderr)
    print(f"Ensure the 'transfer_core' directory and its files (__init__.py, server.py, handlers.py, clients.py, discovery.py, helpers.py) are present and correctly structured relative to this file.", file=sys.stderr)
    print(f"Detailed Error: {e}", file=sys.stderr)
    # Re-raise the exception so the main application exits.
    # The main application (main.py or gui.py) will catch this and potentially show a user-friendly error message.
    raise e


# --- Public functions to be called by GUI (The API of the filetransfer module) ---
# These functions act as intermediaries, starting threads that run tasks from the core modules.

def start_file_server(gui_callbacks, stop_event, discovery_stop_event, get_receive_buffer_size_cb):
    """
    Starts the file/folder transfer server components in separate threads.
    This includes the main TCP listener and triggers the start of the UDP discovery listener.

    Args:
        gui_callbacks (dict): Dictionary of callbacks provided by the GUI.
                              Includes general callbacks (update_status, show_error, etc.) and state callbacks (on_server_stopped, on_transfer_started/finished).
                              Must also include 'root' for safe_gui_update and 'set_active_server_port' for the server task.
                              Must also include 'start_discovery_thread' callback which is a method in GUI to start the discovery thread via root.after.
        stop_event (threading.Event): Event to signal the main server thread (run_tcp_server_task) to stop its accept loop.
        discovery_stop_event (threading.Event): Event to signal the discovery listener thread (listen_for_discovery_task) to stop.
        get_receive_buffer_size_cb (callable): Callback function from GUI to get the selected receive buffer size.
    """
    print("DEBUG: filetransfer.start_file_server called (Orchestrator)")

    # The GUI provides a callback ('start_discovery_thread') which is a method in the GUI class.
    # The run_tcp_server_task will call this callback *after* it successfully binds to a port.
    # This ensures discovery starts only when the server is actually ready.

    # Start the main TCP server thread (run_tcp_server_task imported from transfer_core.server)
    server_thread = threading.Thread(
        target=run_tcp_server_task,
        args=(
            stop_event,           # Pass the stop event for the main server loop
            gui_callbacks,        # Pass all GUI callbacks to the server task
            gui_callbacks['set_active_server_port'], # Pass specific callback for port reporting
            get_receive_buffer_size_cb # Pass specific callback for receive buffer size
        ),
        name="FileServerTCP", # Give threads meaningful names for debugging
        daemon=True # Allow main GUI thread to exit even if server is running
    )
    server_thread.start()

    # The discovery listener thread (listen_for_discovery_task) is NOT started here directly.
    # It is started by the GUI class via the 'start_discovery_thread' callback,
    # which is invoked by the run_tcp_server_task thread after it successfully binds.

    return server_thread # Return the main server thread object if the caller needs it


def stop_file_server(stop_event, discovery_stop_event, cancel_transfer_event, active_port):
    """
    Signals the file/folder transfer server components to stop.

    Args:
        stop_event (threading.Event): Event to signal the main server thread (run_tcp_server_task) to stop.
        discovery_stop_event (threading.Event): Event to signal the discovery listener thread (listen_for_discovery_task) to stop.
        cancel_transfer_event (threading.Event): Event to signal any ongoing transfer handlers (handle_client_connection/folder_transfer) to stop.
        active_port (int or None): The TCP port the server's listening socket is bound to, used to unblock accept().
    """
    print("DEBUG: filetransfer.stop_file_server called (Orchestrator)")
    # Signal all relevant threads to stop by setting their respective Events
    discovery_stop_event.set()      # Signal the UDP discovery listener
    stop_event.set()                # Signal the main TCP server accept loop
    cancel_transfer_event.set()     # Signal any active client handler threads (receiving transfers)


    # Attempt to unblock the server's blocking accept() call if it's currently blocked.
    # This is done by establishing a temporary connection to the server's listening port from localhost.
    if active_port is not None:
        try:
            print(f"DEBUG: Attempting to connect to localhost:{active_port} to unblock TCP accept")
            # Using '127.0.0.1' (localhost) is usually sufficient to unblock a listener bound to '0.0.0.0'.
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Set a short timeout for the connection attempt itself to avoid blocking here if the server is already fully down.
            sock.settimeout(0.5)
            try:
                 # Attempt to connect to the listening port
                 sock.connect(('127.0.0.1', active_port))
                 # Sending a minimal amount of data is sometimes necessary to fully unblock accept.
                 # The handler side (if started) should be robust enough to read/discard unexpected small amounts of data,
                 # or the socket might just be closed by the kernel upon peer close.
                 sock.sendall(b'\0') # Send a null byte to trigger activity
            except (ConnectionRefusedError, socket.timeout):
                 # Server socket is already closed or connection timed out, this is okay.
                 # This means the unblock attempt wasn't strictly necessary or happened too late.
                 print(f"DEBUG: Unblock connection to {active_port} refused or timed out, server likely already stopped or stopping.")
            except Exception as e:
                 # Catch any other unexpected errors during the connect/send/close on the unblock socket
                 print(f"DEBUG: Unexpected error during unblock socket operation on {active_port}: {e}", file=sys.stderr)
            finally:
                 # Ensure the temporary socket created for unblocking is closed, regardless of connect success/failure.
                 sock.close()
                 print("DEBUG: Unblock connection attempt finished for file/folder server.")

        except Exception as e:
            # Catch any error during the initial socket creation for the unblocking attempt
            print(f"DEBUG: Failed to create socket for unblock attempt on {active_port}: {e}", file=sys.stderr)
            pass # Ignore errors during the unblocking attempt


# Note: start_file_discovery_listener is called by GUI via root.after triggered by set_active_server_port_cb

def start_file_discovery_listener(stop_event, gui_callbacks, get_active_server_port_cb):
    """
    Starts the UDP discovery listener task in a separate thread (server side).
    This function should be called by a mechanism that runs in the GUI thread
    after the TCP server successfully binds (e.g., via root.after triggered by set_active_server_port_cb).

    Args:
        stop_event (threading.Event): Event to signal the listener thread (listen_for_discovery_task) to stop.
        gui_callbacks (dict): Dictionary of GUI callbacks provided by the GUI.
                              Includes general callbacks (update_status, show_error, etc.).
                              Must also include 'root' for safe_gui_update and 'is_server_running_cb'.
        get_active_server_port_cb (callable): Callback function from GUI to get the current active server port.
    """
    print("DEBUG: filetransfer.start_file_discovery_listener called (Orchestrator)")
    # Start the discovery listener thread (listen_for_discovery_task imported from transfer_core.discovery)
    discovery_thread = threading.Thread(
        target=listen_for_discovery_task,
        args=(
            stop_event,                 # Pass the stop event for discovery
            gui_callbacks,              # Pass all GUI callbacks
            get_active_server_port_cb   # Pass specific callback for active port
        ),
        name="FileDiscoveryUDP", # Give thread a meaningful name
        daemon=True # Allow main GUI thread to exit
    )
    discovery_thread.start()
    return discovery_thread # Return the thread object if the caller needs it


def start_file_client(filepath, buffer_size, gui_callbacks, cancel_transfer_event):
    """
    Starts the single file transfer client task in a separate thread.
    This task handles server discovery, connection, sending header, and sending file data.

    Args:
        filepath (str): The full path to the file to send.
        buffer_size (int): The buffer size to use for reading from file and sending data over the socket.
        gui_callbacks (dict): Dictionary of GUI callbacks provided by the GUI.
                              Includes general callbacks (update_status, show_info/warning/error, update_progress, update_speed)
                              and state callbacks (on_transfer_finished).
                              Must also include 'root' for safe_gui_update.
        cancel_transfer_event (threading.Event): Event provided by GUI to signal the client task to cancel.
    """
    print("DEBUG: filetransfer.start_file_client called (Orchestrator)")
    # Start the client send task thread (send_file_task imported from transfer_core.clients)
    client_thread = threading.Thread(
        target=send_file_task,
        args=(
            filepath,
            buffer_size,
            gui_callbacks,          # Pass all GUI callbacks
            cancel_transfer_event   # Pass the cancel event
        ),
        name="FileClientSend", # Give thread a meaningful name
        daemon=True # Allow main GUI thread to exit
    )
    client_thread.start()
    return client_thread # Return the thread object if the caller needs it


def start_folder_client(folder_path, buffer_size, gui_callbacks, cancel_transfer_event):
    """
    Starts the folder transfer client task in a separate thread.
    This task handles server discovery, connection, sending folder structure and data.
    Includes a simple handshake mechanism at the end for verification.

    Args:
        folder_path (str): The full path to the folder to send.
        buffer_size (int): The buffer size to use for reading from files and sending data over the socket.
        gui_callbacks (dict): Dictionary of GUI callbacks provided by the GUI.
        cancel_transfer_event (threading.Event): Event provided by GUI to signal the client task to cancel.
    """
    print("DEBUG: filetransfer.start_folder_client called (Orchestrator)")
    # Start the client send task thread (send_folder_task imported from transfer_core.clients)
    client_thread = threading.Thread(
        target=send_folder_task,
        args=(
            folder_path,
            buffer_size,
            gui_callbacks,          # Pass all GUI callbacks
            cancel_transfer_event   # Pass the cancel event
        ),
        name="FolderClientSend", # Give thread a meaningful name
        daemon=True # Allow main GUI thread to exit
    )
    client_thread.start()
    return client_thread # Return the thread object if the caller needs it