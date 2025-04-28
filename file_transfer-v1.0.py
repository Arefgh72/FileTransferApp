# file_transfer_gui.py
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import socket
import os
import threading
import time

# --- تنظیمات ---
DISCOVERY_PORT = 5000       # پورت برای کشف UDP
FILE_TRANSFER_PORT = 5001   # پورت برای انتقال فایل TCP
BUFFER_SIZE = 4096
SEPARATOR = "<SEPARATOR>"
DISCOVERY_MESSAGE = "FIND_FILE_SERVER_XYZ" # پیام خاص برای کشف
SERVER_RESPONSE = f"IM_FILE_SERVER_XYZ {FILE_TRANSFER_PORT}" # پاسخ سرور با پورت TCP
DISCOVERY_TIMEOUT = 5 # چند ثانیه منتظر پاسخ سرور بماند

# --- متغیرهای سراسری برای وضعیت GUI و شبکه ---
server_thread = None
discovery_thread = None
server_stop_event = threading.Event()
discovery_stop_event = threading.Event()
selected_filepath = ""
server_running = False # برای پیگیری وضعیت سرور

# --- توابع هسته شبکه (با کمی تغییر برای آپدیت GUI) ---

def update_status_safe(status_area, message):
    """ به صورت امن متن را در ناحیه وضعیت از هر نخی آپدیت می‌کند """
    if status_area.winfo_exists(): # چک کردن وجود ویجت قبل از آپدیت
        status_area.insert(tk.END, message + "\n")
        status_area.see(tk.END) # اسکرول به پایین

def update_progress_safe(progress_bar, value):
    """ به صورت امن نوار پیشرفت را از هر نخی آپدیت می‌کند """
    if progress_bar.winfo_exists(): # چک کردن وجود ویجت قبل از آپدیت
        progress_bar['value'] = value

def handle_client_connection(client_socket, address, status_area, progress_bar, root):
    """ مدیریت اتصال کلاینت و دریافت فایل (آپدیت GUI) """
    update_status_safe(status_area, f"[+] اتصال جدید از {address} برای دریافت فایل")
    try:
        received = client_socket.recv(BUFFER_SIZE).decode()
        if not received or SEPARATOR not in received:
             update_status_safe(status_area, f"[!] اطلاعات فایل نامعتبر از {address}")
             return

        filename, filesize_str = received.split(SEPARATOR)
        filename = os.path.basename(filename) # امنیت: فقط نام فایل
        filesize = int(filesize_str)

        update_status_safe(status_area, f"[*] شروع دریافت: {filename} ({filesize} بایت) از {address}")
        # استفاده از after برای اجرای آپدیت در نخ اصلی GUI
        root.after(0, lambda: update_progress_safe(progress_bar, 0)) # ریست کردن پروگرس بار

        received_bytes = 0
        save_dir = "received_files"
        if not os.path.exists(save_dir):
            try:
                os.makedirs(save_dir)
            except OSError as e:
                 update_status_safe(status_area, f"[!] خطا در ایجاد پوشه '{save_dir}': {e}")
                 root.after(0, lambda e=e: messagebox.showerror("خطای پوشه", f"خطا در ایجاد پوشه '{save_dir}':\n{e}"))
                 return # ادامه ممکن نیست

        file_path = os.path.join(save_dir, filename)

        with open(file_path, "wb") as f:
            while received_bytes < filesize:
                bytes_read = client_socket.recv(BUFFER_SIZE)
                if not bytes_read:
                    break # اتصال قطع شد
                f.write(bytes_read)
                received_bytes += len(bytes_read)
                progress = (received_bytes / filesize) * 100 if filesize > 0 else 0
                root.after(0, lambda p=progress: update_progress_safe(progress_bar, p))

        if received_bytes == filesize:
             update_status_safe(status_area, f"[+] فایل '{filename}' با موفقیت در پوشه '{save_dir}' دریافت شد.")
             root.after(0, lambda f=filename: messagebox.showinfo("موفقیت", f"فایل '{f}' با موفقیت دریافت شد."))
        else:
             update_status_safe(status_area, f"[!] دریافت فایل '{filename}' ناقص ماند ({received_bytes}/{filesize} بایت).")
             root.after(0, lambda f=filename: messagebox.showwarning("هشدار", f"دریافت فایل '{f}' ناقص بود."))

    except Exception as e:
        update_status_safe(status_area, f"[!] خطایی در ارتباط با {address} رخ داد: {e}")
        root.after(0, lambda addr=address, err=e: messagebox.showerror("خطای دریافت", f"خطا در دریافت فایل از {addr}:\n{err}"))
    finally:
        client_socket.close()
        update_status_safe(status_area, f"[-] اتصال {address} بسته شد.")
        root.after(0, lambda: update_progress_safe(progress_bar, 0)) # ریست پروگرس بار در انتها

