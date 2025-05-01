# tests.py - Logic for drive and network speed tests

import socket
import os
import threading
import time
import math
import sys
import random

# Import configuration and utilities (using absolute imports)
import config
import utils

# --- Drive Test Functions (Run in threads) ---

def run_write_speed_test(buffer_size, gui_callbacks, cancel_test_event):
    """ Thread task to perform a local write speed test """
    print("DEBUG: run_write_speed_test started")
    test_file_path = config.TEST_FILE_NAME
    bytes_to_write = config.TEST_FILE_SIZE

    # gui_callbacks['update_progress'](0) # Initial update is done in wrapper/caller
    # gui_callbacks['update_speed']("Speed: 0 B/s") # Initial update is done in wrapper/caller

    written_bytes = 0
    is_cancelled = False
    data = None

    try:
        print(f"DEBUG: Generating dummy data for write test with chunk size {utils.format_bytes(buffer_size)}...")
        # Ensure buffer size is not excessively large for os.urandom
        # If buffer_size is very large, generating it all at once might consume too much memory.
        # A safer approach is to generate data in smaller chunks if buffer_size > allocated_chunk_size.
        allocated_chunk_size = min(buffer_size, 4 * 1024 * 1024) # Cap allocation size to 4MB to avoid excessive memory use
        try: data = os.urandom(allocated_chunk_size)
        except NotImplementedError:
             print("DEBUG: os.urandom not available, using repeating pattern for dummy data.", file=sys.stderr)
             data = b'\xAA' * allocated_chunk_size

        if not data:
            # Fallback if even small allocation failed
            print("DEBUG: Failed to generate any dummy data, using a minimal byte.", file=sys.stderr)
            data = b'\x00'
            allocated_chunk_size = 1
            if buffer_size > 1:
                 gui_callbacks['update_status'](f"[!] هشدار: قادر به تولید داده تست بافر با اندازه {utils.format_bytes(buffer_size)} نبود. از بافر ۱ بایتی استفاده می‌شود.")


        gui_callbacks['update_status'](f"[*] تست نوشتن با بافر {utils.format_bytes(buffer_size)} و حجم {utils.format_bytes(bytes_to_write)} اجرا می‌شود.")

        start_time = time.time()
        last_update_time = start_time
        last_update_bytes = 0
        gui_callbacks['update_speed']("Speed: 0 B/s") # Start speed display here

        # Use 'with' statement for guaranteed file closing
        with open(test_file_path, "wb") as f:
            while written_bytes < bytes_to_write:
                if cancel_test_event.is_set():
                    is_cancelled = True
                    print("DEBUG: Write test cancelled by user")
                    gui_callbacks['update_status']("[*] تست نوشتن توسط کاربر لغو شد.")
                    break

                bytes_to_write_now = min(buffer_size, bytes_to_write - written_bytes) # Use the requested buffer_size for chunking
                if bytes_to_write_now <= 0: break # Finished or nothing left to write (should only happen if bytes_to_write was 0)

                # Prepare the chunk to write. If requested size is > allocated size, repeat allocated data.
                chunk_to_write = b""
                remaining_in_chunk = bytes_to_write_now
                # This loop efficiently creates a chunk of size bytes_to_write_now by repeating 'data'
                # It avoids creating a huge intermediate byte object if data is small but buffer_size is large.
                while remaining_in_chunk > 0:
                    bytes_from_data = min(remaining_in_chunk, allocated_chunk_size)
                    chunk_to_write += data[:bytes_from_data]
                    remaining_in_chunk -= bytes_from_data


                try:
                    # Write the prepared chunk
                    f.write(chunk_to_write)
                except Exception as e:
                    gui_callbacks['update_status'](f"[!] خطای نوشتن در فایل تست '{test_file_path}': {e}")
                    print(f"DEBUG: Error writing to test file '{test_file_path}': {e}", file=sys.stderr)
                    is_cancelled = True
                    break

                written_bytes += len(chunk_to_write) # Add actual bytes written

                current_time = time.time()
                progress = (written_bytes / bytes_to_write) * 100 if bytes_to_write > 0 else 0
                gui_callbacks['update_progress'](progress)

                if current_time - last_update_time >= config.SPEED_UPDATE_INTERVAL:
                    time_delta = current_time - last_update_time
                    bytes_since_last_update = written_bytes - last_update_bytes
                    speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                    speed_string = utils.format_bytes_per_second(speed_bps)
                    gui_callbacks['update_speed'](f"سرعت نوشتن: {speed_string}")

                    last_update_time = current_time
                    last_update_bytes = written_bytes

            # Check if the loop completed fully (all bytes written)
            if not is_cancelled and written_bytes < bytes_to_write:
                 # This case shouldn't happen if loop logic is correct and no exceptions occurred
                 gui_callbacks['update_status'](f"[!] تست نوشتن ناقص ماند ({utils.format_bytes(written_bytes)}/{utils.format_bytes(bytes_to_write)}).")
                 print("DEBUG: Write test incomplete due to unexpected exit")
                 is_cancelled = True # Mark as cancelled due to incomplete write

        if not is_cancelled and written_bytes >= bytes_to_write:
            end_time = time.time()
            total_time = end_time - start_time
            average_speed_bps = written_bytes / total_time if total_time > 0 else 0
            average_speed_string = utils.format_bytes_per_second(average_speed_bps)
            gui_callbacks['update_status'](f"[+] تست نوشتن کامل شد: {utils.format_bytes(written_bytes)} با موفقیت نوشته شد.")
            gui_callbacks['update_status'](f"    سرعت میانگین نوشتن: {average_speed_string}")
            gui_callbacks['show_info']("تست نوشتن کامل شد", f"تست سرعت نوشتن با موفقیت به پایان رسید.\nسرعت میانگین: {average_speed_string}")
            print("DEBUG: Write test completed successfully")


    except Exception as e:
        # Catch any exception during the process (file opening, writing, etc.)
        gui_callbacks['update_status'](f"[!] خطایی در حین تست نوشتن رخ داد: {e}")
        gui_callbacks['show_error']("خطای تست نوشتن", f"خطای غیرمنتظره در حین تست نوشتن:\n{e}")
        print(f"DEBUG: Exception during write test: {e}", file=sys.stderr)
        is_cancelled = True

    finally:
        print("DEBUG: run_write_speed_test finally block entered.")
        # Attempt to clean up the test file if it exists
        try:
            if os.path.exists(test_file_path):
                os.remove(test_file_path)
                print(f"DEBUG: Test file '{test_file_path}' removed.")
        except Exception as e:
            print(f"DEBUG: Error removing test file '{test_file_path}': {e}", file=sys.stderr)
            gui_callbacks['update_status'](f"[!] هشدار: قادر به حذف فایل تست '{test_file_path}' نبود: {e}")

        # Return success status (True if not cancelled and all bytes written, False otherwise)
        return not is_cancelled and written_bytes >= bytes_to_write


