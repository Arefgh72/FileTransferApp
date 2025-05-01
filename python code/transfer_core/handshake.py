# transfer_core/handshake.py - Logic for the folder transfer handshake process (Count/Size Verification)

import socket
import time
import sys # For file=sys.stderr in prints
import threading # Used for accessing threading events

# Import config, utils, and helpers using relative imports within the package structure
import config
import utils
from .helpers import CancelledError # Import the custom exception


# --- Handshake Functions (Run within client or handler threads) ---

def perform_folder_handshake_client(client_socket, cancel_transfer_event, gui_callbacks):
    """
    Performs the client-side (sender) part of the folder transfer handshake.
    Sends the handshake request and waits for the server's verification response.

    Args:
        client_socket (socket.socket): The socket connected to the server.
        cancel_transfer_event (threading.Event): Event to check for cancellation.
        gui_callbacks (dict): Dictionary of GUI callbacks.

    Returns:
        bool: True if handshake was successful (server responded OK), False otherwise.
    """
    print("DEBUG: Starting Client Handshake phase.")
    handshake_successful = False
    response_buffer = b"" # Initialize response buffer before the try block

    try:
        # Step 1: Send Handshake Request signal to receiver
        print("DEBUG: Sending Handshake Request signal.")
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] ارسال درخواست تایید دریافت پوشه...")
        client_socket.sendall(config.HANDSHAKE_REQUEST_SIGNAL)
        # Note: sendall can still block, but using the handshake timeout below covers the total time.

        # Step 2: Wait for Handshake Response from receiver
        print("DEBUG: Waiting for Handshake Response from receiver.")
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_speed'], "Speed: Waiting for Confirmation...") # Update speed status

        # Use the Handshake Timeout for waiting for the response
        start_time = time.time()

        while time.time() - start_time < config.HANDSHAKE_TIMEOUT:
             if cancel_transfer_event.is_set():
                  print("DEBUG: Client Handshake cancelled by user while waiting for response.")
                  utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[*] تایید دریافت پوشه توسط کاربر لغو شد.")
                  raise CancelledError("Handshake cancelled by user.")

             try:
                  # Set a short timeout for recv to allow checking the cancel event periodically
                  # Adjust timeout based on remaining time
                  elapsed_time = time.time() - start_time
                  remaining_timeout = config.HANDSHAKE_TIMEOUT - elapsed_time
                  if remaining_timeout <= 0: # Ensure timeout is not negative or zero for recv()
                       # If remaining time is zero or less, the main loop condition will handle timeout.
                       # Avoid blocking recv with a zero/negative timeout. Break or set minimal timeout.
                       # Setting a minimal timeout or breaking here depending on preference.
                       # Let's rely on the main while loop condition for the overall timeout.
                       # Set a very small timeout for recv to just non-block enough to check cancel event.
                       recv_timeout_current = min(remaining_timeout, config.CANCEL_CHECK_INTERVAL) # Use CANCEL_CHECK_INTERVAL or less
                       if recv_timeout_current <= 0: recv_timeout_current = 0.001 # Smallest positive timeout
                  else:
                      recv_timeout_current = min(remaining_timeout, config.CANCEL_CHECK_INTERVAL) # Check more often than overall timeout


                  client_socket.settimeout(recv_timeout_current)

                  # Use HANDSHAKE_READ_BUFFER_SIZE constant
                  chunk = client_socket.recv(config.HANDSHAKE_READ_BUFFER_SIZE)

                  # Remove timeout after successful read (important if recv_timeout_current was set)
                  # client_socket.settimeout(None) # Better to let the loop manage timeouts

             except socket.timeout:
                 # Expected timeout, continue loop to check cancel event and overall timeout
                 continue
             except Exception as e:
                  # Handle other socket errors during receive
                  print(f"DEBUG: Error receiving handshake response chunk: {e}", file=sys.stderr)
                  raise Exception(f"Error receiving handshake response: {e}") # Re-raise

             if not chunk:
                 # Connection closed by peer before sending response
                 print("DEBUG: Connection closed by peer while waiting for handshake response.", file=sys.stderr)
                 raise ConnectionResetError("Connection closed by peer while waiting for handshake response.")

             response_buffer += chunk

             # Check if either response signal is in the buffer. We expect one of them.
             if config.HANDSHAKE_COMPLETE_OK_SIGNAL in response_buffer:
                 print("DEBUG: Received Handshake COMPLETE_OK.")
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[+] دریافت پوشه توسط گیرنده تایید شد.")
                 utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_info'], "موفقیت انتقال پوشه", "انتقال پوشه با موفقیت به پایان رسید و توسط گیرنده تایید شد.")
                 handshake_successful = True
                 # We can break immediately once the expected signal is found,
                 # regardless of any trailing data (which shouldn't be there)
                 # Consume the signal and any trailing data (which shouldn't be there)
                 # signal_index = response_buffer.find(config.HANDSHAKE_COMPLETE_OK_SIGNAL)
                 # response_buffer = response_buffer[signal_index + len(config.HANDSHAKE_COMPLETE_OK_SIGNAL):]
                 break # Exit receive loop

             elif config.HANDSHAKE_ERROR_SIGNAL in response_buffer:
                  print("DEBUG: Received Handshake ERROR SIGNAL.", file=sys.stderr)
                  utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[!] گیرنده خطایی در حین دریافت پوشه گزارش کرد.")
                  utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_warning'], "خطا در گیرنده", "گیرنده خطایی در حین دریافت پوشه گزارش کرد. لطفا لاگ گیرنده را بررسی کنید.")
                  handshake_successful = False # Mark as failed due to receiver error
                  # Consume the signal and any trailing data
                  # signal_index = response_buffer.find(config.HANDSHAKE_ERROR_SIGNAL)
                  # response_buffer = response_buffer[signal_index + len(config.HANDSHAKE_ERROR_SIGNAL):]
                  break # Exit receive loop

             # If response buffer is getting excessively large without a recognized signal,
             # treat as a protocol error.
             # Check buffer size after adding chunk.
             if len(response_buffer) > config.BUFFER_SIZE_FOR_HEADER * 2: # Safety limit
                  raise ValueError("Received excessively large or malformed handshake response.")


        # Check if loop finished due to overall timeout without finding a valid response AND wasn't cancelled
        # This happens if the loop condition (time.time() - start_time < config.HANDSHAKE_TIMEOUT) became false.
        if not cancel_transfer_event.is_set() and not handshake_successful:
             print("DEBUG: Client Handshake timeout waiting for response.")
             # If timeout occurred, the handshake is not successful. Raise timeout error.
             raise socket.timeout("Timeout waiting for handshake response from server.")


    except CancelledError:
        # User cancelled, already handled status message within the loop or before entering.
        handshake_successful = False
    except (socket.timeout, ConnectionResetError, ValueError, Exception) as e:
        # Catch specific handshake errors or other exceptions during the process (excluding user CancelledError).
        # Log the error and report to GUI.
        print(f"DEBUG: Error during Client Handshake: {e}", file=sys.stderr)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطا در حین تایید دریافت پوشه (handshake): {e}")
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای تایید دریافت", f"خطا در حین تایید دریافت پوشه (handshake) با گیرنده:\n{e}")
        handshake_successful = False # Mark as failed on any caught exception

    finally:
        # Ensure socket timeout is reset to None when the function exits.
        # This prevents potential issues if the socket is reused or the thread joins later.
        # Check if socket was successfully created and passed as argument.
        if client_socket: # Check if socket was successfully created
            try: client_socket.settimeout(None)
            except Exception: pass # Ignore errors if socket is already closed or invalid

        print("DEBUG: Client Handshake phase finished.")
        return handshake_successful