def listen_for_discovery_task(stop_event, status_area, root):
    """ وظیفه گوش دادن به پیام‌های کشف UDP در یک نخ """
    global server_running # برای آپدیت در صورت خطای مرگبار
    udp_socket = None
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.bind(("", DISCOVERY_PORT))
        root.after(0, lambda: update_status_safe(status_area, f"[*] سرور کشف در حال گوش دادن روی UDP پورت {DISCOVERY_PORT}"))

        while not stop_event.is_set():
            try:
                udp_socket.settimeout(1.0) # برای چک کردن stop_event
                message, client_address = udp_socket.recvfrom(BUFFER_SIZE)
                message = message.decode()
                if message == DISCOVERY_MESSAGE:
                     # از after برای آپدیت GUI در نخ اصلی استفاده کن
                     root.after(0, lambda addr=client_address: update_status_safe(status_area, f"[+] پیام کشف از {addr} دریافت شد. در حال ارسال پاسخ..."))
                     udp_socket.sendto(SERVER_RESPONSE.encode(), client_address)
            except socket.timeout:
                continue
            except Exception as e:
                 # فقط لاگ خطا، سرور کشف باید ادامه دهد (اگر ممکن است)
                 root.after(0, lambda err=e: update_status_safe(status_area, f"[!] خطای UDP Discovery: {err}"))
                 time.sleep(1) # جلوگیری از حلقه خطای سریع

    except OSError as e:
        # خطای رایج: آدرس از قبل استفاده شده
        if e.errno == 98 or e.errno == 10048: # Address already in use (Linux/Windows)
            error_msg = f"[!] خطا: پورت UDP {DISCOVERY_PORT} در حال استفاده است."
            root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
            root.after(0, lambda: messagebox.showerror("خطای سرور کشف", f"پورت UDP {DISCOVERY_PORT} توسط برنامه دیگری در حال استفاده است."))
        else:
            error_msg = f"[!] خطای مرگبار در سرور کشف UDP: {e}"
            root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
            root.after(0, lambda err=e: messagebox.showerror("خطای سرور کشف", f"خطای راه اندازی سرور کشف UDP:\n{err}\nلطفا برنامه را ری‌استارت کنید."))
        # در صورت بروز خطا، سرور را متوقف اعلام کن
        server_running = False
        root.after(0, update_server_button_state) # آپدیت دکمه ها در GUI
    except Exception as e:
        error_msg = f"[!] خطای مرگبار ناشناخته در سرور کشف UDP: {e}"
        root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
        root.after(0, lambda err=e: messagebox.showerror("خطای سرور کشف", f"خطای ناشناخته سرور کشف UDP:\n{err}\nلطفا برنامه را ری‌استارت کنید."))
        server_running = False
        root.after(0, update_server_button_state)
    finally:
        if udp_socket:
            udp_socket.close()
        root.after(0, lambda: update_status_safe(status_area, "[-] ترد شنونده کشف متوقف شد."))


