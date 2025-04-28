
# file_transfer_dynamic_port.py
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import socket
import os
import threading
import time
import math
import sys

# --- تنظیمات ---
DISCOVERY_PORT = 5000
# پورت های احتمالی برای انتقال فایل TCP
FILE_TRANSFER_PORTS = [5001, 5002, 5003, 5004, 5005] # لیست پورت ها
# BUFFER_SIZE حالا پویا تعیین می شود
SEPARATOR = "<SEPARATOR>"
HEADER_SEPARATOR = "|"
DISCOVERY_MESSAGE = "FIND_FILE_SERVER_XYZ"
# SERVER_RESPONSE حالا پویا ساخته می شود
DISCOVERY_TIMEOUT = 5
SPEED_UPDATE_INTERVAL = 0.5
CANCEL_CHECK_INTERVAL = 0.05
BUFFER_SIZE_FOR_HEADER = 2048 # بافر برای خواندن هدر

# --- متغیرهای سراسری برای وضعیت GUI و شبکه ---
server_thread = None
discovery_thread = None
server_stop_event = threading.Event()
discovery_stop_event = threading.Event()
cancel_transfer_event = threading.Event()
selected_filepath = ""
server_running = False
active_server_port = None # پورت TCP ای که سرور واقعا روی آن شروع شده است

# گزینه های اندازه بافر برای Combobox: نام نمایشی -> اندازه به بایت
BUFFER_OPTIONS = {
    "Auto": "Auto",
    "Small (4 KB)": 4096,
    "Medium (16 KB)": 16384,
    "Large (64 KB)": 65536,
    "X-Large (256 KB)": 262144,
    "Mega (1 MB)": 1048576,
}
# متغیرهای Tkinter بعد از root = tk.Tk() تعریف می شوند

# --- توابع کمکی برای GUI و نمایش سرعت ---

def update_status_safe(status_area, message):
    """ به صورت امن متن را در ناحیه وضعیت از هر نخی آپدیت می‌کند """
    if status_area.winfo_exists():
        status_area.insert(tk.END, message + "\n")
        status_area.see(tk.END)

def update_progress_safe(progress_bar, value):
    """ به صورت امن نوار پیشرفت را از هر نخی آپدیت می‌کند """
    if progress_bar.winfo_exists():
        progress_bar['value'] = value

def update_speed_safe(speed_var, speed_string):
    """ به صورت امن متن سرعت را در متغیر نمایش می‌دهد """
    if speed_var:
        speed_var.set(speed_string)

def format_bytes(byte_count):
     """ فرمت کردن تعداد بایت ها به KB, MB, GB """
     if byte_count is None or byte_count < 0:
         return "N/A"
     if byte_count == 0:
         return "0 B"

     size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
     if byte_count > 0:
        i = int(math.floor(math.log(byte_count, 1024)))
        p = math.pow(1024, i)
        s = round(byte_count / p, 2)
        return f"{s} {size_name[i]}"
     else:
        return "0 B"


def format_bytes_per_second(speed_bps):
    """ فرمت کردن سرعت (بایت بر ثانیه) به KB/s, MB/s, GB/s """
    if speed_bps is None or speed_bps < 0:
        return "N/A"
    if speed_bps == 0:
        return "0 B/s"

    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    if speed_bps > 0:
       i = int(math.floor(math.log(speed_bps, 1024)))
       p = math.pow(1024, i)
       s = round(speed_bps / p, 2)
       return f"{s} {size_name[i]}/s"
    else:
       return "0 B/s"

def get_buffer_size(selected_option, file_size):
    """ برگرداندن اندازه بافر بر اساس انتخاب کاربر و حجم فایل (برای حالت Auto) """
    if selected_option == "Auto":
        if file_size < 5 * 1024 * 1024:
            return 4096
        elif file_size < 50 * 1024 * 1024:
            return 16384
        elif file_size < 200 * 1024 * 1024:
            return 65536
        elif file_size < 1024 * 1024 * 1024:
             return 262144
        else:
            return 1048576

    elif selected_option in BUFFER_OPTIONS and selected_option != "Custom":
        return BUFFER_OPTIONS[selected_option]

    return 4096


# --- توابع هسته شبکه ---