def run_read_speed_test(buffer_size, gui_callbacks, cancel_test_event):
    """ Thread task to perform a local read speed test """
    print("DEBUG: run_read_speed_test started")
    test_file_path = config.TEST_FILE_NAME
    bytes_to_read = config.TEST_FILE_SIZE
    is_cancelled = False

    # Ensure the test file exists and has the correct size, create it if not
    file_exists = os.path.exists(test_file_path)
    file_size_correct = False
    if file_exists:
        try: file_size_correct = os.path.getsize(test_file_path) == config.TEST_FILE_SIZE
        except Exception: file_size_correct = False # Assume incorrect if error

    if not file_exists or not file_size_correct:
        gui_callbacks['update_status'](f"[*] فایل تست '{test_file_path}' یافت نشد یا اندازه آن صحیح نیست. در حال ایجاد فایل ({utils.format_bytes(config.TEST_FILE_SIZE)})...")
        print(f"DEBUG: Test file '{test_file_path}' missing or wrong size. Creating...")
        create_buffer_size = min(buffer_size * 10, 4 * 1024 * 1024) # Use a reasonable buffer for creation, up to 4MB
        try: dummy_data = os.urandom(create_buffer_size)
        except NotImplementedError: dummy_data = b'\xAA' * create_buffer_size

        if not dummy_data:
             print("DEBUG: Failed to generate any dummy data for creation, using minimal byte.", file=sys.stderr)
             dummy_data = b'\x00'
             create_buffer_size = 1
             if buffer_size > 1:
                  gui_callbacks['update_status'](f"[!] هشدار: قادر به تولید داده برای ایجاد فایل تست بافر با اندازه {utils.format_bytes(buffer_size)} نبود. از بافر ۱ بایتی استفاده می‌شود.")

        creation_successful = False # Flag to track if creation fully succeeded
        try:
            with open(test_file_path, "wb") as f:
                written = 0
                while written < config.TEST_FILE_SIZE:
                    if cancel_test_event.is_set():
                        gui_callbacks['update_status']("[*] ایجاد فایل تست توسط کاربر لغو شد.")
                        print("DEBUG: Test file creation cancelled")
                        is_cancelled = True
                        break
                    bytes_to_write_now = min(len(dummy_data), config.TEST_FILE_SIZE - written)
                    if bytes_to_write_now <= 0: break

                    try:
                         f.write(dummy_data[:bytes_to_write_now])
                    except Exception as e:
                         gui_callbacks['update_status'](f"[!] خطای نوشتن در حین ایجاد فایل تست '{test_file_path}': {e}")
                         print(f"DEBUG: Error writing during test file creation '{test_file_path}': {e}", file=sys.stderr)
                         is_cancelled = True
                         break

                    written += bytes_to_write_now

            if not is_cancelled:
                 final_size = os.path.getsize(test_file_path) if os.path.exists(test_file_path) else 0
                 if final_size == config.TEST_FILE_SIZE:
                      gui_callbacks['update_status'](f"[+] فایل تست '{test_file_path}' با موفقیت ایجاد شد.")
                      print(f"DEBUG: Test file '{test_file_path}' created successfully.")
                      creation_successful = True
                 else:
                      gui_callbacks['update_status'](f"[!] خطا در ایجاد فایل تست '{test_file_path}': اندازه نهایی ({utils.format_bytes(final_size)}) صحیح نیست.")
                      gui_callbacks['show_error']("خطای ایجاد فایل تست", f"فایل تست '{test_file_path}' با اندازه صحیح ایجاد نشد.")

        except Exception as e:
            gui_callbacks['update_status'](f"[!] خطای ایجاد فایل تست '{test_file_path}': {e}")
            gui_callbacks['show_error']("خطای ایجاد فایل تست", f"خطا در ایجاد فایل تست '{test_file_path}':\n{e}")
            print(f"DEBUG: Error creating test file '{test_file_path}': {e}", file=sys.stderr)
            is_cancelled = True

        finally:
             if is_cancelled and os.path.exists(test_file_path):
                  try:
                      time.sleep(0.01)
                      os.remove(test_file_path)
                      print(f"DEBUG: Partial test file '{test_file_path}' removed after creation cancel/error.")
                  except Exception as e: print(f"DEBUG: Error removing partial test file '{test_file_path}': {e}", file=sys.stderr)

        if not creation_successful or is_cancelled:
             return False

    gui_callbacks['update_progress'](0)
    gui_callbacks['update_speed']("Speed: 0 B/s")

    read_bytes = 0
    is_cancelled = False

    try:
        gui_callbacks['update_status'](f"[*] تست خواندن با بافر {utils.format_bytes(buffer_size)} و حجم {utils.format_bytes(bytes_to_read)} اجرا می‌شود.")
        gui_callbacks['update_progress'](0)
        gui_callbacks['update_speed']("Speed: 0 B/s")

        start_time = time.time()
        last_update_time = start_time
        last_update_bytes = 0

        with open(test_file_path, "rb") as f:
            while read_bytes < bytes_to_read:
                if cancel_test_event.is_set():
                    is_cancelled = True
                    print("DEBUG: Read test cancelled by user")
                    gui_callbacks['update_status']("[*] تست خواندن توسط کاربر لغو شد.")
                    break

                bytes_to_read_now = min(buffer_size, bytes_to_read - read_bytes)
                if bytes_to_read_now <= 0:
                     break
                bytes_read_chunk = f.read(bytes_to_read_now)
                if not bytes_read_chunk:
                    if read_bytes < bytes_to_read:
                         gui_callbacks['update_status']("[!] پایان غیرمنتظره فایل در حین تست خواندن.")
                         print("DEBUG: Unexpected end of file during read test")
                         is_cancelled = True
                    break

                read_bytes += len(bytes_read_chunk)

                current_time = time.time()
                progress = (read_bytes / bytes_to_read) * 100 if bytes_to_read > 0 else 0
                gui_callbacks['update_progress'](progress)

                if current_time - last_update_time >= config.SPEED_UPDATE_INTERVAL:
                    time_delta = current_time - last_update_time
                    bytes_since_last_update = read_bytes - last_update_bytes
                    speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                    speed_string = utils.format_bytes_per_second(speed_bps)
                    gui_callbacks['update_speed'](f"سرعت خواندن: {speed_string}")

                    last_update_time = current_time
                    last_update_bytes = read_bytes

            if not is_cancelled and read_bytes < bytes_to_read:
                 gui_callbacks['update_status'](f"[!] تست خواندن ناقص ماند ({utils.format_bytes(read_bytes)}/{utils.format_bytes(bytes_to_read)}).")
                 gui_callbacks['show_warning']("هشدار تست", f"تست سرعت خواندن ناقص بود (فقط {utils.format_bytes(read_bytes)} خوانده شد).")
                 print(f"DEBUG: Read test incomplete, read {read_bytes}/{bytes_to_read} bytes")
                 is_cancelled = True


        if not is_cancelled and read_bytes >= bytes_to_read:
             end_time = time.time()
             total_time = end_time - start_time
             average_speed_bps = read_bytes / total_time if total_time > 0 else 0
             average_speed_string = utils.format_bytes_per_second(average_speed_bps)
             gui_callbacks['update_status'](f"[+] تست خواندن کامل شد: {utils.format_bytes(read_bytes)} با موفقیت خوانده شد.")
             gui_callbacks['update_status'](f"    سرعت میانگین خواندن: {average_speed_string}")
             gui_callbacks['show_info']("تست خواندن کامل شد", f"تست سرعت خواندن با موفقیت به پایان رسید.\nسرعت میانگین: {average_speed_string}")
             print("DEBUG: Read test completed successfully")

    except FileNotFoundError:
        gui_callbacks['update_status'](f"[!] خطای تست خواندن: فایل تست '{test_file_path}' پیدا نشد.")
        gui_callbacks['show_error']("خطای تست خواندن", f"فایل تست '{test_file_path}' پیدا نشد.\nلطفاً ابتدا تست نوشتن را اجرا کنید یا مطمئن شوید فایل موجود است.")
        print(f"DEBUG: Read test failed, file '{test_file_path}' not found")
        is_cancelled = True
    except Exception as e:
        gui_callbacks['update_status'](f"[!] خطایی در حین تست خواندن رخ داد: {e}")
        gui_callbacks['show_error']("خطای تست خواندن", f"خطای غیرمنتظره در حین تست خواندن:\n{e}")
        print(f"DEBUG: Exception during read test: {e}", file=sys.stderr)
        is_cancelled = True

    finally:
        print("DEBUG: run_read_speed_test finally block entered.")
        return not is_cancelled and read_bytes >= bytes_to_read


def run_write_speed_test_wrapper(buffer_size, gui_callbacks, cancel_test_event):
    """ Wrapper to run single write test and handle its specific cleanup and state reset """
    print("DEBUG: run_write_speed_test_wrapper started")
    try:
        gui_callbacks['update_status']("--- شروع تست سرعت نوشتن ---")
        gui_callbacks['on_test_started']('write')
        test_successful = run_write_speed_test(buffer_size, gui_callbacks, cancel_test_event)
        if not test_successful and not cancel_test_event.is_set():
             gui_callbacks['show_warning']("هشدار تست", "تست سرعت نوشتن با خطا پایان یافت.")

    except Exception as e:
         print(f"DEBUG: UNCAUGHT EXCEPTION IN run_write_speed_test_wrapper: {e}", file=sys.stderr)
         gui_callbacks['update_status'](f"[!] خطای غیرمنتظره در تست نوشتن: {e}")
         gui_callbacks['show_error']("خطای تست نوشتن", f"خطای غیرمنتظره در حین تست نوشتن:\n{e}")

    finally:
        print("DEBUG: run_write_speed_test_wrapper finally block entered")
        gui_callbacks['update_progress'](0)
        gui_callbacks['update_speed']("Speed: N/A - Test Finished")
        gui_callbacks['on_test_sequence_finished']()


def run_read_speed_test_wrapper(buffer_size, gui_callbacks, cancel_test_event):
    """ Wrapper to run single read test and handle its specific cleanup and state reset """
    print("DEBUG: run_read_speed_test_wrapper started")

    try:
        gui_callbacks['update_status']("--- شروع تست سرعت خواندن ---")
        gui_callbacks['on_test_started']('read')
        test_successful = run_read_speed_test(buffer_size, gui_callbacks, cancel_test_event)
        if not test_successful and not cancel_test_event.is_set():
             gui_callbacks['show_warning']("هشدار تست", "تست سرعت خواندن با خطا پایان یافت.")

    except Exception as e:
        print(f"DEBUG: UNCAUGHT EXCEPTION IN run_read_speed_test_wrapper: {e}", file=sys.stderr)
        gui_callbacks['update_status'](f"[!] خطای غیرمنتظره در تست خواندن: {e}")
        gui_callbacks['show_error']("خطای تست خواندن", f"خطای غیرمنتظره در حین تست خواندن:\n{e}")

    finally:
        print("DEBUG: run_read_speed_test_wrapper finally block entered")
        gui_callbacks['update_progress'](0)
        gui_callbacks['update_speed']("Speed: N/A - Test Finished")
        gui_callbacks['on_test_sequence_finished']()