def run_tcp_server_task(stop_event, status_area, progress_bar, root):
    """ وظیفه اصلی سرور TCP برای پذیرش اتصالات در یک نخ """
    global server_running
    tcp_socket = None
    try:
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp_socket.bind(("0.0.0.0", FILE_TRANSFER_PORT))
        tcp_socket.listen(5)
        tcp_socket.settimeout(1.0) # تایم‌اوت برای accept تا بتواند stop_event را چک کند
        root.after(0, lambda: update_status_safe(status_area, f"[*] سرور انتقال فایل در حال گوش دادن روی TCP پورت {FILE_TRANSFER_PORT}"))
        root.after(0, lambda: update_status_safe(status_area, "[*] منتظر دریافت اتصال برای انتقال فایل..."))

        while not stop_event.is_set():
            try:
                client_socket, address = tcp_socket.accept()
                 # برای هر کلاینت یک نخ جدید ایجاد کن
                client_handler_thread = threading.Thread(
                    target=handle_client_connection,
                    args=(client_socket, address, status_area, progress_bar, root),
                    daemon=True
                )
                client_handler_thread.start()
            except socket.timeout:
                 continue # طبیعی است، برای چک کردن stop_event
            except Exception as e:
                 # فقط اگر خودمان متوقف نکرده‌ایم خطا را نشان بده
                 if not stop_event.is_set() and server_running:
                     root.after(0, lambda err=e: update_status_safe(status_area, f"[!] خطای پذیرش اتصال TCP: {err}"))

    except OSError as e:
         if e.errno == 98 or e.errno == 10048: # Address already in use
             error_msg = f"[!] خطا: پورت TCP {FILE_TRANSFER_PORT} در حال استفاده است."
             root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
             root.after(0, lambda: messagebox.showerror("خطای سرور", f"پورت TCP {FILE_TRANSFER_PORT} توسط برنامه دیگری در حال استفاده است."))
         else:
            error_msg = f"[!] خطای راه اندازی سرور TCP: {e}"
            root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
            root.after(0, lambda err=e: messagebox.showerror("خطای سرور", f"خطای راه اندازی سرور TCP:\n{err}"))
         server_running = False # اینجا هم در صورت خطا، وضعیت را آپدیت کن
         root.after(0, update_server_button_state) # آپدیت دکمه ها
    except Exception as e:
        error_msg = f"[!] خطای مرگبار سرور TCP: {e}"
        root.after(0, lambda msg=error_msg: update_status_safe(status_area, msg))
        root.after(0, lambda err=e: messagebox.showerror("خطای سرور", f"خطای ناشناخته سرور TCP:\n{err}"))
        server_running = False
        root.after(0, update_server_button_state)
    finally:
        if tcp_socket:
            tcp_socket.close()
        root.after(0, lambda: update_status_safe(status_area, "[-] سوکت اصلی TCP سرور بسته شد."))
        # اطمینان از اینکه وضعیت سرور در GUI آپدیت می‌شود، حتی اگر قبلا آپدیت شده باشد
        root.after(0, update_server_button_state)

def discover_server_task():
    """ تلاش برای پیدا کردن سرور در شبکه (برگرداندن اطلاعات سرور یا None) """
    udp_socket = None
    server_info = None # (ip, port)
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_socket.settimeout(DISCOVERY_TIMEOUT)

        udp_socket.sendto(DISCOVERY_MESSAGE.encode(), ('<broadcast>', DISCOVERY_PORT))
        start_time = time.time()
        while time.time() - start_time < DISCOVERY_TIMEOUT:
             try:
                 response, server_address = udp_socket.recvfrom(BUFFER_SIZE)
                 response = response.decode()
                 if response.startswith("IM_FILE_SERVER_XYZ"):
                     parts = response.split()
                     if len(parts) == 2:
                         try:
                             server_ip = server_address[0]
                             server_tcp_port = int(parts[1])
                             server_info = (server_ip, server_tcp_port)
                             break # اولین پاسخ را گرفتیم
                         except (ValueError, IndexError):
                             pass # پیام نامعتبر
             except socket.timeout:
                 pass # ادامه تا تایم اوت کلی
             except Exception:
                 pass # خطاهای دیگر دریافت
    except Exception as e:
        # این خطا را فقط در کنسول لاگ می‌کنیم، نه در GUI اصلی
        print(f"[!] خطای ارسال/دریافت کشف UDP: {e}")
    finally:
        if udp_socket:
            udp_socket.close()
    return server_info