def handle_client_connection(client_socket, address, status_area, progress_bar, speed_var, cancel_button, root):
    """ مدیریت اتصال کلاینت و دریافت فایل (آپدیت GUI و سرعت و چک لغو) """
    print(f"DEBUG: handle_client_connection started for {address}")
    update_status_safe(status_area, f"[+] اتصال جدید از {address} برای دریافت فایل")
    root.after(0, lambda: cancel_button.config(state=tk.NORMAL))
    root.after(0, lambda: update_speed_safe(speed_var, "Speed: Connecting..."))

    filesize = 0
    filename = "N/A"
    current_buffer_size = 4096
    received_bytes = 0
    is_cancelled = False

    try:
        client_socket.settimeout(10.0)
        header_data = client_socket.recv(BUFFER_SIZE_FOR_HEADER).decode()
        client_socket.settimeout(None)

        if not header_data or HEADER_SEPARATOR not in header_data:
             update_status_safe(status_area, f"[!] اطلاعات هدر نامعتبر از {address}")
             print(f"DEBUG: Invalid header from {address}: {header_data}")
             return

        parts = header_data.split(HEADER_SEPARATOR)
        if len(parts) != 3:
             update_status_safe(status_area, f"[!] فرمت هدر نامعتبر از {address}")
             print(f"DEBUG: Invalid header format from {address}: {header_data}")
             return

        filename = os.path.basename(parts[0])
        filesize = int(parts[1])
        current_buffer_size = int(parts[2])
        update_status_safe(status_area, f"[*] فرستنده از بافر با اندازه {format_bytes(current_buffer_size)} استفاده می کند.")
        print(f"DEBUG: Receiving file {filename} ({filesize} bytes) with buffer {current_buffer_size} from {address}")


        update_status_safe(status_area, f"[*] شروع دریافت: {filename} ({format_bytes(filesize)}) از {address}")
        root.after(0, lambda: update_progress_safe(progress_bar, 0))

        save_dir = "received_files"
        if not os.path.exists(save_dir):
            try:
                os.makedirs(save_dir)
            except OSError as e:
                 update_status_safe(status_area, f"[!] خطا در ایجاد پوشه '{save_dir}': {e}")
                 root.after(0, lambda e=e: messagebox.showerror("خطای پوشه", f"خطا در ایجاد پوشه '{save_dir}':\n{e}"))
                 print(f"DEBUG: Error creating directory {save_dir}: {e}")
                 return

        file_path = os.path.join(save_dir, filename)

        start_time = time.time()
        last_update_time = start_time
        last_update_bytes = 0
        root.after(0, lambda: update_speed_safe(speed_var, "Speed: 0 B/s"))


        with open(file_path, "wb") as f:
            while received_bytes < filesize:
                if cancel_transfer_event.is_set():
                    update_status_safe(status_area, "[*] دریافت توسط کاربر لغو شد.")
                    is_cancelled = True
                    print("DEBUG: Receive cancelled by user")
                    break

                try:
                    client_socket.settimeout(CANCEL_CHECK_INTERVAL)
                    bytes_read = client_socket.recv(current_buffer_size)
                    client_socket.settimeout(None)
                except socket.timeout:
                    continue
                except Exception as e:
                    update_status_safe(status_area, f"[!] خطای خواندن داده از سوکت: {e}")
                    print(f"DEBUG: Error reading from socket during receive: {e}")
                    break

                if not bytes_read:
                    update_status_safe(status_area, f"[!] اتصال با {address} قبل از اتمام دریافت قطع شد.")
                    print(f"DEBUG: Connection lost during receive from {address}")
                    break

                f.write(bytes_read)
                received_bytes += len(bytes_read)

                current_time = time.time()
                progress = (received_bytes / filesize) * 100 if filesize > 0 else 0
                root.after(0, lambda p=progress: update_progress_safe(progress_bar, p))

                if current_time - last_update_time >= SPEED_UPDATE_INTERVAL:
                    time_delta = current_time - last_update_time
                    bytes_since_last_update = received_bytes - last_update_bytes
                    speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                    speed_string = format_bytes_per_second(speed_bps)
                    root.after(0, lambda s=speed_string: update_speed_safe(speed_var, f"Speed: {s}"))

                    last_update_time = current_time
                    last_update_bytes = received_bytes

        if not is_cancelled:
            end_time = time.time()
            total_time = end_time - start_time
            average_speed_bps = received_bytes / total_time if total_time > 0 else 0
            average_speed_string = format_bytes_per_second(average_speed_bps)

            if received_bytes == filesize:
                 update_status_safe(status_area, f"[+] فایل '{filename}' با موفقیت در پوشه '{save_dir}' دریافت شد.")
                 update_status_safe(status_area, f"    سرعت میانگین دریافت: {average_speed_string}")
                 root.after(0, lambda f=filename: messagebox.showinfo("موفقیت", f"فایل '{f}' با موفقیت دریافت شد."))
                 print(f"DEBUG: File {filename} received successfully")
            else:
                 update_status_safe(status_area, f"[!] دریافت فایل '{filename}' ناقص ماند ({received_bytes}/{filesize} بایت).")
                 root.after(0, lambda f=filename: messagebox.showwarning("هشدار", f"دریافت فایل '{f}' ناقص بود."))
                 print(f"DEBUG: File {filename} receive incomplete")
        else:
            try:
                 os.remove(file_path)
                 update_status_safe(status_area, f"[*] فایل ناقص '{filename}' حذف شد.")
                 print(f"DEBUG: Incomplete file {file_path} removed")
            except Exception as e:
                 update_status_safe(status_area, f"[!] خطا در حذف فایل ناقص '{filename}': {e}")
                 print(f"DEBUG: Error removing incomplete file {file_path}: {e}")


    except socket.timeout:
         update_status_safe(status_area, "[!] زمان انتظار برای دریافت اطلاعات فایل تمام شد.")
         root.after(0, lambda: messagebox.showerror("خطای دریافت", "زمان انتظار برای دریافت اطلاعات فایل از فرستنده تمام شد."))
         print("DEBUG: Timeout waiting for file header")
    except Exception as e:
        update_status_safe(status_area, f"[!] خطایی در ارتباط با {address} رخ داد: {e}")
        root.after(0, lambda addr=address, err=e: messagebox.showerror("خطای دریافت", f"خطا در دریافت فایل از {addr}:\n{err}"))
        print(f"DEBUG: Exception in handle_client_connection with {address}: {e}")
    finally:
        if 'client_socket' in locals() and client_socket:
            try:
                client_socket.close()
                print(f"DEBUG: Client socket closed for {address}")
            except Exception:
                 pass
        update_status_safe(status_area, f"[-] اتصال {address} بسته شد.")
        root.after(0, lambda: update_progress_safe(progress_bar, 0))
        root.after(0, lambda: update_speed_safe(speed_var, "Speed: N/A - Finished"))
        root.after(0, lambda: cancel_button.config(state=tk.DISABLED))
        print(f"DEBUG: handle_client_connection finished for {address}")