def run_all_tests_task(buffer_size, gui_callbacks, cancel_test_event):
    """ Thread task to run sequential write and read speed tests """
    print("DEBUG: run_all_tests_task started")

    write_test_successful = False
    read_test_successful = False
    is_cancelled_sequence = False

    try:
        gui_callbacks['update_status']("--- شروع تست سرعت کلی (نوشتن و خواندن) ---")
        gui_callbacks['update_progress'](0)
        gui_callbacks['update_speed']("Speed: Starting Tests...")

        gui_callbacks['update_status']("[*] شروع تست سرعت نوشتن...")
        gui_callbacks['on_test_started']('write')
        write_test_successful = run_write_speed_test(buffer_size, gui_callbacks, cancel_test_event)
        gui_callbacks['on_test_finished']('write')

        if cancel_test_event.is_set():
             gui_callbacks['update_status']("[*] تست کلی توسط کاربر در مرحله نوشتن لغو شد.")
             is_cancelled_sequence = True
        elif not write_test_successful:
              gui_callbacks['update_status']("[!] تست نوشتن ناموفق بود. تست خواندن اجرا نمی‌شود.")
              is_cancelled_sequence = True

        if not is_cancelled_sequence:
             gui_callbacks['update_status']("[*] شروع تست سرعت خواندن...")
             gui_callbacks['on_test_started']('read')
             read_test_successful = run_read_speed_test(buffer_size, gui_callbacks, cancel_test_event)
             gui_callbacks['on_test_finished']('read')

             if cancel_test_event.is_set():
                  gui_callbacks['update_status']("[*] تست کلی توسط کاربر در مرحله خواندن لغو شد.")
                  is_cancelled_sequence = True
             elif not read_test_successful:
                  gui_callbacks['update_status']("[!] تست خواندن ناموفق بود.")
                  is_cancelled_sequence = True

        if is_cancelled_sequence:
             gui_callbacks['update_status']("[*] تست سرعت کلی لغو شد یا کامل نشد.")
             print("DEBUG: All tests sequence cancelled or failed.")
        else:
            if write_test_successful and read_test_successful:
                 gui_callbacks['update_status']("[+] تست سرعت کلی (نوشتن و خواندن) با موفقیت به پایان رسید.")
                 gui_callbacks['show_info']("تست همه کامل شد", "تست سرعت نوشتن و خواندن با موفقیت به پایان رسید.")
                 print("DEBUG: All tests sequence completed successfully")
            else:
                 gui_callbacks['update_status'](f"[!] تست سرعت کلی با خطا به پایان رسید (نوشتن: {'موفق' if write_test_successful else 'ناموفق'}, خواندن: {'موفق' if read_test_successful else 'ناموفق'}).")
                 gui_callbacks['show_warning']("هشدار تست", "تست سرعت کلی کامل نشد یا خطاهایی رخ داد.")
                 print("DEBUG: All tests sequence completed with errors")


    except Exception as e:
        print(f"DEBUG: UNCAUGHT EXCEPTION IN run_all_tests_task: {e}", file=sys.stderr)
        gui_callbacks['update_status'](f"[!] خطای غیرمنتظره در تست کلی: {e}")
        gui_callbacks['show_error']("خطای تست کلی", f"خطای غیرمنتظره در حین اجرای تست کلی:\n{e}")
        is_cancelled_sequence = True

    finally:
        print("DEBUG: run_all_tests_task finally block entered")
        gui_callbacks['update_progress'](0)
        gui_callbacks['update_speed']("Speed: N/A - Test Finished")
        gui_callbacks['on_test_sequence_finished']()


# --- Network Test Server Functions (Run in threads) ---

