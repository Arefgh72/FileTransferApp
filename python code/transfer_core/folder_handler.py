# transfer_core/folder_handler.py - Server-side connection handler for folder transfer

import socket
import os
import time
import re # Used for basic filename sanitization
import sys # Import sys for stderr
import threading # Used for accessing threading events

# Import config and utils and helpers using relative imports within the package structure
import config # Assuming config is in the package root
import utils # Assuming utils is in the package root
from .helpers import CancelledError, read_header_from_socket # Import custom exception and helper
from .handshake import perform_folder_handshake_server # Import the server-side handshake function


# --- Folder Transfer Server Handler (for folders) ---
def handle_client_folder_transfer(client_socket, address, gui_callbacks, cancel_transfer_event, receive_buffer_size, initial_buffer=b""):
    """
    Thread task to manage client connection and receive folder (updates GUI, speed, checks cancel).
    This handler processes headers and file data according to the custom folder transfer protocol.
    Includes Count/Size verification and calls the separate handshake logic.

    Args:
        client_socket (socket.socket): The accepted socket for this connection.
        address (tuple): The client's address (IP, Port).
        gui_callbacks (dict): Dictionary of GUI callbacks.
        cancel_transfer_event (threading.Event): Event to signal this task to cancel.
        receive_buffer_size (int): The buffer size to use for socket.recv().
        initial_buffer (bytes): Any initial data already read from the socket before starting this handler.
    """
    print(f"DEBUG: handle_client_folder_transfer started for {address} (Folder) with initial buffer size {len(initial_buffer)}")
    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[+] اتصال جدید از {address} برای دریافت پوشه")
    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: Connecting...") # Initial speed status

    save_dir_base = "received_folders" # Base directory for received folders
    current_save_dir = None # This will be set to the actual base directory *inside* save_dir_base based on client's root folder name
    session_root_name = None # Store the determined root folder name for the session
    received_files_base_abs = None # Absolute path to the base received_folders directory

    received_bytes_total = 0 # Total bytes received for the entire folder transfer
    items_received_count = 0 # Total number of files + folders received (including root)

    total_expected_items = None # Expected total item count from TOTAL_INFO header
    total_expected_size = None # Expected total size from TOTAL_INFO header


    current_file_bytes_expected = 0
    current_file_bytes_received = 0
    current_file_handle = None
    current_file_path = None
    current_item_path_protocol = "N/A" # The path string as received in the header (for status messages and error reporting)

    is_cancelled = False # Flag indicating if the transfer was cancelled by user or error
    transfer_success = False # Flag indicating if the transfer completed successfully (reached END_TRANSFER and verification passed)

    received_buffer = initial_buffer # Start with initial buffer from server accept

    # State machine for receiving
    STATE_WAITING_FOR_ROOT_FOLDER_HEADER = 0
    STATE_WAITING_FOR_TOTAL_INFO_HEADER = 1 # New state
    STATE_WAITING_FOR_ITEM_HEADER = 2 # Main loop state for FOLDER/FILE headers
    STATE_RECEIVING_FILE_DATA = 3 # Receiving data for a specific file
    STATE_WAITING_FOR_HANDSHAKE_REQUEST = 4 # After END_TRANSFER
    STATE_HANDSHAKE_COMPLETE = 5 # After sending response

    current_state = STATE_WAITING_FOR_ROOT_FOLDER_HEADER # Start in new initial state


    start_time = time.time()
    last_update_time = start_time
    last_update_bytes = 0


    # --- Helper function to clean up current file handle ---
    def close_current_file():
         """Helper to safely close the current file handle and reset related variables."""
         nonlocal current_file_handle, current_file_path # Access outer scope variables
         if current_file_handle:
             try:
                 current_file_handle.close()
                 print(f"DEBUG: File handle '{current_file_path}' closed.")
             except Exception as e:
                 print(f"DEBUG: Error closing file handle in handler: {e}", file=sys.stderr)
             current_file_handle = None
             current_file_path = None


    # --- Outer try block for the entire folder transfer handling and cleanup ---
    try:

        # --- Initial Setup (Create base directory) ---
        try:
             # Ensure base directory for received folders exists
             received_files_base_abs = os.path.abspath(save_dir_base)
             if not os.path.exists(received_files_base_abs):
                  print(f"DEBUG: Base received directory '{received_files_base_abs}' does not exist, attempting to create.")
                  try: os.makedirs(received_files_base_abs, exist_ok=True)
                  except OSError as e:
                       raise OSError(f"Failed to create base received directory '{received_files_base_abs}': {e}") from e # Re-raise
                  except Exception as e:
                       raise RuntimeError(f"Unexpected error creating base received directory '{received_files_base_abs}': {e}") from e
             else:
                  print(f"DEBUG: Base received directory '{received_files_base_abs}' already exists.")

             # This variable seems unused? Let's keep the logic inside the state machine for creating current_save_dir
             # received_dir_abs = received_files_base_abs # Set the absolute base directory path


             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] آماده دریافت پوشه در پوشه اصلی '{save_dir_base}'...")
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], 0) # Reset progress bar
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: 0 B/s") # Start speed display here


        except (ValueError, OSError, RuntimeError) as e:
             # Catch errors specific to initial setup (directory creation).
             msg = f"[!] خطا در آماده‌سازی پوشه دریافت از {address}: {e}"
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], msg)
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای آماده‌سازی دریافت", f"خطا در آماده‌سازی پوشه دریافت از فرستنده ({address}):\n{e}")
             print(f"DEBUG: Error during initial setup for {address}: {e}", file=sys.stderr)
             is_cancelled = True # Mark as cancelled due to initial error
             # No socket yet, cannot send error handshake
             return # Cannot proceed with transfer, exit handler early


        # --- Main loop to receive headers and file data ---
        # This loop runs as long as not cancelled AND handshake is not complete.
        # It now handles different states based on the expected next header/data.
        while not cancel_transfer_event.is_set() and current_state != STATE_HANDSHAKE_COMPLETE:
             # Use a try block inside the loop to handle exceptions per item/chunk
             try:

                 # --- State: WAITING_FOR_ROOT_FOLDER_HEADER ---
                 if current_state == STATE_WAITING_FOR_ROOT_FOLDER_HEADER:
                      print("DEBUG: State: WAITING_FOR_ROOT_FOLDER_HEADER")
                      # Read data until a full header segment is found (PROTOCOL_PREFIX|TYPE|Path)
                      # read_header_from_socket handles reading from socket with timeout and cancel check.

                      # Read the prefix segment first
                      header_prefix_segment, received_buffer = read_header_from_socket(
                          client_socket, gui_callbacks, cancel_transfer_event,
                          initial_buffer=received_buffer, timeout=config.DISCOVERY_TIMEOUT * 2 # Allow more time for first header
                      )

                      # Check for Handshake Request here too, though unlikely as first message
                      if header_prefix_segment.encode('utf-8').strip() == config.HANDSHAKE_REQUEST_SIGNAL.strip():
                           print("DEBUG: Received handshake request signal as first header? Protocol violation.")
                           # Prepend the signal back to the buffer before raising, so it can be processed by the outer loop's logic if needed (though this is an error case)
                           received_buffer = config.HANDSHAKE_REQUEST_SIGNAL + received_buffer
                           raise ValueError("Received handshake request as the first header. Expected FOLDER protocol prefix.")

                      if header_prefix_segment != config.FOLDER_PROTOCOL_PREFIX:
                           # Prepend the unknown prefix back to the buffer before raising, maybe it's part of a single file header?
                           # No, the server already detected the protocol in run_tcp_server_task. If we are in handle_client_folder_transfer, it must start with FOLDER_PROTOCOL_PREFIX.
                           raise ValueError(f"Invalid folder protocol prefix received: '{header_prefix_segment}'. Expected '{config.FOLDER_PROTOCOL_PREFIX}'.")

                      # Read the type segment
                      header_type_segment, received_buffer = read_header_from_socket(
                           client_socket, gui_callbacks, cancel_transfer_event,
                           initial_buffer=received_buffer
                      )

                      # Read the path segment
                      header_path_segment, received_buffer = read_header_from_socket(
                           client_socket, gui_callbacks, cancel_transfer_event,
                           initial_buffer=received_buffer
                      )

                      # Now we have the core parts: item_type, item_path
                      first_item_type = header_type_segment
                      first_item_path_raw = header_path_segment # This is the intended root path from client (e.g., "MyPhotos/")

                      # Update current_item_path_protocol here for status/error messages (for initial header)
                      current_item_path_protocol = first_item_path_raw # Use raw path for initial status

                      if first_item_type != config.FOLDER_HEADER_TYPE_FOLDER:
                           # The first item must be the root folder header according to our protocol design
                           raise ValueError(f"First item received was not a FOLDER header (Type: {first_item_type}). Expected root folder header.")

                      # Determine the root directory name for the session from the client's path ("MyPhotos/" -> "MyPhotos").
                      normalized_client_root_path_str = os.path.normpath(first_item_path_raw).replace('\\', '/')
                      root_folder_name_for_session = ""

                      # Extract the first non-empty path part as the potential root name
                      parts = normalized_client_root_path_str.split('/')
                      for part in parts:
                           if part and part not in ['.', '..']:
                                root_folder_name_for_session = part
                                break # Found the first valid name component

                      if not root_folder_name_for_session:
                           # If path was empty, ".", "/", or contained only slashes/dots/..'s
                           root_folder_name_for_session = f"received_folder_{int(time.time())}"
                           utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] نام پوشه اصلی از مسیر '{first_item_path_raw}' استخراج نشد. با نام موقت '{root_folder_name_for_session}' ذخیره می‌شود.")
                           print(f"WARNING: Could not get root folder name from client's path '{first_item_path_raw}'. Using fallback '{root_folder_name_for_session}'.", file=sys.stderr)

                      # Sanitize the extracted root name *before* using it to build the full path.
                      try:
                           sanitized_root_name = utils.sanitize_filename_part(root_folder_name_for_session)
                           if not sanitized_root_name:
                                sanitized_root_name = f"received_folder_{int(time.time())}_sanitized_fallback"

                      except ValueError as e: # Catch sanitization errors for the root name
                           sanitized_root_name = f"received_folder_{int(time.time())}_sanitization_error"
                           utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای تمیزکاری نام پوشه اصلی '{root_folder_name_for_session}': {e}. با نام موقت '{sanitized_root_name}' ذخیره می‌شود.")
                           print(f"WARNING: Error sanitizing root folder name '{root_folder_name_for_session}': {e}. Using fallback '{sanitized_root_name}'.", file=sys.stderr)


                      # Determine the actual full base directory path for saving this session's content
                      try:
                           # Use sanitize_path to combine the base received_folders directory and the sanitized root name
                           # The base_dir for sanitize_path should be the pre-checked received_files_base_abs
                           # The relative_path for sanitize_path is the sanitized root name + trailing slash to indicate folder
                           current_save_dir = utils.sanitize_path(received_files_base_abs, sanitized_root_name + '/')

                           # Note: sanitize_path should handle creation if needed based on its internal logic,
                           # or we explicitly create it here. Let's ensure creation explicitly here after getting the path.
                           if not os.path.exists(current_save_dir):
                                os.makedirs(current_save_dir, exist_ok=True)
                                print(f"DEBUG: Created session base directory: {current_save_dir}")
                           else:
                                print(f"DEBUG: Session base directory already exists: {current_save_dir}. Content will be added/overwritten.")


                           session_root_name = sanitized_root_name # Store the final sanitized root name for later use
                           print(f"DEBUG: Session root name determined and sanitized: {session_root_name}")
                           print(f"DEBUG: Session base save directory set to: {current_save_dir}")

                           # Increment item count for the root folder header received and successfully processed
                           items_received_count += 1
                           print(f"DEBUG: Items received count after root folder: {items_received_count}")


                      except (ValueError, OSError, RuntimeError) as e: # Catch creation errors or sanitization errors from sanitize_path
                           raise ValueError(f"Error preparing session base directory '{save_dir_base}/{sanitized_root_name}': {e}") from e


                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] پوشه اصلی دریافت در مسیر '{current_save_dir}' آماده شد.")
                      current_state = STATE_WAITING_FOR_TOTAL_INFO_HEADER # Transition to wait for TOTAL_INFO

                 # --- State: WAITING_FOR_TOTAL_INFO_HEADER ---
                 elif current_state == STATE_WAITING_FOR_TOTAL_INFO_HEADER:
                      print("DEBUG: State: WAITING_FOR_TOTAL_INFO_HEADER")

                      # Read data until a full header segment is found (PROTOCOL_PREFIX|TYPE|Count,Size)
                      # Use read_header_from_socket. The initial_buffer contains data after the root folder header.
                      # The timeout should be reasonable, maybe standard header timeout.
                      header_prefix_segment, received_buffer = read_header_from_socket(
                          client_socket, gui_callbacks, cancel_transfer_event,
                          initial_buffer=received_buffer, timeout=config.DISCOVERY_TIMEOUT # Use discovery timeout as a reasonable limit per header
                      )

                      # Check for Protocol Prefix or Handshake Request
                      if header_prefix_segment.encode('utf-8').strip() == config.HANDSHAKE_REQUEST_SIGNAL.strip():
                           print("DEBUG: Received handshake request signal instead of TOTAL_INFO? Protocol violation.")
                           # If we receive handshake here, it means sender skipped TOTAL_INFO and items.
                           # Treat as incomplete/error and proceed to handshake state with verification_result=False.
                           utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[!] خطای پروتکل: درخواست تایید دریافت پوشه زودتر از پایان انتقال دریافت شد.")
                           # Set buffer back to include the received handshake signal
                           received_buffer = config.HANDSHAKE_REQUEST_SIGNAL + received_buffer # Prepend the signal bytes back
                           current_state = STATE_WAITING_FOR_HANDSHAKE_REQUEST # Transition to handshake state
                           # Note: total_expected_items/size are None. Verification will fail.
                           continue # Continue the main while loop to process handshake

                      if header_prefix_segment != config.FOLDER_PROTOCOL_PREFIX:
                           # If it's not the handshake signal AND not the protocol prefix, it's an error.
                           raise ValueError(f"Invalid folder protocol prefix received: '{header_prefix_segment}'. Expected '{config.FOLDER_PROTOCOL_PREFIX}' or handshake request.")


                      # Read the type segment
                      header_type_segment, received_buffer = read_header_from_socket(
                           client_socket, gui_callbacks, cancel_transfer_event,
                           initial_buffer=received_buffer
                      )

                      # If the type is TOTAL_INFO, process it
                      if header_type_segment == config.FOLDER_HEADER_TYPE_TOTAL_INFO:
                           print("DEBUG: Received TOTAL_INFO header type.")
                           # Read the data segment (Count,Size)
                           header_data_segment, received_buffer = read_header_from_socket(
                                client_socket, gui_callbacks, cancel_transfer_event,
                                initial_buffer=received_buffer
                           )
                           print(f"DEBUG: Received TOTAL_INFO data segment: '{header_data_segment}'. Remaining buffer size: {len(received_buffer)}")

                           # Parse count and size
                           try:
                                count_size_parts = header_data_segment.split(config.TOTAL_INFO_COUNT_SIZE_SEPARATOR)
                                if len(count_size_parts) == 2:
                                     parsed_count = int(count_size_parts[0])
                                     parsed_size = int(count_size_parts[1])
                                     if parsed_count < 0 or parsed_size < 0:
                                          raise ValueError("Negative count or size in TOTAL_INFO header.")

                                     total_expected_items = parsed_count
                                     total_expected_size = parsed_size
                                     print(f"DEBUG: Parsed Total Info: Items={total_expected_items}, Size={utils.format_bytes(total_expected_size)}")
                                     utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] اطلاعات کلی پوشه از فرستنده: {total_expected_items} آیتم، {utils.format_bytes(total_expected_size)}")

                                else:
                                     raise ValueError(f"Malformed TOTAL_INFO data: '{header_data_segment}'. Expected 'count,size'.")
                           except ValueError as e:
                                raise ValueError(f"Invalid count or size in TOTAL_INFO header: {e}. Data: '{header_data_segment}'")


                           current_state = STATE_WAITING_FOR_ITEM_HEADER # Transition to wait for item headers

                      # If the type is not TOTAL_INFO, but FOLDER, FILE, or END_TRANSFER,
                      # it means sender skipped TOTAL_INFO header.
                      elif header_type_segment in [config.FOLDER_HEADER_TYPE_FOLDER, config.FOLDER_HEADER_TYPE_FILE, config.FOLDER_HEADER_TYPE_END_TRANSFER]:
                           print(f"DEBUG: Received header type '{header_type_segment}' instead of TOTAL_INFO. Sender skipped TOTAL_INFO.")
                           utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[!] هشدار: هدر اطلاعات کلی پوشه (TOTAL_INFO) دریافت نشد. تأیید نهایی دقیق نخواهد بود.")
                           # The prefix and type segments were already read. We need to read the path segment now.
                           # Then we need to reconstruct the buffer containing prefix|type|path| and the remaining received_buffer.
                           # This logic is flawed here because header_path_segment is read *after* this elif.
                           # Let's refactor to read prefix, then type, then based on type, read remaining parts.

                           # Reworking the state logic slightly: Read prefix, then type. If type determines it's a valid item header (FOLDER, FILE, END_TRANSFER),
                           # read the path segment and then proceed based on the type. If type is TOTAL_INFO, read its data segment.

                           # Discard the already read type segment from this block's logic, the next read_header will get it again.
                           # Prepend the prefix and type back to the buffer for the next read_header call in WAITING_FOR_ITEM_HEADER state
                           received_buffer = header_prefix_segment.encode('utf-8') + config.HEADER_SEPARATOR.encode('utf-8') + \
                                             header_type_segment.encode('utf-8') + config.HEADER_SEPARATOR.encode('utf-8') + received_buffer # Add back prefix and type

                           current_state = STATE_WAITING_FOR_ITEM_HEADER # Transition to wait for item headers.
                           # Note: total_expected_items/size are None. Verification will fail unless handled.
                           # Let's set them to -1 to indicate they were missing.
                           total_expected_items = -1
                           total_expected_size = -1
                           continue # Continue to the next iteration of the main while loop to process the header in the new state

                      else: # Unknown header type instead of TOTAL_INFO
                           raise ValueError(f"Unknown folder protocol header type received instead of TOTAL_INFO: '{header_type_segment}'.")


                 # --- State: WAITING_FOR_ITEM_HEADER ---
                 # This state is entered after receiving TOTAL_INFO (or if TOTAL_INFO was skipped) or after finishing a file.
                 # It expects FOLDER, FILE, or END_TRANSFER headers.
                 elif current_state == STATE_WAITING_FOR_ITEM_HEADER:
                      # print("DEBUG: State: WAITING_FOR_ITEM_HEADER") # Too verbose

                      # Read data until a full header segment is found (PROTOCOL_PREFIX|TYPE|...)
                      # Use read_header_from_socket. The initial_buffer contains data after the previous header/file.
                      # The timeout should be reasonable, maybe standard header timeout.
                      header_prefix_segment, received_buffer = read_header_from_socket(
                           client_socket, gui_callbacks, cancel_transfer_event,
                           initial_buffer=received_buffer, timeout=config.DISCOVERY_TIMEOUT # Use discovery timeout
                      )

                      # Check for Protocol Prefix or Handshake Request
                      # If it's the handshake signal, transition to the handshake state.
                      if header_prefix_segment.encode('utf-8').strip() == config.HANDSHAKE_REQUEST_SIGNAL.strip():
                           print("DEBUG: Received handshake request signal.")
                           # We don't need to read type/path/size for handshake request here.
                           # The signal itself is the request.
                           # The remaining buffer is after the signal.
                           # Transition to the state where we send the handshake response.
                           current_state = STATE_WAITING_FOR_HANDSHAKE_REQUEST # Transition to handshake state
                           # The verification result (transfer_success) is already determined before END_TRANSFER.
                           # The actual sending of the response happens when we enter the next state block.
                           continue # Continue the main while loop to process handshake

                      if header_prefix_segment != config.FOLDER_PROTOCOL_PREFIX:
                           # If it's not the handshake signal AND not the protocol prefix, it's an error.
                           raise ValueError(f"Invalid folder protocol prefix received: '{header_prefix_segment}'. Expected '{config.FOLDER_PROTOCOL_PREFIX}' or handshake request.")


                      # Read the type segment (only if it was the protocol prefix)
                      header_type_segment, received_buffer = read_header_from_socket(
                           client_socket, gui_callbacks, cancel_transfer_event,
                           initial_buffer=received_buffer
                      )

                      # Handle item based on type
                      if header_type_segment == config.FOLDER_HEADER_TYPE_END_TRANSFER:
                           print("DEBUG: Received END_TRANSFER header.")
                           utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[+] پایان انتقال پوشه از فرستنده دریافت شد.")
                           close_current_file() # Close any currently open file
                           current_state = STATE_WAITING_FOR_HANDSHAKE_REQUEST # Transition to waiting for handshake
                           # Note: transfer_success flag will be set based on the Verification result, not just receiving END_TRANSFER

                           # --- Perform Count/Size Verification (new) ---
                           print("DEBUG: Performing Count/Size verification...")
                           verification_passed = False
                           # Note: total_expected_items/size might be -1 if TOTAL_INFO was skipped. Handle this case.
                           if (total_expected_items is None or total_expected_size is None or total_expected_items < 0 or total_expected_size < 0) or \
                              (items_received_count != total_expected_items or received_bytes_total != total_expected_size):
                               # TOTAL_INFO header was missing/invalid OR counts/sizes don't match
                               print("DEBUG: Verification failed: TOTAL_INFO header missing/invalid or counts/sizes do not match.")
                               utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[!] تأیید دریافت پوشه ناموفق: اطلاعات کلی پوشه از فرستنده دریافت نشد یا مطابقت ندارد.")
                               if total_expected_items is not None and total_expected_size is not None and total_expected_items >= 0 and total_expected_size >= 0: # Only show mismatch details if expected info was valid
                                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"    دریافتی: {items_received_count} آیتم، {utils.format_bytes(received_bytes_total)}")
                                    utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"    انتظار: {total_expected_items} آیتم، {utils.format_bytes(total_expected_size)}")
                               verification_passed = False
                           else:
                                # Counts/Sizes match
                                print("DEBUG: Verification passed: Item count and total size match.")
                                utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[+] تأیید دریافت پوشه موفقیت‌آمیز: تعداد آیتم‌ها و حجم کل مطابقت دارد.")
                                verification_passed = True

                           # Set the overall transfer_success flag based on verification
                           transfer_success = verification_passed

                           # The actual sending of the response happens when we enter the next state block.
                           # Just transition state here.
                           continue # Continue the main while loop to process handshake


                      # If it's a FOLDER or FILE header, we need to read the path segment next.
                      elif header_type_segment in [config.FOLDER_HEADER_TYPE_FOLDER, config.FOLDER_HEADER_TYPE_FILE]:
                          # Read the path segment for FOLDER or FILE
                          header_path_segment, received_buffer = read_header_from_socket(
                               client_socket, gui_callbacks, cancel_transfer_event,
                               initial_buffer=received_buffer
                          )

                          # Now we have the core parts: item_type, item_path
                          item_type = header_type_segment
                          item_path_raw = header_path_segment # Path as received from client (e.g., "rego/fdfg/")

                          # Update current_item_path_protocol here for status/error messages
                          current_item_path_protocol = item_path_raw
                          print(f"DEBUG: Parsed folder header parts: Type='{item_type}', Path='{current_item_path_protocol}'")

                          # Determine the path relative to the session's root folder name for sanitization
                          # This is the path AFTER the session_root_name/
                          normalized_item_path = os.path.normpath(current_item_path_protocol).replace('\\', '/')
                          # Need to handle cases where item_path_protocol IS the root folder name itself (protocol_root_path)
                          # and the sender might send the root name with or without a trailing slash.
                          # The very first header (root folder) is handled in STATE_WAITING_FOR_ROOT_FOLDER_HEADER.
                          # Any subsequent header for the root folder name itself (without subdirs) is likely redundant.
                          normalized_session_root_name_clean = os.path.normpath(session_root_name).replace('\\', '/').rstrip('/') # Get 'rego' not 'rego/'
                          normalized_session_root_path_protocol_style = normalized_session_root_name_clean + '/' # e.g. "rego/"

                          path_relative_to_session_root = ""
                          if normalized_item_path.startswith(normalized_session_root_path_protocol_style):
                               path_relative_to_session_root = normalized_item_path[len(normalized_session_root_path_protocol_style):]
                          elif normalized_item_path == normalized_session_root_name_clean:
                               # This case handles if the sender sends the root path without a trailing slash for an item header.
                               # It's technically malformed protocol if it's not the initial root header, but we can try to handle it.
                               print(f"WARNING: Received item path '{current_item_path_protocol}' matches session root name '{session_root_name}' without trailing slash. Treating as relative to root.", file=sys.stderr)
                               path_relative_to_session_root = "" # The relative path is empty, refers to the root itself

                          # If the path doesn't start with the session root name (and isn't the root name itself), it's a protocol error.
                          elif normalized_item_path != normalized_session_root_name_clean: # Already checked starts with root path or is root path
                               raise ValueError(f"Protocol error: Item path '{current_item_path_protocol}' does not start with session root '{session_root_name}/'.")

                          # The path_relative_to_session_root is now the path string *relative to* the session root folder name.
                          # E.g., if item_path_raw is "rego/subdir/file.txt" and session_root_name is "rego",
                          # path_relative_to_session_root is "subdir/file.txt".
                          # If item_path_raw is "rego/" and session_root_name is "rego", path_relative_to_session_root is "".

                          # Handle item based on type
                          if item_type == config.FOLDER_HEADER_TYPE_FOLDER:
                              print(f"DEBUG: Received FOLDER header for '{current_item_path_protocol}'")
                              # Ensure folder relative path ends with '/' for correct sanitization handling, UNLESS it's the root folder path itself.
                              # The root folder path ("rego/") resolves to "" as path_relative_to_session_root.
                              # So, we append '/' only if the path_relative_to_session_root is NOT empty AND doesn't already end with '/'.
                              path_for_sanitization = path_relative_to_session_root
                              if path_for_sanitization != "" and not path_for_sanitization.endswith('/'):
                                   print(f"WARNING: Folder path '{current_item_path_protocol}' relative part '{path_relative_to_session_root}' does not end with '/'. Appending for sanitization.", file=sys.stderr)
                                   # Append slash for sanitize_path if it's a subfolder path
                                   path_for_sanitization += '/'
                              # If path_relative_to_session_root is empty (""), path_for_sanitization remains "".
                              # sanitize_path("base", "") should resolve to "base".

                              utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] پردازش پوشه: '{current_item_path_protocol}'")

                              # Now sanitize the relative path and join it with the session base directory
                              try:
                                  # Pass the current_save_dir (e.g., received_folders/rego) as base,
                                  # and the path_for_sanitization (e.g., fdfg/ewss/ or "") as the relative path.
                                  sanitized_full_path = utils.sanitize_path(current_save_dir, path_for_sanitization)
                                  print(f"DEBUG: Sanitizing FOLDER path relative part '{path_for_sanitization}' against base '{current_save_dir}' -> Result: '{sanitized_full_path}'")

                                  # Check if the resulting path is the session root directory itself (current_save_dir).
                                  # If item_path_protocol was "rego/", path_relative_to_session_root is "".
                                  # sanitize_path(current_save_dir, "") should resolve to current_save_dir.
                                  # This is expected for the root folder header *if* it's received again (which shouldn't happen after the first state).
                                  # Let's disallow creating the root folder itself again in this state.
                                  if os.path.normpath(sanitized_full_path) == os.path.normpath(current_save_dir) and current_item_path_protocol.strip() != (session_root_name + '/').strip():
                                       # If the sanitized path resolved to the session root but the original path was NOT the root path (e.g., "../rego/"),
                                       # this is a path traversal attempt caught late.
                                       raise ValueError(f"Protocol error: Sanitized folder path '{sanitized_full_path}' resolved to session root unexpectedly for path '{current_item_path_protocol}'.")

                                  # Create the directory if it doesn't exist
                                  os.makedirs(sanitized_full_path, exist_ok=True)
                                  utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] پوشه ایجاد شد: '{current_item_path_protocol}'")

                                  # Increment item count for the folder header received and successfully processed
                                  items_received_count += 1
                                  print(f"DEBUG: Items received count after folder header '{current_item_path_protocol}': {items_received_count}")

                              except (ValueError, OSError, RuntimeError) as e:
                                  # Error during sanitization or directory creation
                                  raise ValueError(f"Error creating folder '{current_item_path_protocol}': {e}") from e


                          elif item_type == config.FOLDER_HEADER_TYPE_FILE:
                              print(f"DEBUG: Received FILE header for '{current_item_path_protocol}'")
                              # Ensure file relative path does NOT end with '/'
                              path_for_sanitization = path_relative_to_session_root
                              if path_for_sanitization.endswith('/'):
                                   print(f"WARNING: File path '{current_item_path_protocol}' relative part '{path_relative_to_session_root}' ends with '/', which is unusual for a file. Removing trailing slash.", file=sys.stderr)
                                   path_for_sanitization = path_relative_to_session_root.rstrip('/')
                              # Ensure file relative path is not empty after stripping slash
                              if not path_for_sanitization:
                                   raise ValueError(f"Protocol error: Relative file path resolved to empty for '{current_item_path_protocol}'.")


                              header_size_segment, received_buffer = read_header_from_socket(client_socket, gui_callbacks, cancel_transfer_event, initial_buffer=received_buffer)
                              print(f"DEBUG: Received size segment: '{header_size_segment}'. Remaining buffer size: {len(received_buffer)}")

                              try:
                                   item_size = int(header_size_segment)
                                   if item_size < 0: raise ValueError(f"Negative size in file header: {item_size}. Path: '{current_item_path_protocol}'")
                                   # Keep sanity check on file size
                                   # This check is debatable if it should abort the whole transfer vs just skip the file.
                                   # Let's keep it as a hard error for now as it might indicate a major protocol issue or malicious data.
                                   if item_size > config.TEST_FILE_SIZE * 10000: # Example: 10000 times the test file size
                                        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] هشدار: اندازه فایل اعلام شده ({utils.format_bytes(item_size)}) برای '{current_item_path_protocol}' بسیار بزرگ است. ممکن است خطا باشد.")
                                        print(f"WARNING: Declared file size {item_size} seems excessively large for '{current_item_path_protocol}'. Aborting receive for this file.", file=sys.stderr)
                                        raise ValueError(f"Declared file size ({item_size}) is excessively large for '{current_item_path_protocol}'. Aborting transfer.")
                                   # Also add check for 0-byte files, ensure they are handled correctly
                                   if item_size == 0:
                                        print(f"DEBUG: Received 0-byte file header for '{current_item_path_protocol}'. Will create empty file.")

                              except ValueError as e:
                                   raise ValueError(f"Invalid size in file header: '{header_size_segment}'. Path: '{current_item_path_protocol}'. Error: {e}")


                              current_file_size = item_size
                              current_file_bytes_received = 0
                              current_file_bytes_expected = item_size

                              # Now sanitize the relative path and join it with the session base directory
                              try:
                                  # Pass the current_save_dir (e.g., received_folders/rego) as base,
                                  # and the path_for_sanitization (e.g., subdir/file.txt) as the relative path.
                                  sanitized_full_path = utils.sanitize_path(current_save_dir, path_for_sanitization)
                                  print(f"DEBUG: Sanitizing FILE path relative part '{path_for_sanitization}' against base '{current_save_dir}' -> Result: '{sanitized_full_path}'")

                                  # Check if the resulting path is the session root directory itself (current_save_dir).
                                  if os.path.normpath(sanitized_full_path) == os.path.normpath(current_save_dir):
                                      # A file path should never resolve to the root directory itself.
                                      raise ValueError(f"Protocol error: Sanitized file path '{sanitized_full_path}' resolved to session root unexpectedly for path '{current_item_path_protocol}'.")

                                  # Ensure parent directory exists for the file
                                  parent_dir = os.path.dirname(sanitized_full_path)
                                  if parent_dir and not os.path.exists(parent_dir):
                                      # Use exists_ok=True just in case a previous file creation failed after making the parent dir
                                      os.makedirs(parent_dir, exist_ok=True)
                                      print(f"DEBUG: Created parent directory for file: {parent_dir}")

                                  # Handle potential filename conflicts (optional but good practice for files)
                                  final_file_path = sanitized_full_path # Start with the sanitized path
                                  # Check if it points to an existing directory (protocol error?)
                                  if os.path.isdir(final_file_path):
                                       raise ValueError(f"Sanitized file path '{final_file_path}' points to an existing directory.")

                                  # Append counter if a file with the exact name already exists
                                  if os.path.exists(final_file_path):
                                      base, ext = os.path.splitext(final_file_path)
                                      counter = 1
                                      original_base = base # Store original base for appending counter
                                      while os.path.exists(final_file_path):
                                          final_file_path = f"{original_base}_{counter}{ext}"
                                          counter += 1
                                          if counter > 10000: # Avoid infinite loop with too many duplicates
                                               raise ValueError(f"Exceeded attempts to find unique filename for {os.path.basename(sanitized_full_path)}")
                                      print(f"DEBUG: File '{sanitized_full_path}' already exists, saving as '{final_file_path}'")
                                      utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] هشدار: فایل '{os.path.basename(sanitized_full_path)}' قبلاً موجود بود. با نام '{os.path.basename(final_file_path)}' ذخیره می‌شود.")


                                  current_file_path = final_file_path # Set the final path for the file
                                  close_current_file() # Close any previous file handle
                                  # Ensure the file doesn't exist as a directory
                                  if os.path.isdir(current_file_path):
                                      raise ValueError(f"Cannot open file '{current_file_path}': A directory with the same name exists.")

                                  # Handle 0-byte files: create the file and immediately close it, then transition state.
                                  if current_file_size == 0:
                                       try:
                                            with open(current_file_path, "wb") as f:
                                                 pass # Just create an empty file
                                            print(f"DEBUG: Empty file '{current_file_path}' created.")
                                            # Update total received bytes and item count for the 0-byte file
                                            # No bytes are received for 0-byte files in the data loop
                                            # The item count is incremented below.
                                            items_received_count += 1 # Count the sent file header
                                            print(f"DEBUG: Items received count after 0-byte file header '{current_item_path_protocol}': {items_received_count}")
                                            # Transition state back to waiting for the next header immediately
                                            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[+] دریافت فایل خالی کامل شد: '{current_item_path_protocol}'")
                                            current_state = STATE_WAITING_FOR_ITEM_HEADER
                                            continue # Continue the main while loop to get the next header

                                       except Exception as e:
                                            # Error creating empty file
                                            print(f"DEBUG: Error creating empty file '{current_file_path}': {e}", file=sys.stderr)
                                            raise Exception(f"Error creating empty file '{current_item_path_protocol}': {e}") from e

                                  # If file size > 0, open the file handle and proceed to receive data state.
                                  else:
                                       current_file_handle = open(current_file_path, "wb")
                                       utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[*] در حال دریافت فایل: '{current_item_path_protocol}' ({utils.format_bytes(current_file_size)})...")
                                       current_state = STATE_RECEIVING_FILE_DATA # Transition state
                                       # Increment item count for the file header received and successfully processed
                                       items_received_count += 1
                                       print(f"DEBUG: Items received count after file header '{current_item_path_protocol}': {items_received_count}")
                                       # Continue the main while loop to process file data in the next iteration
                                       # The next iteration will find current_state == STATE_RECEIVING_FILE_DATA and enter that block.
                                       continue # Explicitly continue to the next loop iteration after handling file header and size


                              except (ValueError, OSError, RuntimeError) as e: # Catch errors during sanitization, directory creation, file opening, or naming conflict
                                  # Error during sanitization, directory creation, file opening, or naming conflict
                                  raise ValueError(f"Error preparing to receive file '{current_item_path_protocol}': {e}") from e


                          else:
                              # This case is already handled by the type check at the start of WAITING_FOR_ITEM_HEADER.
                              # It should not be reached. Add a safeguard raise just in case.
                              raise ValueError(f"Internal error: Received unexpected header type '{item_type}' in WAITING_FOR_ITEM_HEADER state.")


                 # --- State: RECEIVING_FILE_DATA ---
                 # This block executes only if current_state is STATE_RECEIVING_FILE_DATA
                 # and no exception occurred during header processing (if state was WAITING_FOR_ITEM_HEADER previously).
                 # Added an inner while loop here to ensure the entire file is received before changing state.
                 elif current_state == STATE_RECEIVING_FILE_DATA and current_file_handle:
                      # print(f"DEBUG: State: RECEIVING_FILE_DATA for '{current_item_path_protocol}' (Received: {current_file_bytes_received}/{current_file_bytes_expected})") # Too verbose

                      # --- Inner loop to receive data for the current file ---
                      # This loop continues until the current file is complete or cancellation occurs.
                      # Only way to exit naturally is when current_file_bytes_received == current_file_bytes_expected.
                      # Any break/return/exception inside this loop should lead to the outer try's except/finally.
                      while current_file_bytes_received < current_file_bytes_expected:
                          if cancel_transfer_event.is_set():
                              print("DEBUG: Cancel event set during file data receive loop.")
                              # No need to raise CancelledError here. The main while loop checks cancel_transfer_event
                              # and will exit in the next iteration. Breaking the inner loop is enough.
                              is_cancelled = True # Ensure cancelled flag is set
                              break # Exit the 'while current_file_bytes_received < current_file_bytes_expected:' loop

                          bytes_needed_for_file = current_file_bytes_expected - current_file_bytes_received
                          bytes_available_in_buffer = len(received_buffer)

                          # 1. Process data available in the received_buffer first
                          if bytes_available_in_buffer > 0:
                              bytes_to_process_from_buffer = min(bytes_available_in_buffer, bytes_needed_for_file)
                              if bytes_to_process_from_buffer > 0:
                                   try:
                                       current_file_handle.write(received_buffer[:bytes_to_process_from_buffer])
                                       # print(f"DEBUG: Wrote {bytes_to_process_from_buffer} bytes from buffer for '{current_item_path_protocol}'.") # Too verbose
                                   except Exception as e:
                                       # Handle file writing errors
                                       print(f"DEBUG: Error writing data from buffer to file '{current_item_path_protocol}': {e}", file=sys.stderr)
                                       # If file writing fails, it's a critical error for this file/transfer.
                                       # Raise exception to be caught by the inner try's except block.
                                       raise Exception(f"Error writing data from buffer to file '{current_item_path_protocol}': {e}") from e

                                   current_file_bytes_received += bytes_to_process_from_buffer
                                   received_bytes_total += bytes_to_process_from_buffer
                                   received_buffer = received_buffer[bytes_to_process_from_buffer:] # Consume from buffer
                                   # print(f"DEBUG: After buffer processing: File received: {current_file_bytes_received}/{current_file_bytes_expected}. Total received: {received_bytes_total}. Remaining buffer: {len(received_buffer)}") # Too verbose

                              # If after processing buffer, the file is complete, exit the inner data reception loop.
                              if current_file_bytes_received == current_file_bytes_expected:
                                  print(f"DEBUG: File '{current_item_path_protocol}' fully received after buffer processing.")
                                  break # Exit the 'while current_file_bytes_received < current_file_bytes_expected:' loop

                          # 2. If file is not yet complete and data is not available in the buffer, read from the socket.
                          # Only attempt to read from socket if buffer is empty or processed, and file needs more data.
                          if current_file_bytes_received < current_file_bytes_expected: # Redundant check, but clear
                              # Determine how many bytes to attempt to read from the socket.
                              # Use the configured receiver buffer size, limited by bytes needed for the file.
                              bytes_to_read_from_socket = min(receive_buffer_size, bytes_needed_for_file)
                              bytes_to_read_from_socket = max(0, bytes_to_read_from_socket) # Ensure non-negative

                              # If file needs more data AND we want to read (>0 bytes)
                              if bytes_to_read_from_socket > 0:
                                  # print(f"DEBUG: Attempting to read {bytes_to_read_now} bytes from socket...") # Too verbose
                                  try:
                                      # Set a short timeout to allow checking the cancel event while waiting for data
                                      # Use the specific data transfer timeout if defined, otherwise use cancel check interval.
                                      recv_timeout = getattr(config, 'DATA_TRANSFER_TIMEOUT', config.CANCEL_CHECK_INTERVAL) # Use the new constant
                                      client_socket.settimeout(recv_timeout)
                                      chunk = client_socket.recv(bytes_to_read_from_socket) # Use the calculated size to read
                                      client_socket.settimeout(None) # Remove timeout after successful read

                                  except socket.timeout:
                                      # Expected timeout, just continue the inner loop to check cancel and try reading again.
                                      # print("DEBUG: Socket read timed out, trying again.") # Verbose
                                      continue # Go back to the start of the inner 'while current_file_bytes_received < current_file_bytes_expected:' loop

                                  except Exception as e:
                                      # Handle socket reading errors
                                      print(f"DEBUG: Error reading data from socket for file '{current_item_path_protocol}': {e}", file=sys.stderr)
                                      # If socket reading fails, it's a critical error for this file/transfer.
                                      # Raise exception to be caught by the inner try's except block.
                                      raise Exception(f"Error reading data from socket for file '{current_item_path_protocol}': {e}") from e

                                  # If chunk is empty, it means the peer closed the connection unexpectedly
                                  if not chunk:
                                      # If connection closed before all expected bytes are received
                                      if current_file_bytes_received < current_file_bytes_expected:
                                            print(f"DEBUG: Connection lost during data receive for '{current_item_path_protocol}'. Received {current_file_bytes_received}/{current_file_bytes_expected}", file=sys.stderr)
                                            raise ConnectionResetError(f"Connection closed by peer during file data receive for '{current_item_path_protocol}'")
                                      else: # Received 0 bytes but all expected bytes already received (e.g., filesize was 0 or just finished)
                                            break # Exit loop cleanly if no data expected or all data received


                                  # Add the received chunk to the buffer to be processed in the next iteration of the *inner* while loop
                                  received_buffer += chunk
                                  # print(f"DEBUG: Read {len(chunk)} bytes from socket. Received buffer size now: {len(received_buffer)}") # Too verbose
                                  # Continue the inner while loop to process the new data added to the buffer

                              # If file needs more data, buffer IS empty, and bytes_to_read_from_socket was 0 (e.g. buffer limit reached or no space),
                              # this is an issue. The logic above tries to prevent this by ensuring bytes_to_read_from_socket > 0 if needed.
                              # If it somehow gets stuck here, the socket timeout or outer try timeout might eventually catch it.


                      # The inner while loop finishes here.
                      # If it exited because current_file_bytes_received == current_file_bytes_expected, the file is done.
                      # If it exited because of 'break' (due to cancel) or an exception, the file is NOT done.

                      # The check for file completion and state change happens AFTER this inner while loop.


                      # --- After inner loop to receive data for the current file ---
                      # If the inner while loop completed without raising an exception:
                      if current_file_bytes_received == current_file_bytes_expected:
                          # Current file is fully received!
                          print(f"DEBUG: File '{current_item_path_protocol}' fully received.")
                          close_current_file() # Close the completed file handle

                          utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[+] دریافت فایل کامل شد: '{current_item_path_protocol}'")
                          current_state = STATE_WAITING_FOR_ITEM_HEADER # Transition state back to waiting for the next header
                          # Continue the main while loop to process the next item/header in the next iteration.
                          # No need for 'continue' here, the flow naturally goes to the end of the outer try block
                          # and then the next iteration of the main while loop starts with the new state.

                      # else: If the inner while loop exited for any other reason (like 'break' due to cancel_transfer_event, or an exception),
                      # the current state remains RECEIVING_FILE_DATA (unless an exception is raised and caught).
                      # The outer while loop condition `while not cancel_transfer_event.is_set() and current_state != STATE_HANDSHAKE_COMPLETE:`
                      # will handle exiting if cancel_transfer_event is set.


                 # --- State: WAITING_FOR_HANDSHAKE_REQUEST ---
                 # This state is entered after receiving the END_TRANSFER header OR receiving the HANDSHAKE_REQUEST_SIGNAL.
                 # We now call the external handshake function to send the response.
                 elif current_state == STATE_WAITING_FOR_HANDSHAKE_REQUEST:
                     print("DEBUG: State: WAITING_FOR_HANDSHAKE_REQUEST (Ready to send response)")
                     # The handshake logic is now handled by the separate function (sending the response).
                     # Call the server-side handshake function, passing the verification result (transfer_success flag).
                     # The handshake function will handle sending the response based on verification_result.
                     # Pass the socket object correctly to the handshake function
                     perform_folder_handshake_server(
                         client_socket, # Pass the client_socket here
                         cancel_transfer_event, # Pass for consistency, but server handshake only sends
                         gui_callbacks,         # Pass all GUI callbacks
                         transfer_success       # Pass the result of verification
                     )
                     # After the handshake function returns (response sent or failed to send),
                     # we transition to the final state.
                     current_state = STATE_HANDSHAKE_COMPLETE
                     # The main while loop condition will now be false, and it will exit.
                     # No continue needed, as the loop will naturally end.


                 # No 'else' for states...


             # --- Inner Exception Handling for the while loop try block ---
             # This block catches specific errors that occur *within* the main while loop's try block.
             except (socket.timeout, ConnectionResetError, ValueError, CancelledError, OSError, RuntimeError, Exception) as e: # Catch general Exception too for robustness
                  # Catch specific expected errors from reading headers or data chunks in the loop
                  # Close any open file handle before raising the exception to the outer except block.
                  close_current_file()
                  # Set is_cancelled here if it wasn't set by CancelledError already
                  if not isinstance(e, CancelledError):
                       is_cancelled = True
                  # Raise the exception to be caught by the OUTER except block of the handler.
                  raise e # Re-raise the caught exception


             # --- Code that runs at the end of each successful loop iteration (outside inner try/except) ---
             # This code runs if the inner try block completed without raising an exception.
             # Update GUI (Speed) regardless of state, after potential reads/writes
             current_time = time.time()
             if current_time - last_update_time >= config.SPEED_UPDATE_INTERVAL:
                 time_delta = current_time - last_update_time
                 bytes_since_last_update = received_bytes_total - last_update_bytes
                 speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                 speed_string = utils.format_bytes_per_second(speed_bps)
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], f"سرعت دانلود پوشه: {speed_string}")

                 last_update_time = current_time
                 last_update_bytes = received_bytes_total

                 # Progress update: Disabled for folder receive as total size is unknown UNTIL TOTAL_INFO.
                 # If total_expected_size is known, we can update progress.
                 # Cap progress at 99.99 until END_TRANSFER to match sender logic
                 if total_expected_size is not None and total_expected_size > 0 and total_expected_size >= received_bytes_total:
                     progress = (received_bytes_total / total_expected_size) * 100
                     utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], min(progress, 99.99))
                 # If total_expected_size is 0 or None or less than received (error?), progress stays 0 or old value.


        # --- Main while loop finished ---
        # This code runs when the while loop condition becomes false (either cancel_event is set OR current_state is STATE_HANDSHAKE_COMPLETE)
        print(f"DEBUG: Folder transfer main loop exited. is_cancelled: {is_cancelled}, current_state: {current_state}")

        # If loop exited due to cancel_transfer_event being set, ensure is_cancelled flag reflects it.
        if cancel_transfer_event.is_set():
             is_cancelled = True
             transfer_success = False # Cancellation means not successful
             print("DEBUG: Loop exited due to cancellation event.")

        elif current_state == STATE_HANDSHAKE_COMPLETE:
             print("DEBUG: Loop exited due to handshake completion.")
             # transfer_success flag was set by the handshake logic

        else:
             # Loop exited for an unexpected reason (should not happen if logic is correct)
             print(f"DEBUG: Folder transfer main loop exited for unexpected reason. Final state: {current_state}", file=sys.stderr)
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[!] انتقال پوشه به پایان رسید اما وضعیت نامشخص است.")
             transfer_success = False # Treat as failed


    # --- Outer Exception Handling ---
    # This block catches exceptions that were re-raised from the inner try block,
    # or occurred during the initial setup phase (if not caught by the first except).
    # Added general Exception catch here as well.
    except (socket.timeout, ConnectionResetError, ValueError, CancelledError, OSError, RuntimeError, Exception) as e:
        # Close any open file handle in case of error before final cleanup.
        close_current_file()

        # Set is_cancelled here if it wasn't set by CancelledError caught by inner except
        if not isinstance(e, CancelledError) and not is_cancelled:
             is_cancelled = True

        # Report error if not cancelled by user directly
        # Ensure client_socket is checked if used here, but it shouldn't be necessary in this block.
        if not (isinstance(e, CancelledError) and cancel_transfer_event.is_set()): # Avoid double error report for user cancel
             msg = f"[!] خطا در حین انتقال پوشه با {address}: {e}"
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], msg)
             # Include current item path in error message if available
             error_details = f"خطا در حین انتقال پوشه از فرستنده ({address}):\n{e}"
             if current_item_path_protocol != "N/A":
                  error_details += f"\nدر حین پردازش آیتم: '{current_item_path_protocol}'"
             utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای دریافت پوشه", error_details)

             # The NameError 'name 'server_socket' is not defined' was reported here in previous logs.
             # This block does NOT use 'server_socket'.
             # The error MUST have originated from *within* the call to perform_folder_handshake_server
             # and then been caught by this outer except block.
             # The 'Current item: ...' part of the debug log comes from *after* the exception message,
             # suggesting the exception happened during processing that item (which was the handshake call).
             print(f"DEBUG: Specific error re-caught in outer except for {address}: {e}. Current item: '{current_item_path_protocol}'", file=sys.stderr)

        transfer_success = False # Not successful on error

    finally: # This finally block runs after the outer try/except blocks finish
        print(f"DEBUG: handle_client_folder_transfer outer finally block entered for {address}")
        # Ensure any open file handle is closed (double check)
        close_current_file() # Use the helper function

        # Attempt to clean up incomplete file only if transfer failed/cancelled AND file path was created AND file exists AND it wasn't a 0-byte file
        # Only remove if transfer_success is False (meaning it was cancelled or failed) AND current_file_bytes_expected > 0
        # The path should only be cleaned up if it was for the file that was being received when error/cancel happened.
        # We only remove if the error happened *during* receiving file data, not during handshake etc.
        # Check if the state was RECEIVING_FILE_DATA when the error occurred, OR if the file was simply incomplete.
        if not transfer_success and current_file_path and os.path.exists(current_file_path) and current_file_bytes_expected > 0 and current_file_bytes_received < current_file_bytes_expected: # Check if file was incomplete and expected bytes > 0
             try:
                  # Add a small delay to allow file system operations to complete
                  time.sleep(0.01) # Give OS a moment
                  os.remove(current_file_path)
                  # Use protocol item path in status message for consistency
                  utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] فایل ناقص '{current_item_path_protocol}' حذف شد.")
                  print(f"DEBUG: Incomplete file '{current_file_path}' removed.")
             except Exception as e:
                  # Use protocol item path in status message
                  utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطا در حذف فایل ناقص '{current_item_path_protocol}': {e}")
                  print(f"DEBUG: Error removing incomplete file '{current_file_path}': {e}", file=sys.stderr)


        # Ensure the client socket is closed.
        # The main server accept loop might also close the socket, but closing it here
        # ensures it's closed by the handler thread that used it.
        if client_socket: # Check if client_socket was successfully accepted/defined
            try:
                # Shutdown socket gracefully before closing if possible.
                try: client_socket.shutdown(socket.SHUT_RDWR)
                except OSError as e:
                     if e.errno not in (107, 10057): # Ignore common errors if socket is already partly closed or reset
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
        # The handshake function handles sending the final response and most status updates.
        # This final block just signals the GUI state change.

        # Reset GUI elements related to transfer state (Progress bar, Speed display)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_progress'], 0) # Reset progress bar
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: N/A - Transfer Finished") # Reset speed display
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[-] هندلر اتصال پوشه با {address} پایان یافت.")

        # Signal GUI that the transfer is finished (resets is_transfer_active flag in GUI)
        # This is crucial for allowing the server to accept new connections or enabling other GUI actions.
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['on_transfer_finished'])
        print(f"DEBUG: handle_client_folder_transfer finished for {address}")