def listen_for_discovery_task(stop_event, status_area, root):
    """ وظیفه گوش دادن به پیام‌های کشف UDP در یک نخ """
    print("DEBUG: listen_for_discovery_task started")
    global server_running, active_server_port # نیاز به active_server_port
    udp_socket = None
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.bind(("0.0.0.0", DISCOVERY_PORT))
        root.after(0, lambda: update_status_safe(status_area, f"[*] سرور کشف در حال گوش دادن روی UDP پورت {DISCOVERY_PORT}"))
        print(f"DEBUG: Discovery server listening on UDP port {DISCOVERY_PORT}")

        local_ip = "N/A"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
             pass

        # نمایش پورت فعال TCP به کاربر در GUI
        root.after(0, lambda ip=local_ip, port=active_server_port: update_status_safe(status_area, f"    آماده دریافت فایل در: {ip}:{port}"))
        print(f"DEBUG: Ready to receive files at {local_ip}:{active_server_port}")


        while not stop_event.is_set():
            try:
                udp_socket.settimeout(0.5)
                message, client_address = udp_socket.recvfrom(2048)
                message = message.decode()
                if message == DISCOVERY_MESSAGE:
                     root.after(0, lambda addr=client_address: update_status_safe(status_area, f"[+] پیام کشف از {addr[0]} دریافت شد. در حال ارسال پاسخ..."))
                     print(f"DEBUG: Discovery message received from {client_address[0]}. Sending response.")
                     # پاسخ با پورت فعال فعلی سرور
                     current_server_response = f"IM_FILE_SERVER_XYZ {active_server_port}"
                     udp_socket.sendto(current_server_response.encode(), client_address)
            except socket.timeout:
                continue
            except Exception as e:
                 print(f"DEBUG: Minor error in UDP Discovery loop: {e}", file=sys.stderr)

    except OSError as e:
        print(f"DEBUG: OSError starting discovery server: {e}", file=sys.stderr)
        if e.errno in (98, 10048):
            error_msg = f"[!] خطا: پورت UDP {DISCOVERY_PORT} در حال استفاده است."
            root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
            root.after(0, lambda: messagebox.showerror("خطای سرور کشف", f"پورت UDP {DISCOVERY_PORT} توسط برنامه دیگری در حال استفاده است."))
        else:
            error_msg = f"[!] خطای مرگبار در سرور کشف UDP: {e}"
            root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
            root.after(0, lambda err=e: messagebox.showerror("خطای سرور کشف", f"خطای راه اندازی سرور کشف UDP:\n{err}"))
        server_running = False
        root.after(0, update_server_button_state)
    except Exception as e:
        print(f"DEBUG: Uncaught Exception in discovery server: {e}", file=sys.stderr)
        error_msg = f"[!] خطای مرگبار ناشناخته در سرور کشف UDP: {e}"
        root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
        root.after(0, lambda err=e: messagebox.showerror("خطای سرور کشف", f"خطای ناشناخته سرور کشف UDP:\n{err}"))
        server_running = False
        root.after(0, update_server_button_state)
    finally:
        if udp_socket:
            udp_socket.close()
            print("DEBUG: Discovery socket closed")
        root.after(0, lambda: update_status_safe(status_area, "[-] ترد شنونده کشف متوقف شد."))
        print("DEBUG: listen_for_discovery_task finished")


def run_tcp_server_task(stop_event, status_area, progress_bar, speed_var, cancel_button, root):
    """ وظیفه اصلی سرور TCP برای پذیرش اتصالات در یک نخ """
    print("DEBUG: run_tcp_server_task started")
    global server_running, active_server_port
    tcp_socket = None
    port_bound = False

    # سعی می کند روی پورت های مختلف Bind کند
    for port in FILE_TRANSFER_PORTS:
        if stop_event.is_set(): # اگر در حین امتحان پورت، توقف درخواست شد
             print(f"DEBUG: Stop event set during port binding attempt on {port}")
             break
        try:
            print(f"DEBUG: Attempting to bind TCP server to port {port}") # Debug print
            tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tcp_socket.bind(("0.0.0.0", port))
            tcp_socket.listen(5)
            tcp_socket.settimeout(0.5)
            active_server_port = port # پورت فعال را ثبت می کند
            port_bound = True
            root.after(0, lambda p=port: update_status_safe(status_area, f"[*] سرور انتقال فایل در حال گوش دادن روی TCP پورت {p}"))
            print(f"DEBUG: TCP Server successfully bound to port {port}") # Debug print
            break # Bind موفق بود، از حلقه خارج می شود
        except OSError as e:
             print(f"DEBUG: Failed to bind to port {port}: {e}", file=sys.stderr) # Debug print to stderr
             if e.errno in (98, 10048):
                 root.after(0, lambda p=port: update_status_safe(status_area, f"[!] پورت TCP {p} در حال استفاده است. در حال تلاش برای پورت بعدی..."))
             elif e.errno == 10013: # Permission denied
                  root.after(0, lambda p=port: update_status_safe(status_area, f"[!] دسترسی به پورت TCP {p} مسدود شده (فایروال؟). در حال تلاش برای پورت بعدی..."))
             else:
                 # خطاهای OSError دیگر جدی تر هستند
                 root.after(0, lambda p=port, err=e: update_status_safe(status_area, f"[!] خطای OSError در پورت {p}: {err}. در حال تلاش برای پورت بعدی..."))
             if tcp_socket:
                 tcp_socket.close() # سوکت را ببند قبل از تلاش بعدی
                 tcp_socket = None # مطمئن شو که سوکت برای تلاش بعدی None است

        except Exception as e:
             print(f"DEBUG: Uncaught Exception during port binding on {port}: {e}", file=sys.stderr) # Debug print to stderr
             root.after(0, lambda p=port, err=e: update_status_safe(status_area, f"[!] خطای ناشناخته در پورت {p}: {err}. در حال تلاش برای پورت بعدی..."))
             if tcp_socket:
                 tcp_socket.close()
                 tcp_socket = None

    if not port_bound:
        error_msg = "[!] خطا: قادر به راه اندازی سرور TCP روی هیچ یک از پورت های مشخص شده نبود."
        root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
        root.after(0, lambda: messagebox.showerror("خطای سرور", "برنامه قادر به راه اندازی سرور TCP روی هیچ پورتی نبود.\nلطفاً مطمئن شوید پورت ها توسط برنامه دیگری استفاده نشده و فایروال اجازه دسترسی داده است."))
        print("DEBUG: Failed to bind to any specified TCP port") # Debug print
        server_running = False
        root.after(0, update_server_button_state)
        root.after(0, lambda: update_speed_safe(speed_var, "Speed: Server Failed"))
        print("DEBUG: run_tcp_server_task finished due to binding failure") # Debug print
        # اینجا لازم نیست continue کنیم، چون Bind ناموفق بود
        if tcp_socket: tcp_socket.close() # اطمینان از بسته شدن در صورت خطا قبل از خروج
        return # خروج از تابع نخ


    # اگر به اینجا رسیدیم، یعنی Bind موفقیت آمیز بوده است.
    root.after(0, lambda: update_status_safe(status_area, "[*] منتظر دریافت اتصال برای انتقال فایل..."))


    try:
        while not stop_event.is_set():
            try:
                client_socket, address = tcp_socket.accept()
                print(f"DEBUG: Accepted connection from {address}")
                client_handler_thread = threading.Thread(
                    target=handle_client_connection,
                    args=(client_socket, address, status_area, progress_bar, speed_var, cancel_button, root),
                    daemon=True
                )
                client_handler_thread.start()
            except socket.timeout:
                 continue
            except Exception as e:
                 if not stop_event.is_set() and server_running:
                     root.after(0, lambda err=e: update_status_safe(status_area, f"[!] خطای پذیرش اتصال TCP: {err}"))
                     print(f"DEBUG: Error accepting TCP connection: {e}", file=sys.stderr)


    except Exception as e: # خطاهای احتمالی در حلقه accept
        print(f"DEBUG: Uncaught Exception in TCP server accept loop: {e}", file=sys.stderr)
        error_msg = f"[!] خطای مرگبار در سرور TCP: {e}"
        root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
        root.after(0, lambda err=e: messagebox.showerror("خطای سرور", f"خطای ناشناخته سرور TCP:\n{err}"))
        # در صورت خطای مرگبار در حلقه اصلی، وضعیت را آپدیت کن
        server_running = False
        root.after(0, update_server_button_state)
        root.after(0, lambda: update_speed_safe(speed_var, "Speed: Server Error"))

    finally:
        if tcp_socket:
            try:
                tcp_socket.close()
                print("DEBUG: TCP server socket closed")
            except Exception:
                 pass
        server_running = False
        root.after(0, update_server_button_state)
        root.after(0, lambda: update_status_safe(status_area, "[-] سوکت اصلی TCP سرور بسته شد."))
        root.after(0, lambda: update_speed_safe(speed_var, "Speed: N/A - Server Stopped"))
        print("DEBUG: run_tcp_server_task finished")