def handle_network_test_client(client_socket, address, gui_callbacks, cancel_test_event, receive_buffer_size_for_recv):
    """ Thread task to manage network test client connection and receive test data """
    # receive_buffer_size_for_recv: This is the buffer size the RECEIVER (this side) will use for socket.recv().
    print(f"DEBUG: handle_network_test_client started for {address}")

    # gui_callbacks['update_status'](f"[+] اتصال جدید تست شبکه از {address}") # Initial status done by server task
    gui_callbacks['update_speed']("Speed: Receiving Test Data...")
    gui_callbacks['update_progress'](0) # Ensure progress is 0

    bytes_to_receive = 0
    buffer_size_from_header = 4096 # Buffer size announced by sender (for info/log)
    received_bytes = 0
    is_cancelled = False

    try: # Outer try block covering header parsing and data receive
        client_socket.settimeout(10.0) # Timeout for initial header receive attempt
        header_buffer = b""
        header_sep_bytes = config.HEADER_SEPARATOR.encode('utf-8')
        header_expected_prefix_bytes = config.NETWORK_TEST_PROTOCOL_HEADER.encode('utf-8') # Prefix bytes without separator
        prefix_with_sep_bytes = header_expected_prefix_bytes + header_sep_bytes # Prefix followed by a separator
        min_separators_needed_after_prefix = 2 # Need testsize|buffersize after prefix
        max_header_read_buffer = config.BUFFER_SIZE_FOR_HEADER * 4 # Safety limit for header buffer


        # Read header more robustly
        start_header_read_time = time.time()
        header_fully_parsed = False

        while not header_fully_parsed:
            if cancel_test_event.is_set():
                gui_callbacks['update_status']("[*] دریافت تست شبکه توسط کاربر لغو شد.")
                is_cancelled = True
                print("DEBUG: Network test receive cancelled during header receive")
                break # Exit header reading loop


            if time.time() - start_header_read_time > 30.0:
                 raise socket.timeout("Overall timeout waiting for complete network test header.")


            try:
                client_socket.settimeout(config.CANCEL_CHECK_INTERVAL)
                chunk_size_to_read = config.BUFFER_SIZE_FOR_HEADER if len(header_buffer) == 0 else min(4096, config.BUFFER_SIZE_FOR_HEADER) # Read smaller chunks once buffer has content
                 # Don't read more than max_header_read_buffer into the buffer
                chunk_size_to_read = min(chunk_size_to_read, max_header_read_buffer - len(header_buffer))
                if chunk_size_to_read <= 0:
                    raise ValueError("Network test header buffer already at max size, header not found.")

                chunk = client_socket.recv(chunk_size_to_read)
                client_socket.settimeout(10.0)
            except socket.timeout:
                continue
            except Exception as e:
                 raise Exception(f"Error receiving network test header chunk: {e}")

            if not chunk:
                 if len(header_buffer) == 0: raise ConnectionResetError("Connection closed by peer before network test header receive.")
                 else: raise ValueError("Connection closed by peer during network test header receive.")

            header_buffer += chunk

            # Check if we have the expected prefix followed by a separator, and enough separators *after* that.
            # --- FIX START: More robust header prefix and separator finding ---
            prefix_and_first_sep_index = header_buffer.find(prefix_with_sep_bytes)

            if prefix_and_first_sep_index != -1: # Found "PREFIX|"
                # Calculate index *after* the first separator
                after_first_sep_index = prefix_and_first_sep_index + len(prefix_with_sep_bytes)

                # Check if there are enough separators remaining in the buffer after the first one
                if header_buffer[after_first_sep_index:].count(header_sep_bytes) >= min_separators_needed_after_prefix -1: # Need 1 more separator after first one

                    try:
                         # Find the position of the second separator *after* the first one
                         idx2_bytes = header_buffer.find(header_sep_bytes, after_first_sep_index)

                         if idx2_bytes != -1: # Found "PREFIX|TESTSIZE|"
                              # Extract the relevant bytes for testsize and buffersize based on byte indices
                              # Testsize bytes are between the first and second separator
                              testsize_bytes_raw = header_buffer[after_first_sep_index : idx2_bytes]
                              # Buffersize bytes plus data are after the second separator
                              buffersize_bytes_raw_plus_data = header_buffer[idx2_bytes + len(header_sep_bytes) :]

                              # Decode raw bytes for parsing (ignore errors)
                              testsize_str = testsize_bytes_raw.decode('utf-8', errors='ignore').strip() # Strip whitespace
                              buffersize_str_plus_data = buffersize_bytes_raw_plus_data.decode('utf-8', errors='ignore')

                              # Attempt parsing testsize
                              parsed_testsize = int(testsize_str) # Will raise ValueError if not int

                              # Attempt parsing buffersize - extract only leading digits from decoded string
                              buffersize_digits = ""
                              for char in buffersize_str_plus_data:
                                  if char.isdigit():
                                       buffersize_digits += char
                                  else:
                                       break # Stop at the first non-digit

                              if not buffersize_digits:
                                  # Buffersize part didn't start with digits or was empty, malformed header
                                  raise ValueError("Network test buffer size part does not start with digits or is empty.")

                              parsed_buffersize = int(buffersize_digits) # Will raise ValueError if not int if digits found

                              # If parsing is successful, the header structure is valid.
                              # Construct the exact header string based on parsed values.
                              exact_header_str = f"{config.NETWORK_TEST_PROTOCOL_HEADER}{config.HEADER_SEPARATOR}{testsize_str}{config.HEADER_SEPARATOR}{buffersize_digits}"
                              exact_header_bytes = exact_header_str.encode('utf-8')

                              # Find the exact bytes of the constructed header in the original buffer
                              # Search from the beginning of the buffer
                              header_start_index = header_buffer.find(exact_header_bytes)

                              if header_start_index != -1: # Found the exact header bytes
                                  # Found the exact header bytes! This is the end of the header in the buffer.
                                  # Set main header variables and remaining buffer.
                                  bytes_to_receive = parsed_testsize
                                  buffer_size_from_header = parsed_buffersize # Store for info, actual recv buffer is receiver side
                                  # Remaining buffer is from the end of the exact header bytes
                                  remaining_buffer = header_buffer[header_start_index + len(exact_header_bytes):]
                                  # header_data = exact_header_str # Store bytes or string for log
                                  print(f"DEBUG: Network test header successfully parsed and validated: '{exact_header_str}'. Remaining buffer size: {len(remaining_buffer)}")
                                  gui_callbacks['update_status'](f"[*] فرستنده از بافر با اندازه {utils.format_bytes(buffer_size_from_header)} برای تست استفاده می کند.")
                                  header_fully_parsed = True # Found header, exit loop
                              else:
                                   # Exact bytes not found. Log and keep reading.
                                   # This can happen if the buffer has leading garbage before the prefix.
                                   # If prefix_start_index_bytes > 0, we can discard the leading garbage and continue searching.
                                   if prefix_start_index_bytes > 0:
                                        print(f"DEBUG: Discarding {prefix_start_index_bytes} leading bytes before network test header prefix.")
                                        header_buffer = header_buffer[prefix_start_index_bytes:]
                                   else: # Prefix was at the start, but exact bytes not found - unexpected
                                        print(f"DEBUG: Found network test header parts based on separators, but could not find exact byte sequence in buffer starting at prefix. Buffer content start: {header_buffer[:50]}... Continuing to receive header data.")
                                        if len(header_buffer) >= max_header_read_buffer: # Prevent infinite loop
                                             raise ValueError("Could not find valid network test header pattern in buffer.")

                         # else: Second separator not found after the first one

                    except ValueError as e:
                           # Catch parsing/validation errors
                           header_snippet = header_buffer.decode('utf-8', errors='ignore')[:100] # Use buffer snippet for error log
                           print(f"DEBUG: Network test header parsing error: {e}. Likely malformed header. Decoded buffer start: {header_snippet}... Continuing to receive header data if possible.", file=sys.stderr)
                           if len(header_buffer) > max_header_read_buffer / 2:
                               raise ValueError(f"Malformed network test header received during parsing: {e}")
                    except Exception as e:
                           # Catch any other unexpected error
                           header_snippet = header_buffer.decode('utf-8', errors='ignore')[:100]
                           print(f"DEBUG: Unexpected error during network test header parsing attempt: {e}. Decoded buffer start: {header_snippet}... Continuing to receive header data if possible.", file=sys.stderr)
                           if len(header_buffer) > max_header_read_buffer / 2:
                               raise ValueError(f"Unexpected error during network test header parsing: {e}")

            # --- FIX END ---

            # If not fully parsed and buffer isn't excessive, check max buffer size before reading more
            if not header_fully_parsed and len(header_buffer) > max_header_read_buffer: # Safety limit
                 raise ValueError("Network test header buffer size exceeded limit during header read.")

            # If header not fully parsed and buffer isn't excessive, the loop will continue to read more chunks.


        # --- If header was successfully found and parsed, proceed ---
        # Check if loop exited due to cancellation
        if is_cancelled:
             print("DEBUG: Exiting handle_network_test_client due to cancellation after header loop.")
             return # Exit handler early, finally block will run

        # Check if header was fully parsed. If not, and not cancelled, something went wrong in the loop logic.
        if not header_fully_parsed:
             # This should ideally not be reached if the while loop condition and error handling worked,
             # but as a final safety measure if the loop exits unexpectedly without being cancelled:
             raise ValueError("Network test header was not fully parsed after header reading loop terminated unexpectedly.")


        client_socket.settimeout(None) # Remove timeout for data transfer


        # --- Network Test Data Receiving ---
        gui_callbacks['update_status'](f"[*] شروع دریافت داده تست شبکه ({utils.format_bytes(bytes_to_receive)}) از {address}...")
        gui_callbacks['update_progress'](0)
        gui_callbacks['update_speed']("Speed: 0 B/s") # Reset speed for data transfer phase


        start_time = time.time()
        last_update_time = start_time
        last_update_bytes = 0
        print("DEBUG: Starting network test receive loop")

        # Process remaining buffer first if any
        if remaining_buffer:
             bytes_to_process_now = min(len(remaining_buffer), bytes_to_receive - received_bytes)
             if bytes_to_process_now > 0:
                 # We don't write network test data to a file, just count it
                 received_bytes += bytes_to_process_now
                 print(f"DEBUG: Processed {bytes_to_process_now} bytes from header buffer for network test.")
             remaining_buffer = b"" # Clear the remaining buffer after using it

        # Use the *receiver's configured buffer size* for recv() calls (passed to handle_client_connection)
        # This `receive_buffer_size_for_recv` argument comes from the *test* buffer setting in GUI.
        # It's used here as the buffer size for receiving network test data.
        recv_buffer_size_for_loop = receive_buffer_size_for_recv if receive_buffer_size_for_recv is not None and receive_buffer_size_for_recv > 0 else 65536 # Default 64KB if invalid


        while received_bytes < bytes_to_receive:
            if cancel_test_event.is_set():
                gui_callbacks['update_status']("[*] تست شبکه توسط کاربر لغو شد.")
                is_cancelled = True
                print("DEBUG: Network test receive cancelled by user")
                break

            try:
                client_socket.settimeout(config.CANCEL_CHECK_INTERVAL)
                # Ensure we don't read more bytes than remaining if remaining < buffer size
                bytes_to_read_now = min(recv_buffer_size_for_loop, bytes_to_receive - received_bytes)
                if bytes_to_read_now <= 0:
                     # Should only happen if remaining_buffer fulfilled the test size, or test_size was 0
                     break # Exit loop if nothing more to read

                bytes_read_chunk = client_socket.recv(bytes_to_read_now) # Use the receiver's chosen buffer size here
                client_socket.settimeout(None) # Remove timeout after successful read
            except socket.timeout:
                continue # Keep trying to read if timeout is due to CANCEL_CHECK_INTERVAL
            except Exception as e:
                gui_callbacks['update_status'](f"[!] خطای خواندن داده تست شبکه از سوکت: {e}")
                print(f"DEBUG: Error reading network test data from socket: {e}", file=sys.stderr)
                is_cancelled = True
                break

            if not bytes_read_chunk:
                gui_callbacks['update_status'](f"[!] اتصال با {address} قبل از اتمام دریافت تست شبکه قطع شد.")
                print(f"DEBUG: Connection lost during receive from {address}")
                is_cancelled = True
                break

            # Just count the bytes, don't write them
            received_bytes += len(bytes_read_chunk)

            current_time = time.time()
            progress = (received_bytes / bytes_to_receive) * 100 if bytes_to_receive > 0 else 0
            gui_callbacks['update_progress'](progress)

            if current_time - last_update_time >= config.SPEED_UPDATE_INTERVAL:
                time_delta = current_time - last_update_time
                bytes_since_last_update = received_bytes - last_update_bytes
                speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                speed_string = utils.format_bytes_per_second(speed_bps)
                gui_callbacks['update_speed'](f"سرعت دانلود (دریافت کننده تست): {speed_string}")

                last_update_time = current_time
                last_update_bytes = received_bytes
        print("DEBUG: Network test receive loop finished")


        # --- Completion Status ---
        # Check if loop completed fully without cancellation
        if not is_cancelled and received_bytes < bytes_to_receive:
             # Loop ended prematurely without cancellation and without receiving full data
             gui_callbacks['update_status'](f"[!] دریافت داده تست شبکه ناقص ماند ({utils.format_bytes(received_bytes)}/{utils.format_bytes(bytes_to_receive)}) از {address}.")
             print(f"DEBUG: Network test receive incomplete, received {received_bytes}/{bytes_to_receive} bytes")
             is_cancelled = True # Mark as cancelled due to incomplete receive


        if not is_cancelled and received_bytes >= bytes_to_receive:
            end_time = time.time()
            total_time = end_time - start_time
            average_speed_bps = received_bytes / total_time if total_time > 0 else 0
            average_speed_string = utils.format_bytes_per_second(average_speed_bps)

            gui_callbacks['update_status'](f"[+] دریافت داده تست شبکه با موفقیت کامل شد.")
            gui_callbacks['update_status'](f"    سرعت میانگین دانلود (دریافت کننده تست): {average_speed_string}")
            gui_callbacks['show_info']("تست شبکه سمت دریافت کننده", f"دریافت داده تست شبکه کامل شد.\nسرعت میانگین دانلود: {average_speed_string}")
            print("DEBUG: Network test data received successfully")
        # else: Status messages handled where break conditions were met


    except (socket.timeout, ConnectionResetError, ValueError) as e:
         # Use header_buffer in error message if available and not too long
         header_snippet = header_buffer.decode('utf-8', errors='ignore')[:100] if header_buffer else ""
         msg = f"[!] خطا در ارتباط یا هدر تست شبکه با {address}: {e}"
         gui_callbacks['update_status'](msg)
         gui_callbacks['show_error']("خطای دریافت تست شبکه", f"خطا در ارتباط یا هدر تست شبکه از فرستنده ({address}):\n{e}\nهدر دریافتی (بخش اول): {header_snippet}...")
         print(f"DEBUG: Connection/Header error during network test receive from {address}: {e}. Header snippet: {header_snippet}...")
         is_cancelled = True # Ensure is_cancelled is set on these errors

    except Exception as e:
        if not is_cancelled: # Avoid double reporting
            gui_callbacks['update_status'](f"[!] خطایی در حین تست شبکه با {address} رخ داد: {e}")
            gui_callbacks['show_error']("خطای تست شبکه", f"خطا در حین تست شبکه با {address}:\n{e}")
            print(f"DEBUG: Uncaught Exception in handle_network_test_client with {address}: {e}", file=sys.stderr)
            is_cancelled = True

    finally:
        print(f"DEBUG: handle_network_test_client finally block entered for {address}")
        if 'client_socket' in locals() and client_socket:
            try: client_socket.close()
            except Exception: pass
            print(f"DEBUG: Client socket closed for {address}")

        # Reset GUI elements related to this single test client connection
        # Note: These handlers don't reset the main speed/progress bar like file transfer does,
        # because the server might handle multiple test clients sequentially without restarting the main server.
        # The main server thread (run_network_test_server_task) doesn't reset speed/progress on connection finish either.
        # The GUI state for network test client ACTIVE (is_network_test_client_active) is only set by the CLIENT (SENDER) side,
        # and reset by its on_test_sequence_finished callback.
        # The receiver side handler just processes one connection and exits.
        # The progress/speed bar should probably ONLY reflect the *active* transfer or test,
        # regardless of whether it's send or receive, file or network test.
        # The run_network_test_server_task does *not* call on_test_sequence_finished when a client connection finishes.
        # This seems correct for the server side. The server waits for *new* connections.
        # Status updates within the handler are sufficient.

        gui_callbacks['update_status'](f"[-] اتصال تست شبکه {address} بسته شد.")
        # Speed/progress are managed by the main test client thread or main server accept loop
        # Don't call on_test_sequence_finished here.
        print(f"DEBUG: handle_network_test_client finished for {address}")