def send_file_task(server_host, server_port, filename, status_area, progress_bar, root, send_button):
    """ وظیفه ارسال فایل در یک نخ """
    filesize = 0
    try:
        filesize = os.path.getsize(filename)
    except Exception as e:
         # آپدیت GUI با استفاده از after
         root.after(0, lambda err=e: update_status_safe(status_area, f"[!] خطا در خواندن فایل: {err}"))
         root.after(0, lambda err=e, f=os.path.basename(filename): messagebox.showerror("خطای فایل", f"فایل '{f}' قابل خواندن نیست:\n{err}"))
         root.after(0, lambda: send_button.config(state=tk.NORMAL if selected_filepath else tk.DISABLED)) # فعال کردن مجدد دکمه ارسال (با شرط انتخاب فایل)
         return

    file_basename = os.path.basename(filename)
    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        root.after(0, lambda: update_status_safe(status_area, f"[+] در حال اتصال به سرور در {server_host}:{server_port}..."))
        tcp_socket.connect((server_host, server_port))
        root.after(0, lambda: update_status_safe(status_area, "[+] اتصال برقرار شد."))
        root.after(0, lambda: update_progress_safe(progress_bar, 0)) # ریست

        header = f"{file_basename}{SEPARATOR}{filesize}"
        tcp_socket.send(header.encode())
        root.after(0, lambda: update_status_safe(status_area, f"[*] در حال ارسال: {file_basename} ({filesize} بایت)"))

        sent_bytes = 0
        with open(filename, "rb") as f:
            while sent_bytes < filesize:
                bytes_read = f.read(BUFFER_SIZE)
                if not bytes_read:
                    break
                tcp_socket.sendall(bytes_read)
                sent_bytes += len(bytes_read)
                progress = (sent_bytes / filesize) * 100 if filesize > 0 else 0
                root.after(0, lambda p=progress: update_progress_safe(progress_bar, p))

        if sent_bytes == filesize:
            root.after(0, lambda f=file_basename: update_status_safe(status_area, f"[+] فایل '{f}' با موفقیت ارسال شد."))
            root.after(0, lambda f=file_basename: messagebox.showinfo("موفقیت", f"فایل '{f}' با موفقیت ارسال شد."))
        else:
             root.after(0, lambda f=file_basename, sb=sent_bytes, fs=filesize: update_status_safe(status_area, f"[!] ارسال فایل '{f}' ناقص ماند ({sb}/{fs} بایت)."))
             root.after(0, lambda f=file_basename: messagebox.showwarning("هشدار", f"ارسال فایل '{f}' ناقص بود."))

    except ConnectionRefusedError:
        msg = f"[!] خطا: اتصال به {server_host}:{server_port} رد شد. آیا سرور هنوز فعال است؟"
        root.after(0, lambda m=msg: update_status_safe(status_area, m))
        root.after(0, lambda h=server_host, p=server_port: messagebox.showerror("خطای اتصال", f"سرور در {h}:{p} اتصال را رد کرد.\nممکن است سرور متوقف شده باشد."))
    except socket.timeout:
        msg = "[!] خطا: زمان انتظار برای اتصال به سرور تمام شد."
        root.after(0, lambda m=msg: update_status_safe(status_area, m))
        root.after(0, lambda: messagebox.showerror("خطای اتصال", "زمان انتظار برای اتصال به سرور تمام شد."))
    except Exception as e:
        msg = f"[!] خطای ارسال فایل: {e}"
        root.after(0, lambda m=msg: update_status_safe(status_area, m))
        root.after(0, lambda err=e: messagebox.showerror("خطای ارسال", f"خطایی در هنگام ارسال فایل رخ داد:\n{err}"))
    finally:
        tcp_socket.close()
        root.after(0, lambda: update_status_safe(status_area, "[-] اتصال TCP کلاینت بسته شد."))
        root.after(0, lambda: update_progress_safe(progress_bar, 0)) # ریست
        root.after(0, lambda: send_button.config(state=tk.NORMAL if selected_filepath else tk.DISABLED)) # فعال کردن مجدد دکمه ارسال

# --- توابع مربوط به GUI ---

def select_file():
    """ باز کردن پنجره انتخاب فایل و ذخیره مسیر """
    global selected_filepath
    filepath = filedialog.askopenfilename()
    if filepath:
        selected_filepath = filepath
        # نمایش فقط نام فایل در لیبل
        file_label.config(text=os.path.basename(filepath))
        update_status_safe(status_area, f"فایل انتخاب شده: {filepath}")
        send_button.config(state=tk.NORMAL if not server_running else tk.DISABLED) # فعال کردن دکمه ارسال اگر سرور خاموش است
    else:
        # اگر کاربر فایلی انتخاب نکرد یا کنسل کرد
        # selected_filepath = "" # نیازی نیست چون قبلا خالی بوده یا مقدار داشته
        # file_label.config(text="هنوز فایلی انتخاب نشده") # شاید بهتر باشد لیبل قبلی بماند
        send_button.config(state=tk.DISABLED) # غیرفعال کردن دکمه ارسال