def discover_server_task(status_area):
    """ تلاش برای پیدا کردن سرور در شبکه (برگرداندن اطلاعات سرور یا None) """
    print("DEBUG: discover_server_task started")
    udp_socket = None
    server_info = None
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_socket.settimeout(DISCOVERY_TIMEOUT)

        update_status_safe(status_area, f"[*] ارسال پیام کشف به پورت {DISCOVERY_PORT} برای {DISCOVERY_TIMEOUT} ثانیه...")
        print(f"DEBUG: Sending discovery broadcast to port {DISCOVERY_PORT}")
        udp_socket.sendto(DISCOVERY_MESSAGE.encode(), ('<broadcast>', DISCOVERY_PORT))

        print(f"DEBUG: Waiting for discovery response for {DISCOVERY_TIMEOUT} seconds")
        try:
             response, server_address = udp_socket.recvfrom(2048)
             response = response.decode()
             print(f"DEBUG: Received UDP response from {server_address}: {response}")
             if response.startswith("IM_FILE_SERVER_XYZ"):
                 parts = response.split()
                 if len(parts) == 2:
                     try:
                         server_ip = server_address[0]
                         server_tcp_port = int(parts[1]) # خواندن پورت TCP از پاسخ
                         server_info = (server_ip, server_tcp_port)
                         update_status_safe(status_area, f"[+] پاسخ کشف از {server_ip} دریافت شد.")
                         print(f"DEBUG: Valid server response, server_info: {server_info}")
                     except (ValueError, IndexError):
                         update_status_safe(status_area, "[!] پاسخ کشف نامعتبر دریافت شد.")
                         print("DEBUG: Invalid format in server discovery response")
             else:
                 update_status_safe(status_area, "[!] پاسخ UDP نامربوط دریافت شد.")
                 print("DEBUG: Irrelevant UDP response received")
        except socket.timeout:
            update_status_safe(status_area, "[*] زمان انتظار برای پاسخ کشف به پایان رسید.")
            print("DEBUG: Discovery timeout")
        except Exception as e:
            update_status_safe(status_area, f"[!] خطای دریافت پاسخ کشف: {e}")
            print(f"DEBUG: Error receiving discovery response: {e}")

    except Exception as e:
        update_status_safe(status_area, f"[!] خطای کشف سرور: {e}")
        print(f"DEBUG: Error during discovery process: {e}")
    finally:
        if udp_socket:
            udp_socket.close()
            print("DEBUG: Discovery socket closed")
    print(f"DEBUG: discover_server_task finished, returning {server_info}")
    return server_info

