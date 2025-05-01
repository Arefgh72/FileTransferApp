# transfer_core/helpers.py - Common helper functions and exceptions for transfer operations

import socket
import time
import sys # Import sys for stderr in error prints if needed (though avoided in helper itself)

# Import configuration using standard absolute import (assuming project root is in sys.path)
import config


# Define a custom exception for cancellation
class CancelledError(Exception):
    """Custom exception to signal operation cancellation."""
    pass


# --- Helper for Reading Headers (Reusable) ---
# Added check for cancel_event directly inside the loop
# Added initial_buffer parameter to handle data already read by the caller
def read_header_from_socket(sock, gui_callbacks, cancel_event, initial_buffer=b"", timeout=10.0, max_buffer=config.BUFFER_SIZE_FOR_HEADER * 4):
    """
    Reads data from socket until a header separator is found or timeout occurs.
    Includes initial_buffer to prepend previously read data.
    Checks cancel_event periodically.

    Args:
        sock (socket.socket): The socket to read from.
        gui_callbacks (dict): Dictionary of GUI callbacks (passed for consistency/future use, not used directly here).
        cancel_event (threading.Event): Event to check for cancellation.
        initial_buffer (bytes): Data already read before calling this function.
        timeout (float): Overall timeout for header reading from the start of the call.
        max_buffer (int): Maximum buffer size to prevent memory exhaustion.

    Returns:
        tuple: (decoded_header_string, remaining_buffer_bytes)

    Raises:
        socket.timeout: If overall timeout occurs.
        ConnectionResetError: If connection is closed by peer.
        ValueError: If max_buffer is exceeded or malformed data is received.
        CancelledError: If cancel_event is set during read.
        Exception: For other socket errors.
    """
    # print(f"DEBUG: read_header_from_socket started, initial buffer size: {len(initial_buffer)}") # Too verbose here
    header_buffer = initial_buffer # Start with provided buffer
    header_sep_bytes = config.HEADER_SEPARATOR.encode('utf-8')
    start_time = time.time()

    # Check buffer first if it contains the separator
    separator_index = header_buffer.find(header_sep_bytes)
    if separator_index != -1:
         # print(f"DEBUG: Separator found in initial buffer at index {separator_index}") # Too verbose
         header_bytes = header_buffer[:separator_index]
         remaining_buffer = header_buffer[separator_index + len(header_sep_bytes):]
         try:
              decoded_header = header_bytes.decode('utf-8')
              # print(f"DEBUG: Header extracted from initial buffer: '{decoded_header}'. Remaining buffer size: {len(remaining_buffer)}") # Too verbose
              return decoded_header, remaining_buffer
         except Exception as e:
              # Using standard print for error in helper, not GUI callback
              print(f"DEBUG: Could not decode header bytes from initial buffer: {header_buffer}. Error: {e}", file=sys.stderr)
              raise ValueError(f"Malformed header received in initial buffer: Could not decode bytes. {e}")

    # If separator not found in initial buffer, read from socket
    while time.time() - start_time < timeout:
        if cancel_event.is_set():
            print("DEBUG: Cancel event set during header read from socket loop.")
            raise CancelledError("Operation cancelled during header receive.")

        try:
            # Set a short timeout to allow checking the stop_event/cancel_event periodically
            sock.settimeout(config.CANCEL_CHECK_INTERVAL)
            # Read a chunk. Adjust chunk size based on remaining buffer space.
            # Ensure we don't read more than max_buffer allows
            chunk_size_to_read = min(4096, max_buffer - len(header_buffer)) # Read in small increments once buffer has some content
            if len(header_buffer) == 0: chunk_size_to_read = min(config.BUFFER_SIZE_FOR_HEADER, max_buffer) # Read a larger chunk initially

            if chunk_size_to_read <= 0:
                 # This means len(header_buffer) is already >= max_buffer
                 raise ValueError("Header buffer size exceeded limit before finding separator.")

            chunk = sock.recv(chunk_size_to_read)
            # Note: The overall timeout is handled by the while loop condition.
            # The socket timeout here is only to make recv non-blocking for long periods
            # so we can check the cancel_event.
            elapsed_time = time.time() - start_time
            remaining_timeout = timeout - elapsed_time
            if remaining_timeout < 0: remaining_timeout = 0 # Prevent negative timeout
            sock.settimeout(remaining_timeout) # Adjust socket timeout for the *next* recv call


        except socket.timeout:
            # This is expected due to settimeout, just loop again to check cancel_event/overall timeout
            continue
        except Exception as e:
             print(f"DEBUG: Error receiving header chunk from socket: {e}", file=sys.stderr)
             raise Exception(f"Error receiving header chunk: {e}")


        if not chunk:
            # Peer closed connection
            if len(header_buffer) == 0:
                # If no data was read *at all*, it's likely the peer closed before sending anything meaningful.
                raise ConnectionResetError("Connection closed by peer before header receive.")
            else:
                 # If some data was read but then connection closed, it's likely a malformed header followed by close.
                 raise ValueError("Connection closed by peer during header receive.")


        header_buffer += chunk
        # print(f"DEBUG: Header buffer size: {len(header_buffer)}. Current content start: {header_buffer[:50]}...") # Too verbose


        # Check if separator is now in the buffer
        separator_index = header_buffer.find(header_sep_bytes)
        if separator_index != -1:
            # Found the separator! Extract header and remaining data.
            header_bytes = header_buffer[:separator_index]
            remaining_buffer = header_buffer[separator_index + len(header_sep_bytes):]
            try:
                decoded_header = header_bytes.decode('utf-8')
                print(f"DEBUG: Full header found: '{decoded_header}'. Remaining buffer size: {len(remaining_buffer)}")
                return decoded_header, remaining_buffer
            except Exception as e:
                 # Using standard print for error in helper, not GUI callback
                 print(f"DEBUG: Could not decode header bytes: {header_buffer}. Error: {e}", file=sys.stderr)
                 raise ValueError(f"Malformed header received: Could not decode bytes. {e}")


        # If separator not found, check if buffer is getting too large
        if len(header_buffer) > max_buffer:
            raise ValueError("Header buffer size exceeded limit without finding separator.")

    # If loop finishes without finding separator after overall timeout
    raise socket.timeout("Overall timeout waiting for complete header.")