def listen_for_network_test_discovery_task(stop_event, gui_callbacks, get_active_network_test_server_port_cb):
    """ Thread task to listen for UDP network test discovery messages and respond """
    print("DEBUG: listen_for_network_test_discovery_task started")

    udp_socket = None
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_socket.bind(("", config.NETWORK_TEST_DISCOVERY_PORT))
        gui_callbacks['update_status'](f"[*] دریافت کننده تست شبکه در حال گوش دادن روی UDP پورت {config.NETWORK_TEST_DISCOVERY_PORT} (کشف سرور)")
        print(f"DEBUG: Network test discovery server listening on UDP port {config.NETWORK_TEST_DISCOVERY_PORT}")

        while not stop_event.is_set():
            try:
                udp_socket.settimeout(0.5)
                message, client_address = udp_socket.recvfrom(2048)
                message = message.decode('utf-8', errors='ignore').strip()

                if message == config.NETWORK_TEST_DISCOVERY_MESSAGE:
                     active_network_test_server_port = get_active_network_test_server_port_cb()
                     if active_network_test_server_port is not None:
                         gui_callbacks['update_status'](f"[+] پیام کشف تست شبکه از {client_address[0]} دریافت شد. در حال ارسال پاسخ...")
                         print(f"DEBUG: Network test discovery message from {client_address[0]}. Sending response.")
                         current_response = f"{config.NETWORK_TEST_SERVER_RESPONSE_BASE} {active_network_test_server_port}"
                         udp_socket.sendto(current_response.encode('utf-8'), client_address)
                     else:
                         # This case means the TCP server failed to bind, or stopped.
                         print("DEBUG: Cannot respond to network test discovery, active_network_test_server_port is None or server not running.", file=sys.stderr)


            except socket.timeout:
                continue
            except Exception as e:
                 print(f"DEBUG: Minor error in Network Test UDP Discovery loop: {e}", file=sys.stderr)
                 time.sleep(0.1)

    except OSError as e:
        print(f"DEBUG: OSError starting network test discovery server: {e}", file=sys.stderr)
        if e.errno in (98, 10048): error_msg = f"[!] خطا: پورت UDP {config.NETWORK_TEST_DISCOVERY_PORT} (تست شبکه) در حال استفاده است."
        elif e.errno == 10013: error_msg = f"[!] خطا: دسترسی به پورت UDP {config.NETWORK_TEST_DISCOVERY_PORT} (تست شبکه) مسدود شده است (فایروال؟)."
        else: error_msg = f"[!] خطای مرگبار در شنونده کشف تست شبکه UDP: {e}"
        # Only show error if network test server is still meant to be running
        is_net_test_server_running_cb = gui_callbacks.get('is_network_test_server_running_cb')
        if is_net_test_server_running_cb is None or is_net_test_server_running_cb(): # Check if callback exists and returns True
             gui_callbacks['update_status'](error_msg)
             gui_callbacks['show_error']("خطای شنونده کشف تست شبکه", error_msg + "\nلطفا برنامه را ری‌استارت کنید.")

        stop_event.set()
    except Exception as e:
        print(f"DEBUG: Uncaught Exception in network test discovery server: {e}", file=sys.stderr)
        error_msg = f"[!] خطای مرگبار ناشناخته در شنونده کشف تست شبکه UDP: {e}"
        is_net_test_server_running_cb = gui_callbacks.get('is_network_test_server_running_cb')
        if is_net_test_server_running_cb is None or is_net_test_server_running_cb():
             gui_callbacks['update_status'](error_msg)
             gui_callbacks['show_error']("خطای شنونده کشف تست شبکه", f"خطای ناشناخته شنونده کشف تست شبکه UDP:\n{e}\nلطفا برنامه را ری‌استارت کنید.")
        stop_event.set()
    finally:
        print("DEBUG: listen_for_network_test_discovery_task finally block entered")
        if udp_socket:
            try: udp_socket.close()
            except Exception: pass
            print("DEBUG: Network test discovery socket closed")
        gui_callbacks['update_status']("[-] ترد شنونده کشف تست شبکه متوقف شد.")
        print("DEBUG: listen_for_network_test_discovery_task finished")