def perform_folder_handshake_server(server_socket, cancel_transfer_event, gui_callbacks, verification_result):
    """
    Performs the server-side (receiver) part of the folder transfer handshake.
    Sends the verification response after the handshake request is received (handled by caller).

    Args:
        server_socket (socket.socket): The socket connected to the client.
        cancel_transfer_event (threading.Event): Event to check for cancellation.
        gui_callbacks (dict): Dictionary of GUI callbacks.
        verification_result (bool): The result of the count/size verification done by folder_handler.
                                    True if verification passed, False otherwise.

    Returns:
        None
    """
    print("DEBUG: Starting Server Handshake phase (Send Response).") # Updated debug message
    # response_sent flag is not needed ...

    # No need to initialize request_buffer here or wait for request.
    # The caller (handle_client_folder_transfer) is responsible for receiving the request signal.

    try:
        # Step 1: Send Handshake Response based on verification_result
        # This block will only run if the caller received the request and passed verification_result.
        print(f"DEBUG: Verification result is {verification_result}. Sending response.")
        try:
            if verification_result:
                utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[+] ارسال پاسخ موفقیت آمیز بودن دریافت پوشه.")
                server_socket.sendall(config.HANDSHAKE_COMPLETE_OK_SIGNAL)
                print("DEBUG: Sent TRANSFER_COMPLETE_OK signal.")
            else:
                utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], "[!] ارسال پاسخ خطا در دریافت پوشه.")
                server_socket.sendall(config.HANDSHAKE_ERROR_SIGNAL)
                print("DEBUG: Sent TRANSFER_ERROR signal.")
            # response_sent = True # No need for this flag within this function, just send and exit or catch error

        except Exception as e:
            # If sending the response fails, it's an error at the very end.
            print(f"DEBUG: Error sending handshake response: {e}", file=sys.stderr)
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطا در ارسال پاسخ handshake: {e}")
            utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_warning'], "هشدار دریافت", "خطا در ارسال پاسخ تکمیل دریافت پوشه.")
            # Do NOT re-raise here. Handshake failed at the very end, the main handler will proceed to cleanup.


        # If we reached here, the handshake attempt completed (either response sent or send failed and caught).
        # Mark handshake as completed (successfully or with error response attempt).
        # The overall success of the *transfer* depends on verification_result, which is passed to the caller.
        # handshake_completed = True # This flag is not strictly needed anymore as the function is about to return


    except CancelledError:
        # User cancelled, already handled status message.
        # The function will return None implicitly or we can return False if we change signature.
        # For now, just let it proceed to finally (although there is no finally block currently).
        pass # Exit the try block due to cancellation

    except (socket.timeout, ConnectionResetError, ValueError, Exception) as e:
        # Catch specific handshake errors or other exceptions that occur *before* sending the response.
        # This should ideally not happen anymore since the function only sends.
        # But as a safeguard:
        print(f"DEBUG: Unexpected error during Server Handshake (Send Response phase): {e}", file=sys.stderr)
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['update_status'], f"[!] خطای غیرمنتظره در حین تایید دریافت پوشه (ارسال پاسخ): {e}")
        utils.safe_gui_update(gui_callbacks['root'], gui_callbacks['show_error'], "خطای تایید دریافت", f"خطای غیرمنتظره در حین تایید دریافت پوشه (ارسال پاسخ) با فرستنده:\n{e}")

        # Attempt to send an error handshake response as a last resort if an error occurred *before* sending a response.
        # This block runs *because* an exception happened in the main try block.
        # We only attempt to send error if the exception wasn't CancelledError due to user action.
        if not isinstance(e, CancelledError):
             try:
                  print("DEBUG: Attempting to send TRANSFER_ERROR signal after unexpected error in response phase.")
                  # Check if the socket is still apparently open before attempting to send
                  # The server_socket parameter is available here.
                  server_socket.sendall(config.HANDSHAKE_ERROR_SIGNAL)
                  print("DEBUG: TRANSFER_ERROR signal sent successfully after error.")
             except Exception as send_e:
                  # Ignore errors if the socket is already closed or broken during this last attempt.
                  print(f"DEBUG: Error sending TRANSFER_ERROR signal after unexpected error: {send_e}", file=sys.stderr)
                  pass

        # The exception 'e' that triggered this block is *not* re-raised.
        # The caller (handle_client_folder_transfer) will continue from where it called handshake.
        # Its outer except block will catch the exception if it was re-raised, but here it's handled.
        # The handle_client_folder_transfer will proceed to its finally block.


    finally: # This block runs when the function finishes (either normally or due to exception caught above)
        # Ensure socket timeout is reset to None when the function exits.
        # This prevents potential issues if the socket is reused or the thread joins later.
        # Check if socket was successfully created AND passed as argument.
        # The 'server_socket' parameter is always defined within the function scope.
        if server_socket:
            try: server_socket.settimeout(None)
            except Exception: pass # Ignore errors if socket is already closed or invalid

        print("DEBUG: Server Handshake phase finished.")
        # This function doesn't return a result, the calling handler (folder_handler)
        # uses the transfer_success flag set before calling this handshake.