def start_server_ui():
    """ شروع عملیات سرور در نخ‌های جداگانه """
    global server_thread, discovery_thread, server_running
    if server_running:
        messagebox.showwarning("هشدار", "سرور از قبل در حال اجرا است.")
        return

    # پاک کردن وضعیت قبل از شروع
    status_area.delete('1.0', tk.END)
    update_status_safe(status_area, "--- شروع حالت سرور (گیرنده) ---")

    server_stop_event.clear()
    discovery_stop_event.clear()
    server_running = True # بلافاصله وضعیت را آپدیت کن

    # شروع نخ شنونده کشف
    discovery_thread = threading.Thread(target=listen_for_discovery_task, args=(discovery_stop_event, status_area, root), daemon=True)
    discovery_thread.start()

    # شروع نخ اصلی سرور TCP (با تاخیر کم برای اطمینان از شروع کشف)
    # time.sleep(0.1) # شاید لازم نباشد
    server_thread = threading.Thread(target=run_tcp_server_task, args=(server_stop_event, status_area, progress_bar, root), daemon=True)
    server_thread.start()

    update_server_button_state()

def stop_server_ui():
    """ متوقف کردن نخ‌های سرور """
    global server_running
    if not server_running:
        # اگر از قبل متوقف شده (مثلا به خاطر خطا)، فقط پیام بده
        update_status_safe(status_area, "[*] سرور در حال حاضر در حال اجرا نیست.")
        return

    update_status_safe(status_area, "[*] در حال متوقف کردن سرور...")

    discovery_stop_event.set()
    server_stop_event.set()

    server_running = False
    # منتظر ماندن برای نخ‌ها ممکن است GUI را قفل کند، به daemon=True اکتفا می‌کنیم
    # if discovery_thread and discovery_thread.is_alive():
    #     discovery_thread.join(timeout=1.5)
    # if server_thread and server_thread.is_alive():
    #     server_thread.join(timeout=1.5)

    update_server_button_state()
    update_status_safe(status_area, "[*] سرور متوقف شد.")
    update_progress_safe(progress_bar, 0) # ریست پروگرس بار

def update_server_button_state():
    """ فعال/غیرفعال کردن دکمه‌های سرور و کلاینت بر اساس وضعیت """
    if server_running:
        start_server_button.config(state=tk.DISABLED)
        stop_server_button.config(state=tk.NORMAL)
        select_file_button.config(state=tk.DISABLED)
        send_button.config(state=tk.DISABLED)
    else:
        start_server_button.config(state=tk.NORMAL)
        stop_server_button.config(state=tk.DISABLED)
        select_file_button.config(state=tk.NORMAL)
        # دکمه ارسال فقط اگر فایلی انتخاب شده فعال باشد
        send_button.config(state=tk.NORMAL if selected_filepath else tk.DISABLED)

def send_file_ui():
    """ شروع عملیات کلاینت (کشف و ارسال) در نخ جداگانه """
    if server_running:
        messagebox.showerror("خطا", "نمی‌توانید فایل ارسال کنید وقتی برنامه در حالت سرور است.")
        return

    if not selected_filepath:
        messagebox.showerror("خطا", "لطفاً ابتدا یک فایل برای ارسال انتخاب کنید.")
        return

    # پاک کردن وضعیت قبل از شروع ارسال
    status_area.delete('1.0', tk.END)
    update_status_safe(status_area, "--- شروع حالت کلاینت (فرستنده) ---")
    send_button.config(state=tk.DISABLED) # غیرفعال کردن دکمه ارسال تا عملیات تمام شود
    update_progress_safe(progress_bar, 0) # ریست

    # اجرای کشف و ارسال در یک نخ جدید
    client_thread = threading.Thread(target=discover_and_send_wrapper, args=(selected_filepath, status_area, progress_bar, root, send_button), daemon=True)
    client_thread.start()