def run_network_test_server_task(stop_event, gui_callbacks, set_active_network_test_server_port_cb):
    """ Thread task for the main TCP network test server (bind, listen, accept connections) """
    print("DEBUG: run_network_test_server_task started")

    tcp_socket = None
    port_bound = False
    active_network_test_server_port = None

    try: # Outer try block for server binding and accept loop
        for port in config.NETWORK_TEST_PORTS:
            if stop_event.is_set():
                 print(f"DEBUG: Stop event set during network test TCP port binding attempt on {port}")
                 break
            try:
                print(f"DEBUG: Attempting to bind network test TCP server to port {port}")
                tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                tcp_socket.bind(("0.0.0.0", port))
                tcp_socket.listen(1) # Allow only one network test client connection at a time
                tcp_socket.settimeout(0.5)
                active_network_test_server_port = port
                port_bound = True
                set_active_network_test_server_port_cb(port) # Inform GUI
                # Status updates are handled by the callback
                # gui_callbacks['update_status'](f"[*] دریافت کننده تست شبکه در حال گوش دادن روی TCP پورت {active_network_test_server_port}")
                print(f"DEBUG: Network test TCP Server successfully bound to port {port}")

                # local_ip and readiness status moved to callback

                break # Successfully bound, exit the port trial loop

            except OSError as e:
                 print(f"DEBUG: Failed to bind network test server to port {port}: {e}", file=sys.stderr)
                 if e.errno in (98, 10048): gui_callbacks['update_status'](f"[!] پورت TCP تست شبکه {port} در حال استفاده است. در حال تلاش برای پورت بعدی...")
                 elif e.errno == 10013: gui_callbacks['update_status'](f"[!] دسترسی به پورت TCP تست شبکه {port} مسدود شده (فایروال؟). در حال تلاش برای پورت بعدی...")
                 else:
                     gui_callbacks['update_status'](f"[!] خطای OSError در پورت تست شبکه {port}: {e}. در حال تلاش برای پورت بعدی...")
                 if tcp_socket:
                     tcp_socket.close()
                     tcp_socket = None

            except Exception as e:
                 print(f"DEBUG: Uncaught Exception during network test port binding on {port}: {e}", file=sys.stderr)
                 gui_callbacks['update_status'](f"[!] خطای ناشناخته در پورت تست شبکه {port}: {e}. در حال تلاش برای پورت بعدی...")
                 if tcp_socket:
                     tcp_socket.close()
                     tcp_socket = None


        if not port_bound:
            print("DEBUG: Failed to bind network test server to any specified TCP port")
            error_msg = "[!] خطا: قادر به راه اندازی دریافت کننده تست شبکه TCP روی هیچ یک از پورت های مشخص شده نبود."
            gui_callbacks['update_status'](error_msg)
            gui_callbacks['show_error']("خطای دریافت کننده تست شبکه", f"برنامه قادر به راه اندازی دریافت کننده تست شبکه TCP روی هیچ پورتی نبود.\nلطفاً مطمئن شوید پورت ها توسط برنامه دیگری استفاده نشده و فایروال اجازه دسترسی داده است.")
            # Gui state updates in finally
            # gui_callbacks['on_network_test_server_stopped']()
            # gui_callbacks['update_speed']("Speed: Net Test Receiver Failed")
            print("DEBUG: run_network_test_server_task finished due to binding failure")
            # Closing socket happens in finally

            return # Exit thread if binding failed


        # Main server loop to accept connections
        while not stop_event.is_set():
             # The network test server currently allows only one client connection at a time due to listen(1)
             # If a handle_network_test_client thread is running, it will keep the accepted socket open.
             # The accept loop will be blocked until the client disconnects or the handler thread finishes.
             # The check for 'is_network_test_client_active_cb' seems misplaced here,
             # as that flag is set by the *client (sender)* side.
             # The network test server should just listen and accept the *next* connection.
             # The listen(1) limits it to one pending connection.
             # If you want to truly prevent accepting *while a handler is busy*,
             # you would need a counter or a flag managed by the server thread,
             # incremented before starting handler, decremented when handler finishes.
             # For now, let's remove the 'is_network_test_client_active_cb' check from the server accept loop
             # as it's incorrect logic for the server side. The listen(1) already limits concurrency.

            try:
                client_socket, address = tcp_socket.accept()
                print(f"DEBUG: Accepted network test connection from {address}")
                # gui_callbacks['on_test_started']('network_receive') # Signal GUI? Maybe not needed per connection

                # Get the configured TEST buffer size from GUI via callback
                test_recv_buffer_size = 65536 # Default
                get_test_buffer_size_cb = gui_callbacks.get('get_test_buffer_size')
                if get_test_buffer_size_cb:
                    try:
                        chosen_buffer = get_test_buffer_size_cb()
                        if isinstance(chosen_buffer, int) and chosen_buffer > 0:
                             test_recv_buffer_size = chosen_buffer
                             print(f"DEBUG: Using configured test receive buffer size from GUI: {test_recv_buffer_size}")
                        else:
                             print(f"DEBUG: get_test_buffer_size callback returned invalid value ({chosen_buffer}), using default 64KB", file=sys.stderr)
                    except Exception as e:
                         print(f"DEBUG: Error calling get_test_buffer_size callback for net test handler: {e}, using default.", file=sys.stderr)
                else:
                    print("DEBUG: get_test_buffer_size callback not found for net test handler, using default.", file=sys.stderr)


                client_handler_thread = threading.Thread(
                    target=handle_network_test_client,
                    args=(
                        client_socket,
                        address,
                        gui_callbacks,
                        gui_callbacks['cancel_test_event'],
                        test_recv_buffer_size # Pass the TEST buffer size for network test receive
                    ),
                    daemon=True
                )
                client_handler_thread.start()
            except socket.timeout:
                 # Expected timeout, check stop_event and loop again
                 continue
            except Exception as e:
                 if not stop_event.is_set():
                     gui_callbacks['update_status'](f"[!] خطای پذیرش اتصال تست شبکه TCP: {e}")
                     print(f"DEBUG: Error accepting network test TCP connection: {e}", file=sys.stderr)
                 time.sleep(0.1)

    except Exception as e:
        print(f"DEBUG: Uncaught Exception in network test TCP server accept loop: {e}", file=sys.stderr)
        error_msg = f"[!] خطای مرگبار در دریافت کننده تست شبکه TCP: {e}"
        gui_callbacks['update_status'](error_msg)
        gui_callbacks['show_error']("خطای دریافت کننده تست شبکه", f"خطای ناشناخته دریافت کننده تست شبکه TCP:\n{e}")
        # Gui state updates in finally
        # gui_callbacks['on_network_test_server_stopped']()
        # gui_callbacks['update_speed']("Speed: Net Test Receiver Failed")


    finally:
        print("DEBUG: run_network_test_server_task finally block entered")
        if tcp_socket:
            try:
                # Closing the socket unblocks accept
                tcp_socket.close()
                print("DEBUG: Network test TCP server socket closed")
            except Exception: pass

        # Signal GUI that network test server has stopped and reset state
        gui_callbacks['on_network_test_server_stopped']()
        gui_callbacks['update_status']("[-] سوکت اصلی دریافت کننده تست شبکه TCP بسته شد.")
        gui_callbacks['update_speed']("Speed: N/A - Net Test Receiver Stopped")
        set_active_network_test_server_port_cb(None) # Inform GUI
        print("DEBUG: run_network_test_server_task finished")