def send_file_task(server_host, server_port, filename, status_area, progress_bar, speed_var, cancel_button, root, send_button, buffer_size):
    """ وظیفه ارسال فایل در یک نخ (با آپدیت GUI و سرعت و چک لغو و اندازه بافر انتخابی) """
    print(f"DEBUG: send_file_task started to {server_host}:{server_port}")
    filesize = 0
    file_basename = os.path.basename(filename)
    root.after(0, lambda: update_speed_safe(speed_var, "Speed: Connecting..."))
    root.after(0, lambda: cancel_button.config(state=tk.NORMAL))
    sent_bytes = 0
    is_cancelled = False

    try:
        filesize = os.path.getsize(filename)
        print(f"DEBUG: File size: {filesize} bytes")
    except Exception as e:
         root.after(0, lambda err=e: update_status_safe(status_area, f"[!] خطا در خواندن فایل '{file_basename}': {err}"))
         root.after(0, lambda err=e, f=file_basename: messagebox.showerror("خطای فایل", f"فایل '{f}' قابل خواندن نیست:\n{err}"))
         print(f"DEBUG: Error getting file size: {e}")
         return

    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_socket.settimeout(10) # تایم‌اوت برای اتصال

    try:
        root.after(0, lambda: update_status_safe(status_area, f"[+] در حال اتصال به سرور در {server_host}:{server_port}..."))
        print(f"DEBUG: Attempting to connect to {server_host}:{server_port}")
        tcp_socket.connect((server_host, server_port))
        tcp_socket.settimeout(None)
        root.after(0, lambda: update_status_safe(status_area, "[+] اتصال برقرار شد."))
        print("DEBUG: Connection established")
        root.after(0, lambda: update_progress_safe(progress_bar, 0))

        header = f"{file_basename}{HEADER_SEPARATOR}{filesize}{HEADER_SEPARATOR}{buffer_size}"
        header_bytes = header.encode('utf-8')
        if len(header_bytes) > BUFFER_SIZE_FOR_HEADER:
             error_msg = f"[!] خطای داخلی: هدر خیلی بزرگ است ({len(header_bytes)} بایت > {BUFFER_SIZE_FOR_HEADER} بایت). لطفاً نام فایل یا اندازه بافر کوچکتر انتخاب کنید."
             root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
             root.after(0, lambda: messagebox.showerror("خطای هدر", "اطلاعات فایل (نام، اندازه، بافر) بیش از حد طولانی است."))
             is_cancelled = True
             print(f"DEBUG: Header too large: {len(header_bytes)} > {BUFFER_SIZE_FOR_HEADER}")
             return

        tcp_socket.send(header_bytes)
        root.after(0, lambda: update_status_safe(status_area, f"[*] در حال ارسال: {file_basename} ({format_bytes(filesize)}) با بافر {format_bytes(buffer_size)}"))
        print(f"DEBUG: Sending header: {header_bytes}")


        start_time = time.time()
        last_update_time = start_time
        last_update_bytes = 0
        root.after(0, lambda: update_speed_safe(speed_var, "Speed: 0 B/s"))
        print("DEBUG: Starting file transfer loop")

        with open(filename, "rb") as f:
            while sent_bytes < filesize:
                if cancel_transfer_event.is_set():
                    update_status_safe(status_area, "[*] ارسال توسط کاربر لغو شد.")
                    is_cancelled = True
                    print("DEBUG: Send cancelled by user")
                    break

                bytes_read = f.read(buffer_size)
                if not bytes_read:
                    update_status_safe(status_area, "[!] خطای خواندن فایل یا پایان غیرمنتظره فایل در حین ارسال.")
                    print("DEBUG: Error reading file during send")
                    break

                try:
                    tcp_socket.settimeout(CANCEL_CHECK_INTERVAL)
                    tcp_socket.sendall(bytes_read)
                    tcp_socket.settimeout(None)
                except socket.timeout:
                     update_status_safe(status_area, "[!] زمان انتظار برای ارسال داده تمام شد.")
                     print("DEBUG: Timeout during socket send")
                     break
                except Exception as e:
                     update_status_safe(status_area, f"[!] خطای ارسال داده به سوکت: {e}")
                     print(f"DEBUG: Error sending data: {e}")
                     break

                sent_bytes += len(bytes_read)

                current_time = time.time()
                progress = (sent_bytes / filesize) * 100 if filesize > 0 else 0
                root.after(0, lambda p=progress: update_progress_safe(progress_bar, p))

                if current_time - last_update_time >= SPEED_UPDATE_INTERVAL:
                    time_delta = current_time - last_update_time
                    bytes_since_last_update = sent_bytes - last_update_bytes
                    speed_bps = bytes_since_last_update / time_delta if time_delta > 0 else 0
                    speed_string = format_bytes_per_second(speed_bps)
                    root.after(0, lambda s=speed_string: update_speed_safe(speed_var, f"Speed: {s}"))

                    last_update_time = current_time
                    last_update_bytes = sent_bytes
            print("DEBUG: File transfer loop finished")


        if not is_cancelled:
            end_time = time.time()
            total_time = end_time - start_time
            average_speed_bps = sent_bytes / total_time if total_time > 0 else 0
            average_speed_string = format_bytes_per_second(average_speed_bps)

            if sent_bytes == filesize:
                root.after(0, lambda f=file_basename: update_status_safe(status_area, f"[+] فایل '{f}' با موفقیت ارسال شد."))
                root.after(0, lambda f=file_basename: update_status_safe(status_area, f"    سرعت میانگین ارسال: {average_speed_string}"))
                root.after(0, lambda f=file_basename: messagebox.showinfo("موفقیت", f"فایل '{f}' با موفقیت ارسال شد."))
                print(f"DEBUG: File {filename} sent successfully")
            else:
                 update_status_safe(status_area, f"[!] ارسال فایل '{file_basename}' ناقص ماند ({sent_bytes}/{filesize} بایت).")
                 root.after(0, lambda f=file_basename: messagebox.showwarning("هشدار", f"ارسال فایل '{f}' ناقص بود."))
                 print(f"DEBUG: File {filename} send incomplete")
        else:
            pass


    except ConnectionRefusedError:
        msg = f"[!] خطا: اتصال به {server_host}:{server_port} رد شد. آیا سرور هنوز فعال است؟"
        root.after(0, lambda m=msg: update_status_safe(status_area, m))
        root.after(0, lambda h=server_host, p=server_port: messagebox.showerror("خطای اتصال", f"سرور در {h}:{p} اتصال را رد کرد.\nممکن است سرور متوقف شده باشد."))
        print(f"DEBUG: Connection refused to {server_host}:{server_port}")
    except socket.timeout:
        msg = "[!] خطا: زمان انتظار برای اتصال به سرور تمام شد."
        root.after(0, lambda m=msg: update_status_safe(status_area, m))
        root.after(0, lambda: messagebox.showerror("خطای اتصال", "زمان انتظار برای اتصال به سرور تمام شد."))
        print("DEBUG: Socket timeout during connection attempt")
    except Exception as e:
        msg = f"[!] خطای ارسال فایل: {e}"
        root.after(0, lambda m=msg: update_status_safe(status_area, m))
        root.after(0, lambda err=e: messagebox.showerror("خطای ارسال", f"خطایی در هنگام ارسال فایل رخ داد:\n{err}"))
        print(f"DEBUG: Uncaught Exception in send_file_task: {e}")
    finally:
        if 'tcp_socket' in locals() and tcp_socket:
            try:
                tcp_socket.close()
                print("DEBUG: Client socket closed")
            except Exception:
                 pass
        root.after(0, lambda: update_status_safe(status_area, "[-] اتصال TCP کلاینت بسته شد."))
        root.after(0, lambda: update_progress_safe(progress_bar, 0))
        root.after(0, lambda: update_speed_safe(speed_var, "Speed: N/A - Finished"))
        root.after(0, lambda: send_button.config(state=tk.NORMAL if selected_filepath else tk.DISABLED))
        root.after(0, lambda: select_file_button.config(state=tk.NORMAL))
        root.after(0, lambda: buffer_size_combobox.config(state='readonly' if selected_filepath else tk.DISABLED))
        root.after(0, lambda: cancel_button.config(state=tk.DISABLED))
        cancel_transfer_event.clear()
        print("DEBUG: send_file_task finished")