def discover_and_send_wrapper(filepath, status_area, progress_bar, root, send_button):
    """ تابعی که ابتدا کشف و سپس ارسال را در یک نخ انجام می‌دهد """
    root.after(0, lambda: update_status_safe(status_area, "[*] در حال جستجوی سرور در شبکه..."))
    server_info = discover_server_task() # این تابع خودش تایم‌اوت دارد

    if server_info:
        server_ip, server_port = server_info
        root.after(0, lambda ip=server_ip, p=server_port: update_status_safe(status_area, f"[+] سرور پیدا شد در {ip}:{p}"))
        # ارسال فایل
        send_file_task(server_ip, server_port, filepath, status_area, progress_bar, root, send_button)
    else:
        root.after(0, lambda: update_status_safe(status_area, "[!] هیچ سروری در شبکه پیدا نشد."))
        root.after(0, lambda: messagebox.showerror("خطا", "هیچ سرور گیرنده‌ای در شبکه پیدا نشد.\nمطمئن شوید برنامه گیرنده در دستگاه دیگری اجرا شده و فایروال مسدود نکرده است."))
        # فعال کردن مجدد دکمه ارسال چون عملیات تمام شد (و ناموفق بود)
        root.after(0, lambda: send_button.config(state=tk.NORMAL if selected_filepath else tk.DISABLED))

def on_closing():
    """ هنگام بستن پنجره، سرور را (اگر در حال اجراست) متوقف کن """
    global server_running, discovery_stop_event, server_stop_event
    if server_running:
        print("پنجره بسته شد، در حال متوقف کردن سرور...") # لاگ کنسول
        discovery_stop_event.set()
        server_stop_event.set()
        server_running = False
        # کمی صبر برای بسته شدن سوکت‌ها قبل از نابود کردن پنجره
        time.sleep(0.2)
    root.destroy()

# --- ساخت رابط کاربری ---
root = tk.Tk()
root.title("ارسال و دریافت فایل در شبکه")
root.geometry("600x450") # اندازه اولیه پنجره

# استایل برای ظاهر بهتر (اختیاری)
style = ttk.Style()
try:
    # سعی کن از تم‌های موجود در سیستم استفاده کنی
    style.theme_use('vista') # یا 'clam', 'alt', 'default', 'classic', 'xpnative'
except tk.TclError:
    print("تم 'vista' یافت نشد، از تم پیش‌فرض استفاده می‌شود.")

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

# استفاده از Entry به جای Label برای نمایش بهتر مسیرهای طولانی (فقط خواندنی)
file_var = tk.StringVar(value="هنوز فایلی انتخاب نشده")
file_label = ttk.Entry(client_frame, textvariable=file_var, state='readonly', width=40)
# تعریف مجدد تابع select_file برای آپدیت Entry
def select_file():
    global selected_filepath
    filepath = filedialog.askopenfilename()
    if filepath:
        selected_filepath = filepath
        file_var.set(os.path.basename(filepath)) # نمایش نام فایل در Entry
        update_status_safe(status_area, f"فایل انتخاب شده: {filepath}")
        send_button.config(state=tk.NORMAL if not server_running else tk.DISABLED)
    else:
        # اگر فایلی انتخاب نشد، دکمه ارسال غیرفعال شود
        # file_var.set("هنوز فایلی انتخاب نشده") # لیبل قبلی بماند بهتر است
        send_button.config(state=tk.DISABLED)
file_label.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)


send_button = ttk.Button(client_frame, text="2. ارسال فایل", command=send_file_ui, state=tk.DISABLED)
send_button.pack(side=tk.LEFT, padx=5)

# --- نوار پیشرفت ---
progress_bar = ttk.Progressbar(root, orient="horizontal", length=300, mode="determinate")
progress_bar.pack(pady=10, padx=10, fill=tk.X)

# --- ناحیه نمایش وضعیت ---
status_frame = ttk.Frame(root)
status_frame.pack(pady=5, padx=10, fill=tk.BOTH, expand=True)

status_label = ttk.Label(status_frame, text="وضعیت عملیات:")
status_label.pack(anchor='w')

status_area = scrolledtext.ScrolledText(status_frame, wrap=tk.WORD, height=10, state=tk.NORMAL)
status_area.pack(fill=tk.BOTH, expand=True)

# --- تنظیم وضعیت اولیه دکمه ها ---
update_server_button_state() # برای اطمینان از وضعیت درست در شروع

# --- اتصال تابع on_closing به رویداد بستن پنجره ---
root.protocol("WM_DELETE_WINDOW", on_closing)

# --- شروع حلقه اصلی GUI ---
root.mainloop()