def discover_network_test_server_task(gui_callbacks):
    """ Thread task for the client to discover available network test servers using UDP broadcast """
    print("DEBUG: discover_network_test_server_task started")

    udp_socket = None
    found_server_info = None # (ip, port) tuple if found

    try:
        gui_callbacks['update_status'](f"[*] در حال جستجو برای دریافت کننده تست شبکه در شبکه روی UDP پورت {config.NETWORK_TEST_DISCOVERY_PORT}...")
        gui_callbacks['update_speed']("Speed: Discovering Test Receiver...")
        print(f"DEBUG: Broadcasting network test discovery message on UDP port {config.NETWORK_TEST_DISCOVERY_PORT}")

        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # No overall timeout on socket, use loop timeout

        message = config.NETWORK_TEST_DISCOVERY_MESSAGE.encode('utf-8')
        try:
            # Send broadcast message to the discovery port
            udp_socket.sendto(message, ('<broadcast>', config.NETWORK_TEST_DISCOVERY_PORT))
        except Exception as e:
            raise Exception(f"Error sending network test discovery broadcast: {e}")


        # Wait for a response
        start_discover_time = time.time()
        while not gui_callbacks['cancel_test_event'].is_set() and (time.time() - start_discover_time) < config.DISCOVERY_TIMEOUT:
             try:
                  # Set a short timeout within the loop to allow checking cancel event
                  udp_socket.settimeout(config.CANCEL_CHECK_INTERVAL) # Use CANCEL_CHECK_INTERVAL

                  response, server_address = udp_socket.recvfrom(1024)
                  response = response.decode('utf-8', errors='ignore').strip()
                  print(f"DEBUG: Received network test UDP response from {server_address[0]}: {response}")

                  # Check if the response starts with the expected base and contains a port number
                  if response.startswith(config.NETWORK_TEST_SERVER_RESPONSE_BASE):
                       parts = response.split()
                       if len(parts) == 2:
                            try:
                                server_port = int(parts[1])
                                # Found a valid server response
                                found_server_info = (server_address[0], server_port)
                                gui_callbacks['update_status'](f"[+] دریافت کننده تست شبکه پیدا شد در {server_address[0]}:{server_port}")
                                print(f"DEBUG: Network test server found: {found_server_info}")
                                break # Exit the response loop

                            except ValueError:
                                print(f"DEBUG: Invalid port number in network test discovery response from {server_address[0]}: {parts[1]}", file=sys.stderr)
                                continue # Continue listening for other responses
                       else:
                            print(f"DEBUG: Malformed network test discovery response from {server_address[0]}: {response}", file=sys.stderr)
                            continue # Continue listening for other responses

             except socket.timeout:
                 # Expected timeout, just loop again
                 continue
             except Exception as e:
                 # Handle other errors during receive
                 print(f"DEBUG: Error during network test UDP discovery response receive: {e}", file=sys.stderr)
                 time.sleep(0.05) # Small sleep


        # If loop exited because of timeout and no server was found, AND NOT cancelled
        if not gui_callbacks['cancel_test_event'].is_set() and found_server_info is None:
             gui_callbacks['update_status']("[*] جستجوی دریافت کننده تست شبکه به پایان رسید اما سروری پیدا نشد.")
             gui_callbacks['show_warning']("سرور تست شبکه یافت نشد", f"دریافت کننده تست شبکه در شبکه پیدا نشد ({config.DISCOVERY_TIMEOUT} ثانیه زمان انتظار). لطفاً مطمئن شوید برنامه در حالت 'شروع تست شبکه (دریافت)' روی کامپیوتر دیگر در حال اجرا است و فایروال اجازه ارتباط UDP را می‌دهد.")
             print("DEBUG: No network test server found within timeout.")

    except OSError as e:
         # Handle errors during socket creation or sendto
         print(f"DEBUG: OSError during network test discovery broadcast: {e}", file=sys.stderr)
         if e.errno == 10013: # WSAEACCES (Windows)
             error_msg = f"[!] خطا: دسترسی به پورت UDP {config.NETWORK_TEST_DISCOVERY_PORT} برای ارسال پیام کشف سرور تست شبکه مسدود شده است (فایروال؟). لطفاً دسترسی را مجاز کنید."
         else:
             error_msg = f"[!] خطای OSError در حین کشف سرور تست شبکه: {e}"
         gui_callbacks['update_status'](error_msg)
         gui_callbacks['show_error']("خطای کشف سرور تست شبکه", error_msg)

    except Exception as e:
        # Handle any other uncaught exceptions
        print(f"DEBUG: Uncaught Exception during network test server discovery: {e}", file=sys.stderr)
        error_msg = f"[!] خطای ناشناخته در حین کشف سرور تست شبکه: {e}"
        gui_callbacks['update_status'](error_msg)
        gui_callbacks['show_error']("خطای کشف سرور تست شبکه", f"خطای ناشناخته کشف سرور تست شبکه:\n{e}")

    finally:
        print("DEBUG: discover_network_test_server_task finally block entered")
        if udp_socket:
            try: udp_socket.close()
            except Exception: pass
            print("DEBUG: Network test discovery socket closed")

        # Return the found server info (or None)
        return found_server_info


def run_network_test_client_task(chosen_buffer_size, gui_callbacks, cancel_test_event):
    """ Thread task for the network test client (discover server, connect, send test data, measure) """
    print(f"DEBUG: run_network_test_client_task started with buffer size {chosen_buffer_size}")

    tcp_socket = None
    is_cancelled = False
    server_info = None # Initialize server_info

    try: # Outer try block for the entire send process
        gui_callbacks['update_status']("--- شروع حالت فرستنده تست شبکه ---")
        gui_callbacks['update_speed']("Speed: Searching for Network Test Receiver...")
        gui_callbacks['update_progress'](0)

        # Step 1: Discover the server
        # The discovery task runs and returns the result. It handles its own UI updates/errors.
        server_info = discover_network_test_server_task(gui_callbacks)

        if cancel_test_event.is_set():
             gui_callbacks['update_status']("[*] تست شبکه توسط کاربر پس از کشف دریافت کننده لغو شد.")
             is_cancelled = True
             print("DEBUG: Network test client cancelled after discovery")
             return # Exit if cancelled


        if server_info is None:
             # If discovery failed (timed out without cancel), the discovery task already reported it.
             # If discovery was cancelled, is_cancelled is True.
             # Just exit here.
             if not is_cancelled: # Only print if not already cancelled by event
                 print("DEBUG: Network test client cannot proceed, no receiver found.")
             return # Exit if no server found or discovery was cancelled


        server_ip, server_port = server_info

        # Step 2: Connect to the server
        gui_callbacks['update_speed'](f"Speed: Connecting to {server_ip}:{server_port}...")
        print(f"DEBUG: Attempting to connect to network test TCP server at {server_ip}:{server_port}")
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.settimeout(10) # Timeout for connection attempt

        print(f"DEBUG: Attempting socket.connect to {server_ip}:{server_port}")
        tcp_socket.connect((server_ip, server_port))
        print("DEBUG: Socket connection established")
        tcp_socket.settimeout(None) # Remove timeout after connection
        gui_callbacks['update_status']("[+] اتصال تست شبکه برقرار شد.")

        if cancel_test_event.is_set():
             gui_callbacks['update_status']("[*] تست شبکه توسط کاربر پس از اتصال لغو شد.")
             is_cancelled = True
             print("DEBUG: Network test client cancelled after connection")
             return # Exit if cancelled

        # Step 3: Send test header (NET_TEST_START|testsize|buffersize)
        # Header format: NET_TEST_START|testsize|buffersize
        header_str = f"{config.NETWORK_TEST_PROTOCOL_HEADER}{config.HEADER_SEPARATOR}{config.NETWORK_TEST_SIZE}{config.HEADER_SEPARATOR}{chosen_buffer_size}"
        header_bytes = header_str.encode('utf-8')

        if len(header_bytes) > config.BUFFER_SIZE_FOR_HEADER:
             # This should not happen with current config unless constants change
             error_msg = f"[!] خطای داخلی: هدر تست شبکه خیلی بزرگ است ({len(header_bytes)} بایت > {config.BUFFER_SIZE_FOR_HEADER} بایت)."
             gui_callbacks['update_status'](error_msg)
             gui_callbacks['show_error']("خطای هدر تست شبکه", "اطلاعات تست شبکه بیش از حد طولانی است.")
             is_cancelled = True
             print(f"DEBUG: Network test Header too large: {len(header_bytes)} > {config.BUFFER_SIZE_FOR_HEADER}")
             return

        print(f"DEBUG: Sending network test header: {header_str}")
        tcp_socket.sendall(header_bytes)
        gui_callbacks['update_status'](f"[*] هدر تست شبکه ارسال شد: {config.NETWORK_TEST_PROTOCOL_HEADER} | {utils.format_bytes(config.NETWORK_TEST_SIZE)} | {utils.format_bytes(chosen_buffer_size)}")
        print(f"DEBUG: Sent network test header ({len(header_bytes)} bytes)")


        # Step 4: Send the test data
        gui_callbacks['update_status'](f"[*] در حال ارسال داده تست شبکه ({utils.format_bytes(config.NETWORK_TEST_SIZE)}) با بافر {utils.format_bytes(chosen_buffer_size)} به {server_ip}...")
        gui_callbacks['update_progress'](0)
        gui_callbacks['update_speed']("Speed: 0 B/s") # Reset speed for data transfer phase

        # Generate dummy data - cap allocation size to avoid memory issues with huge buffers
        allocated_chunk_size = min(chosen_buffer_size, 4 * 1024 * 1024) # Cap allocation size to 4MB
        try: data = os.urandom(allocated_chunk_size)
        except NotImplementedError: data = b'\xAA' * allocated_chunk_size

        if not data:
             # Fallback if even small allocation failed
             print("DEBUG: Failed to generate any dummy data for network test, using a minimal byte.", file=sys.stderr)
             data = b'\x00'
             allocated_chunk_size = 1
             if chosen_buffer_size > 1:
                  gui_callbacks['update_status'](f"[!] هشدار: قادر به تولید داده تست بافر با اندازه {utils.format_bytes(chosen_buffer_size)} نبود. از بافر ۱ بایتی استفاده می‌شود.")


        sent_bytes = 0
        start_time = time.time()
        last_update_time = start_time
        last_update_bytes = 0
        print("DEBUG: Starting network test send loop")

        # Use the chosen buffer size for sending chunks
        send_buffer_size_for_loop = chosen_buffer_size # Use the size passed into this function


        while sent_bytes < config.NETWORK_TEST_SIZE:
            if cancel_test_event.is_set():
                gui_callbacks['update_status']("[*] تست شبکه توسط کاربر لغو شد.")
                is_cancelled = True
                print("DEBUG: Network test send cancelled by user")
                break

            bytes_to_send_now = min(send_buffer_size_for_loop, config.NETWORK_TEST_SIZE - sent_bytes)
            if bytes_to_send_now <= 0: break

            # Prepare the chunk to send. If requested size is > allocated size, repeat allocated data.
            chunk_to_send = b""
            remaining_in_chunk = bytes_to_send_now
            # This loop efficiently creates a chunk of size bytes_to_send_now by repeating 'data'
            while remaining_in_chunk > 0:
                bytes_from_data = min(remaining_in_chunk, allocated_chunk_size)
                chunk_to_send += data[:bytes_from_data]
                remaining_in_chunk -= bytes_from_data

            try:
                tcp_socket.settimeout(config.CANCEL_CHECK_INTERVAL)
                # Send the chunk over the socket
                tcp_socket.sendall(chunk_to_send)
                tcp_socket.settimeout(None)
            except socket.timeout:
                 gui_callbacks['update_status']("[!] زمان انتظار برای ارسال داده تست شبکه تمام شد.")
                 print("DEBUG: Timeout during network test socket send")
                 is_cancelled = True
                 break
            except Exception as e:
                 gui_callbacks['update_status'](f"[!] خطای ارسال داده تست شبکه به سوکت: {e}")
                 print(f"DEBUG: Error sending network test data: {e}", file=sys.stderr)
                 is_cancelled = True
                 break

            sent_bytes += len(chunk_to_send) # Add actual bytes sent

            current_time = time.time()
            progress = (sent_bytes / config.NETWORK_TEST_SIZE) * 100 if config.NETWORK_TEST_SIZE > 0 else 0
            gui_callbacks['update_progress'](progress)

            if current_time - last_update_time >= config.SPEED_UPDATE_INTERVAL:
                time_delta = current_time - last_update_time
                bytes_since_last_update = sent_bytes - last_update_bytes
                speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                speed_string = utils.format_bytes_per_second(speed_bps)
                gui_callbacks['update_speed'](f"سرعت آپلود (فرستنده تست): {speed_string}")

                last_update_time = current_time
                last_update_bytes = sent_bytes
        print("DEBUG: Network test send loop finished")

        # --- Completion Status ---
        # Check if loop completed fully without cancellation
        if not is_cancelled and sent_bytes < config.NETWORK_TEST_SIZE:
             # Loop ended prematurely without cancellation and without sending full data
             gui_callbacks['update_status'](f"[!] ارسال داده تست شبکه ناقص ماند ({utils.format_bytes(sent_bytes)}/{utils.format_bytes(config.NETWORK_TEST_SIZE)}) به {server_ip}.")
             print(f"DEBUG: Network test send incomplete, sent {sent_bytes}/{config.NETWORK_TEST_SIZE} bytes")
             is_cancelled = True # Mark as cancelled due to incomplete send


        if not is_cancelled and sent_bytes >= config.NETWORK_TEST_SIZE:
            end_time = time.time()
            total_time = end_time - start_time
            average_speed_bps = sent_bytes / total_time if total_time > 0 else 0
            average_speed_string = utils.format_bytes_per_second(average_speed_bps)

            gui_callbacks['update_status'](f"[+] ارسال داده تست شبکه با موفقیت کامل شد.")
            gui_callbacks['update_status'](f"    سرعت میانگین آپلود (فرستنده تست): {average_speed_string}")
            gui_callbacks['show_info']("تست شبکه سمت فرستنده", f"ارسال داده تست شبکه کامل شد.\nسرعت میانگین آپلود: {average_speed_string}")
            print("DEBUG: Network test data sent successfully")
        # else: Status messages handled where break conditions were met


    except ConnectionRefusedError:
        # server_ip and server_port should be set if discovery was successful
        server_ip, server_port = server_info if server_info else ("N/A", "N/A")
        msg = f"[!] خطا: اتصال تست شبکه به {server_ip}:{server_port} رد شد. آیا دریافت کننده تست شبکه هنوز فعال است؟"
        gui_callbacks['update_status'](msg)
        gui_callbacks['show_error']("خطای اتصال تست شبکه", f"دریافت کننده تست شبکه در {server_ip}:{server_port} اتصال را رد کرد.\nممکن است متوقف شده باشد.")
        print(f"DEBUG: Connection refused to {server_ip}:{server_port}")
        is_cancelled = True
    except socket.timeout:
        # This catches timeout during client_socket.connect()
        msg = "[!] خطا: زمان انتظار برای اتصال به دریافت کننده تست شبکه تمام شد."
        gui_callbacks['update_status'](msg)
        gui_callbacks['show_error']("خطای اتصال تست شبکه", "زمان انتظار برای اتصال به دریافت کننده تست شبکه تمام شد.")
        print("DEBUG: Socket timeout during connection attempt")
        is_cancelled = True
    except Exception as e:
        # Catch any other uncaught exceptions during the process
        if not is_cancelled:
            msg = f"[!] خطای تست شبکه (فرستنده): {e}"
            gui_callbacks['update_status'](msg)
            gui_callbacks['show_error']("خطای تست شبکه", f"خطایی در هنگام اجرای تست شبکه (فرستنده) رخ داد:\n{e}")
            print(f"DEBUG: Uncaught Exception in run_network_test_client_task: {e}", file=sys.stderr)
            is_cancelled = True

    finally:
        print("DEBUG: run_network_test_client_task finally block entered")
        if 'tcp_socket' in locals() and tcp_socket:
            try: tcp_socket.close()
            except Exception: pass
            print("DEBUG: Network test client socket closed")

        # Reset GUI elements related to test state
        gui_callbacks['update_progress'](0)
        gui_callbacks['update_speed']("Speed: N/A - Test Finished")

        # Signal GUI that the overall test sequence (a single client test counts as a sequence) is finished.
        # This callback resets the is_network_test_client_active flag and updates button states.
        gui_callbacks['on_test_sequence_finished']()
        print("DEBUG: run_network_test_client_task finished")