# --- توابع مربوط به GUI ---

def select_file():
    """ باز کردن پنجره انتخاب فایل و ذخیره مسیر """
    global selected_filepath
    filepath = filedialog.askopenfilename()
    if filepath:
        selected_filepath = filepath
        file_var.set(os.path.basename(filepath))
        update_status_safe(status_area, f"فایل انتخاب شده: {filepath}")
        send_button.config(state=tk.NORMAL if not server_running else tk.DISABLED)
        buffer_size_combobox.config(state='readonly')
        print(f"DEBUG: File selected: {selected_filepath}")
    else:
        send_button.config(state=tk.DISABLED)
        buffer_size_combobox.config(state=tk.DISABLED)
        print("DEBUG: File selection cancelled")


def start_server_ui():
    """ شروع عملیات سرور در نخ‌های جداگانه """
    print("DEBUG: start_server_ui called")
    global server_thread, discovery_thread, server_running, active_server_port
    if server_running:
        messagebox.showwarning("هشدار", "سرور از قبل در حال اجرا است.")
        print("DEBUG: Server already running, start_server_ui ignored")
        return

    status_area.delete('1.0', tk.END)
    update_status_safe(status_area, "--- شروع حالت سرور (گیرنده) ---")
    print("DEBUG: Starting server mode")

    server_stop_event.clear()
    discovery_stop_event.clear()
    cancel_transfer_event.clear()
    server_running = True
    active_server_port = None # پورت فعال را قبل از شروع ریست می کند


    # ترد اصلی سرور TCP (که Bind روی پورت را انجام می دهد) زودتر شروع شود
    server_thread = threading.Thread(target=run_tcp_server_task, args=(server_stop_event, status_area, progress_bar, speed_var, cancel_button, root), daemon=True)
    server_thread.start()
    print("DEBUG: TCP server thread started")

    # کمی صبر کنید تا نخ سرور TCP فرصت Bind کردن روی یک پورت را پیدا کند
    # این لازم است تا پورت فعال قبل از شروع نخ کشف مشخص شود و در پاسخ کشف قرار گیرد.
    # می توانید از یک Event یا Condition Variable برای سیگنال دهی Bind موفق استفاده کنید
    # یا برای سادگی، کمی صبر کنید.
    timeout_start = time.time()
    while active_server_port is None and server_running and (time.time() - timeout_start < 10): # حداکثر 10 ثانیه صبر
         if server_stop_event.is_set(): break
         time.sleep(0.1) # صبر کوتاه

    if active_server_port is None:
        # اگر بعد از صبر هم پورتی فعال نشد، احتمالا خطایی رخ داده که در run_tcp_server_task لاگ شده
        # و server_running به False تغییر کرده است.
        print("DEBUG: TCP server failed to bind to any port within timeout.")
        # پیام خطا و وضعیت دکمه ها توسط run_tcp_server_task مدیریت شده است
        return # از start_server_ui خارج می شود


    # اگر پورت فعال پیدا شد، نخ شنونده کشف را شروع می کند
    discovery_thread = threading.Thread(target=listen_for_discovery_task, args=(discovery_stop_event, status_area, root), daemon=True)
    discovery_thread.start()
    print("DEBUG: Discovery thread started")


    update_server_button_state()
    select_file_button.config(state=tk.DISABLED)
    buffer_size_combobox.config(state=tk.DISABLED)
    send_button.config(state=tk.DISABLED)
    cancel_button.config(state=tk.DISABLED)
    print("DEBUG: start_server_ui finished")


def stop_server_ui():
    """ متوقف کردن نخ‌های سرور """
    print("DEBUG: stop_server_ui called")
    global server_running, active_server_port
    if not server_running:
        update_status_safe(status_area, "[*] سرور در حال حاضر در حال اجرا نیست.")
        print("DEBUG: Server not running, stop_server_ui ignored")
        return

    update_status_safe(status_area, "[*] در حال متوقف کردن سرور...")
    print("DEBUG: Stopping server mode")

    discovery_stop_event.set()
    server_stop_event.set()
    cancel_transfer_event.set()

    # بستن احتمالی سوکت ها اگر هنوز باز باشند (اختیاری، نخ ها باید خودشان ببندند)
    # اگر سرور TCP با موفقیت Bind شده باشد، active_server_port مقدار دارد.
    # یک راه برای تلاش برای آزاد کردن پورت TCP فورا، ایجاد یک اتصال کوتاه به آن است
    # این ممکن است نخ accept را از بلاک خارج کند.
    if active_server_port is not None:
        try:
            print(f"DEBUG: Attempting to connect to localhost:{active_server_port} to unblock accept")
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(('127.0.0.1', active_server_port))
        except Exception:
            pass # اتصال ممکن است ناموفق باشد، مهم نیست

    server_running = False
    update_server_button_state()
    update_progress_safe(progress_bar, 0)
    update_speed_safe(speed_var, "Speed: Stopping...")