# --- Public functions to be called by GUI ---

def start_write_test(buffer_size, gui_callbacks, cancel_test_event):
    print("DEBUG: tests.start_write_test called")
    test_thread = threading.Thread(
        target=run_write_speed_test_wrapper,
        args=(buffer_size, gui_callbacks, cancel_test_event),
        daemon=True
    )
    test_thread.start()
    return test_thread

def start_read_test(buffer_size, gui_callbacks, cancel_test_event):
    print("DEBUG: tests.start_read_test called")
    test_thread = threading.Thread(
        target=run_read_speed_test_wrapper,
        args=(buffer_size, gui_callbacks, cancel_test_event),
        daemon=True
    )
    test_thread.start()
    return test_thread

def start_all_tests(buffer_size, gui_callbacks, cancel_test_event):
    print("DEBUG: tests.start_all_tests called")
    test_thread = threading.Thread(
        target=run_all_tests_task,
        args=(buffer_size, gui_callbacks, cancel_test_event),
        daemon=True
    )
    test_thread.start()
    return test_thread

def start_network_test_server(gui_callbacks, stop_event, discovery_stop_event):
     print("DEBUG: tests.start_network_test_server called")

     server_thread = threading.Thread(
         target=run_network_test_server_task,
         args=(stop_event, gui_callbacks, gui_callbacks['set_active_network_test_server_port']),
         daemon=True
     )
     server_thread.start()

     return server_thread

def stop_network_test_server(stop_event, discovery_stop_event, cancel_test_event, active_port):
     print("DEBUG: tests.stop_network_test_server called")
     discovery_stop_event.set()
     stop_event.set()
     cancel_test_event.set() # Cancel any active test *client handler* connections

     if active_port is not None:
        try:
            print(f"DEBUG: Attempting to connect to localhost:{active_port} to unblock network test TCP accept")
            # Using 127.0.0.1 (localhost) is usually sufficient to unblock a listener bound to 0.0.0.0
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            sock.connect(('127.0.0.1', active_port))
            # sock.sendall(b'stop') # Note: If you send data, handler needs to expect/discard it
            sock.close()
            print("DEBUG: Network test unblock connection sent.")
        except ConnectionRefusedError:
             print(f"DEBUG: Unblock connection to {active_port} refused, server likely already stopped or stopping.")
        except Exception as e:
            print(f"DEBUG: Failed to connect to localhost:{active_port} for unblock: {e}", file=sys.stderr)


def start_network_test_client(buffer_size, gui_callbacks, cancel_test_event):
     print("DEBUG: tests.start_network_test_client called")

     client_thread = threading.Thread(
         target=run_network_test_client_task,
         args=(buffer_size, gui_callbacks, cancel_test_event),
         daemon=True
     )
     client_thread.start()
     return client_thread

def start_network_test_discovery_listener(stop_event, gui_callbacks, get_active_network_test_server_port_cb):
    """ Thread task to listen for UDP network test discovery messages and respond """
    print("DEBUG: tests.start_network_test_discovery_listener called")
    discovery_thread = threading.Thread(
        target=listen_for_network_test_discovery_task,
        args=(stop_event, gui_callbacks, get_active_network_test_server_port_cb),
        daemon=True
    )
    discovery_thread.start()
    return discovery_thread