def update_server_button_state():
    """ فعال/غیرفعال کردن دکمه‌های سرور و کلاینت بر اساس وضعیت """
    root.after(0, _update_server_button_state_safe)

def _update_server_button_state_safe():
    #print(f"DEBUG: Updating button state. server_running: {server_running}, selected_filepath: {selected_filepath}")
    if server_running:
        start_server_button.config(state=tk.DISABLED)
        stop_server_button.config(state=tk.NORMAL)
        select_file_button.config(state=tk.DISABLED)
        buffer_size_combobox.config(state=tk.DISABLED)
        send_button.config(state=tk.DISABLED)
        cancel_button.config(state=tk.DISABLED)
    else:
        start_server_button.config(state=tk.NORMAL)
        stop_server_button.config(state=tk.DISABLED)
        select_file_button.config(state=tk.NORMAL)
        buffer_size_combobox.config(state='readonly' if selected_filepath else tk.DISABLED)
        send_button.config(state=tk.NORMAL if selected_filepath else tk.DISABLED)
        cancel_button.config(state=tk.DISABLED)


def send_file_ui():
    """ شروع عملیات کلاینت (کشف و ارسال) در نخ جداگانه """
    print("DEBUG: send_file_ui called")
    if server_running:
        messagebox.showerror("خطا", "نمی‌توانید فایل ارسال کنید وقتی برنامه در حالت سرور است.")
        print("DEBUG: Server is running, cannot send file")
        return

    if not selected_filepath:
        messagebox.showerror("خطا", "لطفاً ابتدا یک فایل برای ارسال انتخاب کنید.")
        print("DEBUG: No file selected, cannot send")
        return

    selected_buffer_option = buffer_size_var.get()
    try:
        file_size = os.path.getsize(selected_filepath)
        chosen_buffer_size = get_buffer_size(selected_buffer_option, file_size)
        update_status_safe(status_area, f"[*] اندازه بافر انتخاب شده برای ارسال: {format_bytes(chosen_buffer_size)}")
        print(f"DEBUG: Chosen buffer size: {chosen_buffer_size} ({selected_buffer_option}) for file size {file_size}")
    except Exception as e:
        messagebox.showerror("خطای فایل", f"خطا در خواندن حجم فایل: {e}")
        print(f"DEBUG: Error getting file size: {e}")
        return

    status_area.delete('1.0', tk.END)
    update_status_safe(status_area, "--- شروع حالت کلاینت (فرستنده) ---")
    print("DEBUG: Starting client mode")

    send_button.config(state=tk.DISABLED)
    select_file_button.config(state=tk.DISABLED)
    buffer_size_combobox.config(state=tk.DISABLED)
    cancel_button.config(state=tk.NORMAL)

    update_progress_safe(progress_bar, 0)
    update_speed_safe(speed_var, "Speed: Searching for server...")
    cancel_transfer_event.clear()

    client_thread = threading.Thread(
        target=discover_and_send_wrapper,
        args=(selected_filepath, status_area, progress_bar, speed_var, cancel_button, root, send_button, select_file_button, buffer_size_combobox, chosen_buffer_size),
        daemon=True
    )
    client_thread.start()
    print("DEBUG: Client thread (discover_and_send_wrapper) started")


def discover_and_send_wrapper(filepath, status_area, progress_bar, speed_var, cancel_button, root, send_button, select_file_button, buffer_size_combobox, chosen_buffer_size):
    """ تابعی که ابتدا کشف و سپس ارسال را در یک نخ انجام می‌دهد """
    print("DEBUG: discover_and_send_wrapper started")

    # این بلاک finally مطمئن می شود که دکمه های کلاینت در پایان این تابع (موفقیت، خطا، یا لغو) به حالت صحیح برگردانده می شوند
    try:
        root.after(0, lambda: update_status_safe(status_area, "[*] در حال جستجوی سرور در شبکه..."))
        print("DEBUG: Started searching for server...")

        if cancel_transfer_event.is_set():
             root.after(0, lambda: update_status_safe(status_area, "[*] عملیات ارسال توسط کاربر قبل از کشف لغو شد."))
             print("DEBUG: Cancellation requested before discovery")
             return

        server_info = discover_server_task(status_area)
        print(f"DEBUG: discover_server_task returned: {server_info}")

        if cancel_transfer_event.is_set():
             root.after(0, lambda: update_status_safe(status_area, "[*] عملیات ارسال توسط کاربر پس از کشف لغو شد."))
             print("DEBUG: Cancellation requested after discovery")
             return


        if server_info:
            server_ip, server_port = server_info
            root.after(0, lambda ip=server_ip, p=server_port: update_status_safe(status_area, f"[+] سرور پیدا شد در {ip}:{p}"))
            print(f"DEBUG: Server found at {server_ip}:{server_port}. Proceeding to send file.")
            # ارسال فایل با اندازه بافر انتخابی - دکمه لغو و وضعیت های پایان از اینجا مدیریت می شود
            send_file_task(server_ip, server_port, filepath, status_area, progress_bar, speed_var, cancel_button, root, send_button, chosen_buffer_size)
        else:
            root.after(0, lambda: update_status_safe(status_area, "[!] هیچ سرور گیرنده‌ای در شبکه پیدا نشد."))
            root.after(0, lambda: messagebox.showerror("خطا", "هیچ سرور گیرنده‌ای در شبکه پیدا نشد.\nمطمئن شوید برنامه گیرنده در دستگاه دیگری اجرا شده و فایروال مسدود نکرده است."))
            root.after(0, lambda: update_speed_safe(speed_var, "Speed: Discovery Failed"))
            print("DEBUG: No server found after discovery timeout.")

    except Exception as e:
        print(f"DEBUG: UNCAUGHT EXCEPTION IN discover_and_send_wrapper: {e}", file=sys.stderr)
        root.after(0, lambda err=e: update_status_safe(status_area, f"[!] خطای غیرمنتظره در شروع کلاینت: {err}"))
        root.after(0, lambda err=e: messagebox.showerror("خطای کلاینت", f"خطای غیرمنتظره در شروع عملیات ارسال:\n{err}"))
        root.after(0, lambda: update_speed_safe(speed_var, "Speed: Error"))

    finally:
        # اطمینان از بازگشت دکمه ها به حالت عادی پس از اتمام نخ کلاینت
        print("DEBUG: discover_and_send_wrapper finally block entered")
        root.after(0, lambda: send_button.config(state=tk.NORMAL if selected_filepath else tk.DISABLED))
        root.after(0, lambda: select_file_button.config(state=tk.NORMAL))
        root.after(0, lambda: buffer_size_combobox.config(state='readonly' if selected_filepath else tk.DISABLED))
        root.after(0, lambda: cancel_button.config(state=tk.DISABLED))
        # Note: progress_bar and speed_var are handled in send_file_task's finally block
        cancel_transfer_event.clear()
        print("DEBUG: discover_and_send_wrapper finished")


def cancel_transfer_ui():
    """ تنظیم رویداد لغو برای توقف انتقال در حال انجام """
    update_status_safe(status_area, "[*] درخواست لغو انتقال...")
    print("DEBUG: Cancel button pressed. Setting cancel_transfer_event.")
    cancel_transfer_event.set()


def on_closing():
    """ هنگام بستن پنجره، سرور را (اگر در حال اجراست) متوقف کن """
    print("DEBUG: on_closing called")
    global server_running
    if server_running:
        print("DEBUG: Server is running, stopping server...")
        stop_server_ui()
        time.sleep(0.5)
    else:
        if cancel_button.cget('state') == tk.NORMAL:
             print("DEBUG: Client transfer in progress, setting cancel_transfer_event...")
             cancel_transfer_event.set()
             time.sleep(0.5)

    root.destroy()
    print("DEBUG: root.destroy() called")


# --- ساخت رابط کاربری ---
root = tk.Tk()
root.title("ارسال و دریافت فایل در شبکه")
root.geometry("600x510")

style = ttk.Style()
try:
    style.theme_use('vista')
except tk.TclError:
    pass

# --- فریم برای دکمه های سرور ---
server_frame = ttk.LabelFrame(root, text="حالت دریافت کننده (سرور)", padding="10")
server_frame.pack(fill=tk.X, padx=10, pady=5)

start_server_button = ttk.Button(server_frame, text="شروع دریافت", command=start_server_ui)
start_server_button.pack(side=tk.LEFT, padx=5)

stop_server_button = ttk.Button(server_frame, text="توقف دریافت", command=stop_server_ui, state=tk.DISABLED)
stop_server_button.pack(side=tk.LEFT, padx=5)

# --- فریم برای انتخاب و ارسال فایل ---
client_frame = ttk.LabelFrame(root, text="حالت ارسال کننده (کلاینت)", padding="10")
client_frame.pack(fill=tk.X, padx=10, pady=5)

select_file_button = ttk.Button(client_frame, text="1. انتخاب فایل", command=select_file)
select_file_button.pack(side=tk.LEFT, padx=5)

# تعریف متغیرهای Tkinter بعد از root = tk.Tk()
file_var = tk.StringVar(value="هنوز فایلی انتخاب نشده")
file_label = ttk.Entry(client_frame, textvariable=file_var, state='readonly', width=30)
file_label.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

# فریم برای انتخاب بافر
buffer_frame = ttk.Frame(client_frame)
buffer_frame.pack(side=tk.LEFT, padx=5)

buffer_label = ttk.Label(buffer_frame, text="Buffer:")
buffer_label.pack(side=tk.LEFT)

# تعریف buffer_size_var در اینجاست
buffer_size_var = tk.StringVar(value="Auto")

buffer_size_combobox = ttk.Combobox(buffer_frame, textvariable=buffer_size_var, state=tk.DISABLED, width=12)
buffer_size_combobox['values'] = list(BUFFER_OPTIONS.keys())
buffer_size_combobox.current(0)
buffer_size_combobox.pack(side=tk.LEFT)


send_button = ttk.Button(client_frame, text="2. ارسال فایل", command=send_file_ui, state=tk.DISABLED)
send_button.pack(side=tk.LEFT, padx=5)

# --- نوار پیشرفت، نمایش سرعت و دکمه لغو ---
progress_speed_cancel_frame = ttk.Frame(root)
progress_speed_cancel_frame.pack(pady=10, padx=10, fill=tk.X)

progress_bar = ttk.Progressbar(progress_speed_cancel_frame, orient="horizontal", mode="determinate")
progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

# تعریف speed_var در اینجاست
speed_var = tk.StringVar(value="Speed: N/A")
speed_label = ttk.Label(progress_speed_cancel_frame, textvariable=speed_var)
speed_label.pack(side=tk.LEFT, padx=(0, 10))

cancel_button = ttk.Button(progress_speed_cancel_frame, text="لغو", command=cancel_transfer_ui, state=tk.DISABLED)
cancel_button.pack(side=tk.LEFT)


# --- ناحیه نمایش وضعیت ---
status_frame = ttk.Frame(root)
status_frame.pack(pady=5, padx=10, fill=tk.BOTH, expand=True)

status_label = ttk.Label(status_frame, text="وضعیت عملیات:")
status_label.pack(anchor='w')

status_area = scrolledtext.ScrolledText(status_frame, wrap=tk.WORD, height=8, state=tk.NORMAL)
status_area.pack(fill=tk.BOTH, expand=True)


# --- تنظیم وضعیت اولیه دکمه ها ---
update_server_button_state()

# --- اتصال تابع on_closing به رویداد بستن پنجره ---
root.protocol("WM_DELETE_WINDOW", on_closing)

# --- شروع حلقه اصلی GUI ---
root.mainloop()