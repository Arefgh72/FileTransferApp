# gui.py - Main GUI class and application logic

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import time
import sys
import os
import socket # Needed for unblocking sockets on close

# Add the directory containing the modules to the Python path if running directly.
# This is important for absolute imports like `import config`.
# Check if the script is run directly OR if it's being imported as part of a larger package structure
# The '__package__' attribute is None when run directly as a script.
if __package__ is None:
    # Running as a script, assume package root is the directory containing this script.
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)
    print(f"DEBUG: Running as script, added {current_dir} to sys.path for imports.")
else:
    # Running as part of a package, absolute imports should work relative to package root.
    # No sys.path modification needed if package structure is correct.
    print(f"DEBUG: Running as part of package: {__package__}.")


# Import other modules in the package (using absolute imports based on package name)
# These imports now rely on the sys.path modification above if running as script,
# or standard package import mechanisms if running as package.
try:
    import config
    import utils
    import filetransfer # Renamed to filetransfer
    import tests
except ImportError as e:
    print(f"FATAL ERROR: Could not import required modules. Make sure config.py, utils.py, filetransfer.py, and tests.py are in the same directory or accessible within the package structure. Error: {e}", file=sys.stderr)
    # Attempt to show a messagebox error before exiting
    try:
        # Need to initialize Tkinter root briefly to show messagebox
        dummy_root = tk.Tk()
        dummy_root.withdraw() # Hide the main window
        messagebox.showerror("خطای اجرا", f"قادر به بارگذاری ماژول‌های لازم برنامه نبود.\n{e}\nلطفاً مطمئن شوید تمام فایل‌های برنامه در کنار یکدیگر قرار دارند.")
        dummy_root.destroy()
    except Exception:
        # If Tkinter fails too, just print to console/log
        pass
    sys.exit(1) # Exit the application if core imports fail


class Application:
    def __init__(self, root):
        self.root = root
        self.root.title("ارسال و دریافت فایل در شبکه")
        self.root.geometry("700x650")

        # --- Global State and Events ---
        self.selected_filepath = ""

        # Events to signal threads to stop/cancel
        self.server_stop_event = threading.Event()
        self.discovery_stop_event = threading.Event()
        self.network_test_server_stop_event = threading.Event()
        self.network_test_discovery_stop_event = threading.Event()
        self.cancel_transfer_event = threading.Event() # For file transfer cancel
        self.cancel_test_event = threading.Event()     # For any test cancel

        # Boolean flags indicating *which* operation is currently active (Managed by GUI)
        self.is_server_running = False             # File transfer server is active
        self.is_transfer_active = False            # A file transfer (client or server handler) is in progress
        self.is_write_test_active = False          # A write drive test is in progress
        self.is_read_test_active = False           # A read drive test is in progress
        self.is_all_tests_active = False           # Sequential drive tests are in progress
        self.is_network_test_server_running = False   # Network test server is active
        self.is_network_test_client_active = False # Network test client sender is active (using shared progress/speed)

        self.active_server_port = None # TCP port file transfer server is bound to
        self.active_network_test_server_port = None # TCP port network test server is bound to

        # --- GUI Callbacks for Worker Threads ---
        # Callbacks allow worker threads to safely interact with the GUI thread and get GUI state.
        # They are wrapped in utils.safe_gui_update to ensure they run in the GUI thread.
        self.gui_callbacks = {
            'root': self.root, # Pass the root window object (needed by utils.safe_gui_update)
            'update_status': lambda msg: utils.safe_gui_update(self.root, utils._update_status_direct, self.status_area, msg),
            'update_progress': lambda val: utils.safe_gui_update(self.root, utils._update_progress_direct, self.progress_bar, val),
            'update_speed': lambda msg: utils.safe_gui_update(self.root, utils._update_speed_direct, self.speed_var, msg),
            'show_info': lambda title, msg: utils.safe_gui_update(self.root, utils._show_messagebox_direct, 'info', title, msg),
            'show_warning': lambda title, msg: utils.safe_gui_update(self.root, utils._show_messagebox_direct, 'warning', title, msg),
            'show_error': lambda title, msg: utils.safe_gui_update(self.root, utils._show_messagebox_direct, 'error', title, msg),

            # Callbacks for state changes signalled by worker threads
            'on_transfer_started': self._on_transfer_started,
            'on_transfer_finished': self._on_transfer_finished,
            'on_server_stopped': self._on_server_stopped,

            'on_test_started': self._on_test_started, # Takes test_type ('write', 'read', 'network_receive' - though net_receive doesn't set flags anymore)
            'on_test_finished': self._on_test_finished, # Takes test_type
            'on_test_sequence_finished': self._on_test_sequence_finished, # Called when a test sequence (write, read, all, net_client) finishes
            'on_network_test_server_stopped': self._on_network_test_server_stopped,

            # Callbacks for threads to get/set internal GUI state needed for their logic
            'set_active_server_port': self._set_active_server_port,
            'get_active_server_port': self._get_active_server_port, # Primarily used by file discovery listener
            'set_active_network_test_server_port': self._set_active_network_test_server_port,
            'get_active_network_test_server_port': self._get_active_network_test_server_port, # Primarily used by network test discovery listener

            # Callbacks for worker threads to get GUI state (safe access to GUI thread variables)
            # These callbacks *must* be called using gui_callbacks['..._cb']() from worker threads.
            'is_transfer_active_cb': self._is_transfer_active_cb, # Used by file transfer server to prevent multiple connections
            'is_network_test_client_active_cb': self._is_network_test_client_active_cb, # Used by network test server? (Logic seems debatable here, but keeping callback)
            # Added a callback to check if the file server itself is running
            'is_server_running_cb': self._is_server_running_cb,
            # Added a callback to check if the network test server is running
            'is_network_test_server_running_cb': self._is_network_test_server_running_cb,

            # Callback to get the selected buffer size for *Tests* (Drive and Network Test Receiver)
            'get_test_buffer_size': self._get_selected_test_buffer_size, # Added this callback

            # Pass cancel events to the threads that need to check them
            'cancel_transfer_event': self.cancel_transfer_event,
            'cancel_test_event': self.cancel_test_event,

            # Callbacks to start other threads (e.g. discovery after TCP bind)
            # These are called from the TCP server threads after successful bind
            # They use root.after to ensure the thread starts safely in the GUI loop
            'start_discovery_thread': self._start_discovery_thread,
            'start_network_test_discovery_thread': self._start_network_test_discovery_thread,

            # Callback to get the selected receive buffer size for *File Transfer* (needed by the file server handler task)
            'get_receive_buffer_size': self._get_selected_receive_buffer_size # This one is specifically for File Transfer receive buffer
        }

        # --- Build GUI ---
        self._create_widgets()
        self._setup_layout()
        self._bind_events()

        # --- Initial Setup ---
        # Set initial combobox values from config if not already set by StringVar default
        if config.BUFFER_OPTIONS:
            options = list(config.BUFFER_OPTIONS.keys())
            default_option_key = options[0] if options else "Auto" # Fallback if somehow options is empty

            if not self.buffer_size_var.get():
                 self.buffer_size_combobox.set(default_option_key)
            self.buffer_size_combobox['values'] = options # Ensure values are set here

            if not self.test_buffer_size_var.get():
                 self.test_buffer_size_combobox.set(default_option_key)
            self.test_buffer_size_combobox['values'] = options # Ensure values are set here

            if not self.server_buffer_size_var.get() or self.server_buffer_size_var.get() == "Medium (16 KB)": # Check for initial empty or specific default
                 # Try setting medium as default, fallback to first
                 if "Medium (16 KB)" in config.BUFFER_OPTIONS:
                      self.server_buffer_size_combobox.set("Medium (16 KB)")
                 else:
                      self.server_buffer_size_combobox.set(default_option_key)
            self.server_buffer_size_combobox['values'] = options # Ensure values are set here

        else: # If no buffer options are defined in config, disable comboboxes and set a default text
            default_text = "N/A (No options)"
            self.buffer_size_var.set(default_text)
            self.buffer_size_combobox.config(state=tk.DISABLED, values=[default_text])
            self.test_buffer_size_var.set(default_text)
            self.test_buffer_size_combobox.config(state=tk.DISABLED, values=[default_text])
            self.server_buffer_size_var.set(default_text)
            self.server_buffer_size_combobox.config(state=tk.DISABLED, values=[default_text])


        self._update_button_state()
        print("DEBUG: Initial UI state update complete.")


    # --- GUI Widget Creation and Layout ---
    def _create_widgets(self):
        style = ttk.Style()
        # Try a theme, fall back to default if not available
        try:
            style.theme_use('vista')
        except tk.TclError:
            print("Theme 'vista' not found, using default theme.")

        self.notebook = ttk.Notebook(self.root)

        # --- File Transfer Tab ---
        self.transfer_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.transfer_frame, text=' انتقال فایل ')

        self.server_frame = ttk.LabelFrame(self.transfer_frame, text="حالت دریافت کننده فایل", padding="10")
        self.start_server_button = ttk.Button(self.server_frame, text="شروع دریافت", command=self.start_server_ui)
        self.stop_server_button = ttk.Button(self.server_frame, text="توقف دریافت", command=self.stop_server_ui, state=tk.DISABLED)

        # Added Receive Buffer selection for Server
        self.server_buffer_frame = ttk.Frame(self.server_frame)
        self.server_buffer_label = ttk.Label(self.server_buffer_frame, text="Receive Buffer:")
        # Initial value will be set in __init__ or after widget creation
        self.server_buffer_size_var = tk.StringVar()
        self.server_buffer_size_combobox = ttk.Combobox(self.server_buffer_frame, textvariable=self.server_buffer_size_var, state='readonly', width=12)
        # values set later in __init__

        self.client_frame = ttk.LabelFrame(self.transfer_frame, text="حالت فرستنده فایل", padding="10")
        self.select_file_button = ttk.Button(self.client_frame, text="1. انتخاب فایل", command=self.select_file_ui)

        self.file_var = tk.StringVar(value="هنوز فایلی انتخاب نشده")
        self.file_label = ttk.Entry(self.client_frame, textvariable=self.file_var, state='readonly', width=30)

        self.buffer_frame = ttk.Frame(self.client_frame)
        self.buffer_label = ttk.Label(self.buffer_frame, text="Send Buffer:")
        # Initial value will be set in __init__ or after widget creation
        self.buffer_size_var = tk.StringVar()
        self.buffer_size_combobox = ttk.Combobox(self.buffer_frame, textvariable=self.buffer_size_var, state=tk.DISABLED, width=12)
        # values set later in __init__


        self.send_button = ttk.Button(self.client_frame, text="2. ارسال فایل", command=self.send_file_ui, state=tk.DISABLED)

        # --- Speed Test Tab ---
        self.test_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.test_frame, text=' تست سرعت ')

        self.drive_test_frame = ttk.LabelFrame(self.test_frame, text="تست درایو (سرعت خواندن/نوشتن دیسک) - فایل تست: " + config.TEST_FILE_NAME + f" ({utils.format_bytes(config.TEST_FILE_SIZE)})", padding="10")
        self.drive_test_controls_frame = ttk.Frame(self.drive_test_frame)

        self.drive_test_buffer_frame = ttk.Frame(self.drive_test_controls_frame)
        self.test_buffer_label = ttk.Label(self.drive_test_buffer_frame, text="Buffer:")
        # Initial value will be set in __init__ or after widget creation
        self.test_buffer_size_var = tk.StringVar()
        self.test_buffer_size_combobox = ttk.Combobox(self.drive_test_buffer_frame, textvariable=self.test_buffer_size_var, state='readonly', width=12)
        # values set later in __init__

        self.drive_test_buttons_inner_frame = ttk.Frame(self.drive_test_controls_frame)
        self.write_test_button = ttk.Button(self.drive_test_buttons_inner_frame, text="شروع تست نوشتن", command=self.start_write_test_ui)
        self.read_test_button = ttk.Button(self.drive_test_buttons_inner_frame, text="شروع تست خواندن", command=self.start_read_test_ui)
        self.start_all_tests_button = ttk.Button(self.drive_test_buttons_inner_frame, text="شروع تست همه", command=self.start_all_tests_ui)

        self.network_test_frame = ttk.LabelFrame(self.test_frame, text=f"تست شبکه (LAN/Router) - حجم داده تست: {utils.format_bytes(config.NETWORK_TEST_SIZE)}", padding="10")
        self.network_test_buttons_frame = ttk.Frame(self.network_test_frame)

        self.start_network_test_server_button = ttk.Button(self.network_test_buttons_frame, text="شروع تست شبکه (دریافت)", command=self.start_network_test_server_ui)
        self.stop_network_test_server_button = ttk.Button(self.network_test_buttons_frame, text="توقف دریافت تست", command=self.stop_network_test_server_ui, state=tk.DISABLED)
        self.start_network_test_client_button = ttk.Button(self.network_test_buttons_frame, text="شروع تست شبکه (ارسال)", command=self.start_network_test_client_ui)


        self.network_test_info_label = ttk.Label(self.test_frame, text="\nبرای تست سرعت انتقال فایل در شبکه (شامل درایو و شبکه):"
                                                    "\n- در کامپیوتر اول در تب 'انتقال فایل' روی 'شروع دریافت' کلیک کنید."
                                                    "\n- در کامپیوتر دوم در تب 'انتقال فایل'، فایل را انتخاب و روی 'ارسال فایل' کلیک کنید."
                                                    "\n\nبرای تست سرعت شبکه خالص (بدون در نظر گرفتن درایو):"
                                                    "\n- در کامپیوتر اول در تب 'تست سرعت' روی 'شروع تست شبکه (دریافت)' کلیک کنید."
                                                    "\n- در کامپیوتر دوم در تب 'تست سرعت' روی 'شروع تست شبکه (ارسال)' کلیک کنید."
                                                    "\n- سرعت نمایش داده شده در سمت ارسال کننده تست، سرعت آپلود و در سمت دریافت کننده تست، سرعت دانلود را نشان می‌دهد.",
                                     justify=tk.LEFT)

        # --- Shared Progress Bar, Speed Display, and Cancel Buttons ---
        self.progress_speed_cancel_frame = ttk.Frame(self.root)
        self.progress_bar = ttk.Progressbar(self.progress_speed_cancel_frame, orient="horizontal", mode="determinate")
        self.speed_var = tk.StringVar(value="Speed: N/A")
        self.speed_label = ttk.Label(self.progress_speed_cancel_frame, textvariable=self.speed_var)
        self.cancel_frame = ttk.Frame(self.progress_speed_cancel_frame)
        self.cancel_button = ttk.Button(self.cancel_frame, text="لغو انتقال", command=self.cancel_transfer_ui, state=tk.DISABLED)
        self.test_cancel_button = ttk.Button(self.cancel_frame, text="لغو تست", command=self.cancel_test_ui, state=tk.DISABLED)

        # --- Shared Status Area ---
        self.status_frame = ttk.Frame(self.root)
        self.status_label = ttk.Label(self.status_frame, text="وضعیت عملیات:")
        self.status_area = scrolledtext.ScrolledText(self.status_frame, wrap=tk.WORD, height=8, state=tk.NORMAL)


    def _setup_layout(self):
        self.notebook.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

        self.server_frame.pack(fill=tk.X, padx=10, pady=5)
        self.start_server_button.pack(side=tk.LEFT, padx=5)
        self.stop_server_button.pack(side=tk.LEFT, padx=5)
        self.server_buffer_frame.pack(side=tk.LEFT, padx=10) # Pack the server buffer frame
        self.server_buffer_label.pack(side=tk.LEFT)
        self.server_buffer_size_combobox.pack(side=tk.LEFT)


        self.client_frame.pack(fill=tk.X, padx=10, pady=5)
        self.select_file_button.pack(side=tk.LEFT, padx=5)
        self.file_label.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.buffer_frame.pack(side=tk.LEFT, padx=5)
        self.buffer_label.pack(side=tk.LEFT)
        self.buffer_size_combobox.pack(side=tk.LEFT)
        self.send_button.pack(side=tk.LEFT, padx=5)

        self.drive_test_frame.pack(fill=tk.X, padx=10, pady=5)
        self.drive_test_controls_frame.pack(fill=tk.X, padx=0, pady=5)
        self.drive_test_buffer_frame.pack(side=tk.LEFT, padx=10)
        self.test_buffer_label.pack(side=tk.LEFT)
        self.test_buffer_size_combobox.pack(side=tk.LEFT)
        self.drive_test_buttons_inner_frame.pack(side=tk.RIGHT, padx=10)
        self.write_test_button.pack(side=tk.LEFT, padx=5)
        self.read_test_button.pack(side=tk.LEFT, padx=5)
        self.start_all_tests_button.pack(side=tk.LEFT, padx=5)

        self.network_test_frame.pack(fill=tk.X, padx=10, pady=5)
        self.network_test_buttons_frame.pack(fill=tk.X, padx=0, pady=5)
        self.start_network_test_server_button.pack(side=tk.LEFT, padx=5)
        self.stop_network_test_server_button.pack(side=tk.LEFT, padx=5)
        self.start_network_test_client_button.pack(side=tk.LEFT, padx=10)


        self.network_test_info_label.pack(pady=10, padx=10, anchor='w')


        self.progress_speed_cancel_frame.pack(pady=(0, 10), padx=10, fill=tk.X)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.speed_label.pack(side=tk.LEFT, padx=(0, 10))
        self.cancel_frame.pack(side=tk.LEFT)
        self.cancel_button.pack(side=tk.LEFT, padx=(0, 5))
        self.test_cancel_button.pack(side=tk.LEFT)

        self.status_frame.pack(pady=5, padx=10, fill=tk.BOTH, expand=True)
        self.status_label.pack(anchor='w')
        self.status_area.pack(fill=tk.BOTH, expand=True)


    def _bind_events(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)


    # --- State Management Methods (Internal to GUI) ---

    def _update_button_state(self):
        """ Directly updates the state of all relevant widgets based on internal flags.
            This function MUST run in the GUI thread.
        """
        # print(f"DEBUG: _update_button_state called. server_running: {self.is_server_running}, is_transfer_active: {self.is_transfer_active}, is_write_test_active: {self.is_write_test_active}, is_read_test_active: {self.is_read_test_active}, is_all_tests_active: {self.is_all_tests_active}, is_network_test_server_running: {self.is_network_test_server_running}, is_network_test_client_active: {self.is_network_test_client_active}, selected_filepath: {self.selected_filepath}")

        is_any_test_active = self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active

        # Determine global state
        is_any_operation_active = self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or is_any_test_active

        # Manage Cancel Buttons
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.cancel_button, tk.NORMAL if self.is_transfer_active else tk.DISABLED)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.test_cancel_button, tk.NORMAL if is_any_test_active else tk.DISABLED)


        # --- File Transfer Tab Buttons ---
        # These buttons are disabled if ANY operation is active, UNLESS it's the specific server/transfer button itself.
        # File Server buttons are enabled only if no operation is active OR if only the server is running (to allow stopping).
        # File Client buttons are enabled only if no operation is active.

        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.start_server_button, tk.NORMAL if not is_any_operation_active else tk.DISABLED)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.stop_server_button, tk.NORMAL if self.is_server_running else tk.DISABLED)
        # Enable server buffer selection only when idle AND there are buffer options
        server_buffer_state = 'readonly' if not is_any_operation_active and config.BUFFER_OPTIONS else tk.DISABLED
        utils.safe_gui_update(self.root, utils._update_combobox_state_direct, self.server_buffer_size_combobox, server_buffer_state)

        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.select_file_button, tk.NORMAL if not is_any_operation_active else tk.DISABLED)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.send_button, tk.NORMAL if not is_any_operation_active and self.selected_filepath else tk.DISABLED) # Send needs file selected

        # Send buffer combobox state depends on file selection AND overall state AND buffer options existence
        send_buffer_state = tk.DISABLED
        if not is_any_operation_active and self.selected_filepath and config.BUFFER_OPTIONS:
            send_buffer_state = 'readonly'
        utils.safe_gui_update(self.root, utils._update_combobox_state_direct, self.buffer_size_combobox, send_buffer_state)


        # --- Speed Test Tab Buttons ---
        # Test buttons are enabled only if no operation is active.
        # Network Test Server buttons are enabled only if no operation is active OR if only the net test server is running.

        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.write_test_button, tk.NORMAL if not is_any_operation_active else tk.DISABLED)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.read_test_button, tk.NORMAL if not is_any_operation_active else tk.DISABLED)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.start_all_tests_button, tk.NORMAL if not is_any_operation_active else tk.DISABLED)

        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.start_network_test_server_button, tk.NORMAL if not is_any_operation_active else tk.DISABLED)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.stop_network_test_server_button, tk.NORMAL if self.is_network_test_server_running else tk.DISABLED)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.start_network_test_client_button, tk.NORMAL if not is_any_operation_active else tk.DISABLED) # Net test client is a test sequence


        # Test buffer combobox state depends on overall state AND buffer options existence
        test_buffer_state = tk.DISABLED
        if not is_any_operation_active and config.BUFFER_OPTIONS:
            test_buffer_state = 'readonly'
        utils.safe_gui_update(self.root, utils._update_combobox_state_direct, self.test_buffer_size_combobox, test_buffer_state)


        # Ensure progress/speed are reset when nothing is active
        if not is_any_operation_active:
            utils.safe_gui_update(self.root, utils._update_progress_direct, self.progress_bar, 0)
            utils.safe_gui_update(self.root, utils._update_speed_direct, self.speed_var, "Speed: N/A")


    # --- Callbacks from Worker Threads (MUST be methods of this class, called via safe_gui_update or root.after) ---

    def _on_transfer_started(self):
        print("DEBUG: _on_transfer_started callback received")
        # This is called by run_tcp_server_task *after* accepting a connection,
        # and by send_file_task *before* connecting.
        self.is_transfer_active = True
        self._update_button_state()

    def _on_transfer_finished(self):
        print("DEBUG: _on_transfer_finished callback received")
        # This is called by the finally block of handle_client_connection (server side)
        # and the finally block of send_file_task (client side).
        self.is_transfer_active = False
        print(f"DEBUG: is_transfer_active set to {self.is_transfer_active}")
        self._update_button_state()

    def _on_server_stopped(self):
        print("DEBUG: _on_server_stopped callback received")
        # This is called by the finally block of run_tcp_server_task.
        self.is_server_running = False
        self.is_transfer_active = False # Ensure transfer state is reset too if server stopped while transfer was active
        self._update_button_state()

    def _on_test_started(self, test_type):
         print(f"DEBUG: _on_test_started callback received for type: {test_type}")
         # This callback is primarily for potentially updating UI based on test type (e.g., status message)
         # The main flags (is_write_test_active, etc.) are set in the UI event handlers *before* starting the thread.
         pass # No state flags need to be set here based on the current design.


    def _on_test_finished(self, test_type):
         print(f"DEBUG: _on_test_finished callback received for type: {test_type}")
         # This callback is primarily for potential cleanup or intermediate reporting within a sequence.
         # The main flag reset happens in _on_test_sequence_finished.
         pass


    def _on_test_sequence_finished(self):
         print("DEBUG: _on_test_sequence_finished callback received")
         # This callback should reset all test flags that indicate a test is *running*.
         # It is called by the WRAPPERS (run_write_speed_test_wrapper, run_read_speed_test_wrapper, run_all_tests_task)
         # and the NETWORK TEST CLIENT (run_network_test_client_task).
         # The network test SERVER (run_network_test_server_task) and its handler (handle_network_test_client) DO NOT call this.
         self.is_write_test_active = False
         self.is_read_test_active = False
         self.is_all_tests_active = False
         self.is_network_test_client_active = False # Network test client (sender) is finished

         self.cancel_test_event.clear() # Clear the test cancel event HERE after a sequence finishes
         print("DEBUG: cancel_test_event cleared in _on_test_sequence_finished")
         self._update_button_state()


    def _on_network_test_server_stopped(self):
        print("DEBUG: _on_network_test_server_stopped callback received")
        # This is called by the finally block of run_network_test_server_task.
        self.is_network_test_server_running = False
        self._update_button_state()


    def _set_active_server_port(self, port):
         print(f"DEBUG: _set_active_server_port callback received: {port}")
         self.active_server_port = port
         if port is not None:
              # Update status message here using root.after for safety
              utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], f"[*] سرور انتقال فایل در حال گوش دادن روی TCP پورت {port}")
              # Get local IP for display (optional, but helpful)
              local_ip = "N/A"
              try:
                  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                  s.connect(("8.8.8.8", 80)) # Connect to a common public server just to get local IP
                  local_ip = s.getsockname()[0]
                  s.close()
              except Exception: pass # Ignore errors, IP might not be available
              utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], f"    آماده دریافت فایل در: {local_ip}:{port}")
              utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] منتظر دریافت اتصال برای انتقال فایل...")

              # Start discovery only AFTER the port is successfully bound and UI is updated
              # Use root.after to delay slightly and ensure everything is set up
              self.root.after(50, self._start_discovery_thread)
         else:
              # Port is None, means server stopped or failed to bind
              utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] سرور انتقال فایل متوقف شد یا موفق به راه‌اندازی نشد.")


    def _get_active_server_port(self):
         """ Callback for file discovery listener to get the server's bound port. """
         return self.active_server_port

    def _set_active_network_test_server_port(self, port):
         print(f"DEBUG: _set_active_network_test_server_port callback received: {port}")
         self.active_network_test_server_port = port
         if port is not None:
             # Update status message here
             utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], f"[*] دریافت کننده تست شبکه در حال گوش دادن روی TCP پورت {port}")
             # Get local IP for display
             local_ip = "N/A"
             try:
                 s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                 s.connect(("8.8.8.8", 80))
                 local_ip = s.getsockname()[0]
                 s.close()
             except Exception: pass
             utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], f"    آماده تست شبکه در: {local_ip}:{port}")
             utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] منتظر دریافت اتصال برای تست شبکه...")

             # Start network test discovery only AFTER binding
             self.root.after(50, self._start_network_test_discovery_thread)
         else:
             # Port is None, means server stopped or failed to bind
             utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] دریافت کننده تست شبکه متوقف شد یا موفق به راه‌اندازی نشد.")


    def _get_active_network_test_server_port(self):
         """ Callback for network test discovery listener. """
         return self.active_network_test_server_port

    # --- CALLBACK METHODS TO GET STATE ---
    def _is_transfer_active_cb(self):
         """ Callback for worker threads (like file server's accept loop) to check if a transfer is active. """
         # This method runs in the GUI thread when called by a worker thread via gui_callbacks['..._cb']()
         # Accessing self.is_transfer_active directly here is safe.
         return self.is_transfer_active

    def _is_network_test_client_active_cb(self):
         """ Callback for network test server to check if a network test client (sender) is active.
             Note: This flag is set by the sender side, so the receiver checks if a sender *process* is running somewhere.
             This might not be the most robust way to prevent multiple connections on the server.
         """
         return self.is_network_test_client_active

    def _is_server_running_cb(self):
        """ Callback for worker threads (like discovery listener) to check if file server is running. """
        return self.is_server_running

    def _is_network_test_server_running_cb(self):
        """ Callback for worker threads (like network test discovery listener) to check if net test server is running. """
        return self.is_network_test_server_running


    # --- CALLBACK TO GET BUFFER SIZE FOR TESTS ---
    def _get_selected_test_buffer_size(self):
        """ Callback for test tasks (drive tests, network test receiver/sender) to get the user's selected test buffer size. """
        selected_option = self.test_buffer_size_var.get()
        # Call utils.get_buffer_size with only ONE argument as per its definition
        buffer_size = utils.get_buffer_size(selected_option)
        print(f"DEBUG: _get_selected_test_buffer_size called, returning {buffer_size}")
        return buffer_size

    # --- CALLBACK TO GET RECEIVE BUFFER SIZE FOR FILE TRANSFER ---
    def _get_selected_receive_buffer_size(self):
        """ Callback for the file server task's client handler to get the user's selected receive buffer size. """
        selected_option = self.server_buffer_size_var.get()
        # Call utils.get_buffer_size with only ONE argument as per its definition
        buffer_size = utils.get_buffer_size(selected_option)
        print(f"DEBUG: _get_selected_receive_buffer_size called, returning {buffer_size}")
        return buffer_size


    # --- UI Event Handlers (Called by Tkinter, run in GUI thread) ---

    def select_file_ui(self):
        print("DEBUG: select_file_ui called")
        # Check if any operation is active before allowing file selection
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از انتخاب فایل جدید منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: select_file_ui called during active operation, ignoring.")
            return

        filepath = filedialog.askopenfilename(title="فایل مورد نظر را انتخاب کنید")
        if filepath:
            self.selected_filepath = filepath
            utils.safe_gui_update(self.root, utils._update_entry_var_direct, self.file_var, os.path.basename(filepath))
            self.gui_callbacks['update_status'](f"فایل انتخاب شده: {filepath}")
            print(f"DEBUG: File selected: {self.selected_filepath}")
            self._update_button_state() # Update button state based on file selection
        else:
            # Clear selected file if selection was cancelled or failed
            self.selected_filepath = ""
            utils.safe_gui_update(self.root, utils._update_entry_var_direct, self.file_var, "هنوز فایلی انتخاب نشده")
            self.gui_callbacks['update_status']("انتخاب فایل لغو شد.")
            print("DEBUG: File selection cancelled")
            self._update_button_state() # Update button state after clearing file


    def start_server_ui(self):
        print("DEBUG: start_server_ui called")
        # Check if any operation is active
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از شروع سرور منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: start_server_ui called during active operation, ignoring.")
            return

        self.status_area.delete('1.0', tk.END)
        # The status update "--- شروع حالت سرور..." is now done in _set_active_server_port callback
        # gui_callbacks['update_status']("--- شروع حالت سرور (گیرنده فایل) ---")
        print("DEBUG: Starting file transfer server mode")

        # Clear all relevant stop/cancel events
        self.server_stop_event.clear()
        self.discovery_stop_event.clear()
        self.cancel_transfer_event.clear()
        self.cancel_test_event.clear() # Also clear test cancel in case it was left set

        # Set state flags (is_transfer_active starts False)
        self.is_server_running = True
        self.active_server_port = None # Will be set by the server thread callback after bind

        # Update UI state based on new flags
        self._update_button_state()

        # Start the file server thread (includes TCP bind and accept loop)
        # Discovery listener and specific status updates are started by a callback from the server thread *after* successful bind.
        filetransfer.start_file_server(self.gui_callbacks, self.server_stop_event, self.discovery_stop_event)
        print("DEBUG: start_server_ui finished, TCP server thread requested.")


    def _start_discovery_thread(self):
         """ Called by the file server thread after successful bind to start the discovery listener. """
         print("DEBUG: _start_discovery_thread called")
         # Check if the server is still intended to be running and discovery isn't stopped
         if self.is_server_running and not self.discovery_stop_event.is_set():
              filetransfer.start_file_discovery_listener(
                  self.discovery_stop_event,
                  self.gui_callbacks,
                  self._get_active_server_port # Pass the callback to get the bound port
              )
              print("DEBUG: File transfer Discovery thread started by _start_discovery_thread.")
         else:
              print("DEBUG: File transfer Discovery thread not started because server is not running or stop event is set.")


    def stop_server_ui(self):
        print("DEBUG: stop_server_ui called")
        if not self.is_server_running:
            self.gui_callbacks['update_status']("[*] سرور انتقال فایل در حال حاضر در حال اجرا نیست.")
            print("DEBUG: File transfer server not running, stop_server_ui ignored")
            return

        self.gui_callbacks['update_status']("[*] در حال متوقف کردن سرور انتقال فایل...")
        print("DEBUG: Stopping file transfer server mode")

        # Call the stop function in filetransfer.py
        filetransfer.stop_file_server(
            self.server_stop_event,
            self.discovery_stop_event,
            self.cancel_transfer_event, # Pass the cancel event to stop active transfers
            self.active_server_port # Pass the active port to unblock accept
        )

        self.gui_callbacks['update_status']("[*] درخواست توقف سرور ارسال شد. منتظر تکمیل...")
        print("DEBUG: stop_server_ui finished.")
        # The GUI state flags (is_server_running, is_transfer_active) will be reset
        # by the _on_server_stopped callback which is called by the server thread's finally block.


    def send_file_ui(self):
        print("DEBUG: send_file_ui called")
        # Check if any operation is active
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از ارسال فایل منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: send_file_ui called during active operation, ignoring.")
            return

        if not self.selected_filepath:
            messagebox.showerror("خطا", "لطفاً ابتدا یک فایل برای ارسال انتخاب کنید.")
            print("DEBUG: No file selected, cannot send")
            return

        # Validate file existence and readability
        try:
             if not os.path.exists(self.selected_filepath): raise FileNotFoundError(f"فایل '{self.selected_filepath}' یافت نشد.")
             if not os.path.isfile(self.selected_filepath): raise IsADirectoryError(f"مسیر '{self.selected_filepath}' یک پوشه است، نه یک فایل.")
             # Check readability by attempting to open and immediately close
             with open(self.selected_filepath, 'rb') as f: pass
             # Get file size (used for header, not for buffer size selection logic anymore)
             try: file_size = os.path.getsize(self.selected_filepath)
             except Exception: file_size = 0 # Handle case where size can't be determined
        except Exception as e:
             messagebox.showerror("خطای فایل", f"فایل '{os.path.basename(self.selected_filepath)}' قابل دسترسی یا خواندن نیست:\n{e}")
             self.gui_callbacks['update_status'](f"[!] خطای دسترسی به فایل انتخابی: {e}")
             print(f"DEBUG: Error accessing selected file '{self.selected_filepath}': {e}", file=sys.stderr)
             # Clear the invalid file selection
             self.selected_filepath = ""
             utils.safe_gui_update(self.root, utils._update_entry_var_direct, self.file_var, "هنوز فایلی انتخاب نشده")
             self._update_button_state() # Update button state after clearing file
             return

        selected_buffer_option = self.buffer_size_var.get()
        # Get the chosen buffer size using the helper function (takes only one argument now)
        chosen_buffer_size = utils.get_buffer_size(selected_buffer_option)


        self.status_area.delete('1.0', tk.END)
        self.gui_callbacks['update_status']("--- شروع حالت کلاینت (فرستنده فایل) ---")
        print("DEBUG: Starting file transfer client mode")

        # Set state flag (Transfer is now active on sender side)
        self.is_transfer_active = True
        self._update_button_state()

        # Clear cancel events for the new operation
        self.cancel_transfer_event.clear()
        self.cancel_test_event.clear() # Clear test cancel too


        # Initial UI updates for progress/speed
        self.gui_callbacks['update_progress'](0)
        self.gui_callbacks['update_speed']("Speed: Searching for server...")

        # Start the client task in a separate thread
        filetransfer.start_file_client(self.selected_filepath, chosen_buffer_size, self.gui_callbacks, self.cancel_transfer_event)
        print("DEBUG: Client file transfer thread requested.")
        # The is_transfer_active flag will be reset by _on_transfer_finished callback
        # called by the send_file_task's finally block.


    def cancel_transfer_ui(self):
        print("DEBUG: Cancel transfer button pressed. Setting cancel_transfer_event.")
        # Check if a transfer is actually active before showing message/setting event
        if self.is_transfer_active:
            self.gui_callbacks['update_status']("[*] درخواست لغو انتقال فایل...")
            self.cancel_transfer_event.set()
        else:
             self.gui_callbacks['update_status']("[*] انتقالی در حال حاضر برای لغو وجود ندارد.")


    def start_write_test_ui(self):
        print("DEBUG: start_write_test_ui called")
        # Check if any operation is active
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از شروع تست نوشتن منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: start_write_test_ui called during active operation, ignoring.")
            return

        self.status_area.delete('1.0', tk.END)
        # Status update is handled inside the test wrapper now

        selected_buffer_option = self.test_buffer_size_var.get()
        # Get the chosen buffer size using the helper function (takes only one argument now)
        chosen_buffer_size = utils.get_buffer_size(selected_buffer_option)


        # Set state flag BEFORE starting thread
        # The wrapper will call _on_test_started('write') eventually, but setting flag here updates UI immediately.
        self.is_write_test_active = True
        self._update_button_state()

        # Clear cancel events for the new operation
        self.cancel_transfer_event.clear() # Clear transfer cancel too
        self.cancel_test_event.clear()

        # Initial UI updates for progress/speed
        self.gui_callbacks['update_progress'](0)
        self.gui_callbacks['update_speed']("Speed: Starting Write Test...")


        # Start the test task in a separate thread using the wrapper
        tests.start_write_test(chosen_buffer_size, self.gui_callbacks, self.cancel_test_event)
        print("DEBUG: Write test thread requested.")
        # The state flags will be reset by _on_test_sequence_finished called by the wrapper's finally block.


    def start_read_test_ui(self):
        print("DEBUG: start_read_test_ui called")
        # Check if any operation is active
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از شروع تست خواندن منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: start_read_test_ui called during active operation, ignoring.")
            return

        self.status_area.delete('1.0', tk.END)
         # Status update is handled inside the test wrapper now

        selected_buffer_option = self.test_buffer_size_var.get()
        # Get the chosen buffer size using the helper function (takes only one argument now)
        chosen_buffer_size = utils.get_buffer_size(selected_buffer_option)


        # Set state flag BEFORE starting thread
        # The wrapper will call _on_test_started('read') eventually.
        self.is_read_test_active = True
        self._update_button_state()

        # Clear cancel events for the new operation
        self.cancel_transfer_event.clear() # Clear transfer cancel too
        self.cancel_test_event.clear()

        # Initial UI updates for progress/speed
        self.gui_callbacks['update_progress'](0)
        self.gui_callbacks['update_speed']("Speed: Starting Read Test...")

        # Start the test task in a separate thread using the wrapper
        tests.start_read_test(chosen_buffer_size, self.gui_callbacks, self.cancel_test_event)
        print("DEBUG: Read test thread requested.")
        # The state flags will be reset by _on_test_sequence_finished called by the wrapper's finally block.


    def start_all_tests_ui(self):
        print("DEBUG: start_all_tests_ui called")
        # Check if any operation is active
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از شروع همه تست‌ها منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: start_all_tests_ui called during active operation, ignoring.")
            return

        self.status_area.delete('1.0', tk.END)
        # Initial status update is handled inside the all_tests_task

        selected_buffer_option = self.test_buffer_size_var.get()
        # Get the chosen buffer size using the helper function (takes only one argument now)
        chosen_buffer_size = utils.get_buffer_size(selected_buffer_option)


        # Set state flag BEFORE starting thread
        # The task will call _on_test_started for each sub-test ('write', then 'read')
        # The overall flag indicates the sequence is active.
        self.is_all_tests_active = True
        self._update_button_state()

        # Clear cancel events for the new operation
        self.cancel_transfer_event.clear() # Clear transfer cancel too
        self.cancel_test_event.clear()

        # Initial UI updates for progress/speed
        self.gui_callbacks['update_progress'](0)
        self.gui_callbacks['update_speed']("Speed: Starting Tests...") # Initial speed status for the sequence


        # Start the sequential test task in a separate thread
        tests.start_all_tests(chosen_buffer_size, self.gui_callbacks, self.cancel_test_event)
        print("DEBUG: All tests thread requested.")
        # The state flags will be reset by _on_test_sequence_finished called by the task's finally block.


    def start_network_test_server_ui(self):
        print("DEBUG: start_network_test_server_ui called")
        # Check if any operation is active
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از شروع دریافت کننده تست شبکه منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: start_network_test_server_ui called during active operation, ignoring.")
            return

        self.status_area.delete('1.0', tk.END)
        # The status update "--- شروع حالت دریافت کننده تست شبکه..." is now done in _set_active_network_test_server_port callback
        # self.gui_callbacks['update_status']("--- شروع حالت دریافت کننده تست شبکه ---")
        print("DEBUG: Starting network test receiver mode")

        # Clear relevant stop/cancel events
        self.network_test_server_stop_event.clear()
        self.network_test_discovery_stop_event.clear()
        self.cancel_transfer_event.clear() # Clear transfer cancel too
        self.cancel_test_event.clear() # Use the shared test cancel event

        # Set state flag
        self.is_network_test_server_running = True
        # is_network_test_client_active is for the SENDER side, keep False here.
        self.active_network_test_server_port = None # Will be set by callback after bind

        # Update UI state
        self._update_button_state()

        # Start the network test server thread (includes TCP bind and accept loop)
        # Discovery listener and specific status updates are started by a callback from the server thread *after* successful bind.
        tests.start_network_test_server(self.gui_callbacks, self.network_test_server_stop_event, self.network_test_discovery_stop_event)
        print("DEBUG: Network test TCP server thread requested.")
        # State will be reset by _on_network_test_server_stopped called by the server thread's finally block.


    def _start_network_test_discovery_thread(self):
        """ Called by the network test server thread after successful bind to start the discovery listener. """
        print("DEBUG: _start_network_test_discovery_thread called")
        # Check if the network test server is still intended to be running and discovery isn't stopped
        if self.is_network_test_server_running and not self.network_test_discovery_stop_event.is_set():
             tests.start_network_test_discovery_listener(
                 self.network_test_discovery_stop_event,
                 self.gui_callbacks,
                 self._get_active_network_test_server_port # Pass callback to get bound port
             )
             print("DEBUG: Network test Discovery thread started by _start_network_test_discovery_thread.")
        else:
             print("DEBUG: Network test Discovery thread not started.")


    def stop_network_test_server_ui(self):
        print("DEBUG: stop_network_test_server_ui called")
        if not self.is_network_test_server_running:
            self.gui_callbacks['update_status']("[*] دریافت کننده تست شبکه در حال حاضر در حال اجرا نیست.")
            print("DEBUG: Network test receiver not running, stop_network_test_server_ui ignored")
            return

        self.gui_callbacks['update_status']("[*] در حال متوقف کردن دریافت کننده تست شبکه...")
        print("DEBUG: Stopping network test receiver mode")

        # Call the stop function in tests.py
        tests.stop_network_test_server(
            self.network_test_server_stop_event,
            self.network_test_discovery_stop_event,
            self.cancel_test_event, # Pass the cancel event to stop active test receives
            self.active_network_test_server_port # Pass the active port to unblock accept
        )

        self.gui_callbacks['update_status']("[*] درخواست توقف دریافت کننده تست شبکه ارسال شد. منتظر تکمیل...")
        print("DEBUG: stop_network_test_server_ui finished.")
        # State will be reset by _on_network_test_server_stopped called by the server thread's finally block.


    def start_network_test_client_ui(self):
        print("DEBUG: start_network_test_client_ui called")
        # Check if any operation is active
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از شروع فرستنده تست شبکه منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: start_network_test_client_ui called during active operation, ignoring.")
            return

        self.status_area.delete('1.0', tk.END)
        # Initial status update is handled inside the client task
        # self.gui_callbacks['update_status']("--- شروع حالت فرستنده تست شبکه ---")

        selected_buffer_option = self.test_buffer_size_var.get()
        # Get the chosen buffer size using the helper function (takes only one argument now)
        chosen_buffer_size = utils.get_buffer_size(selected_buffer_option)


        # Set state flag BEFORE starting thread
        self.is_network_test_client_active = True
        self._update_button_state()

        # Clear cancel events for the new operation
        self.cancel_transfer_event.clear() # Clear transfer cancel too
        self.cancel_test_event.clear()

        # Initial UI updates for progress/speed
        self.gui_callbacks['update_progress'](0)
        self.gui_callbacks['update_speed']("Speed: Starting Network Test Client...")


        # Start the network test client task in a separate thread
        tests.start_network_test_client(chosen_buffer_size, self.gui_callbacks, self.cancel_test_event)
        print("DEBUG: Network test client thread requested.")
        # State will be reset by _on_test_sequence_finished called by the client task's finally block.


    def cancel_test_ui(self):
        print("DEBUG: Cancel test button pressed. Setting cancel_test_event.")
        # Check if any test operation is actually active before showing message/setting event
        if self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            self.gui_callbacks['update_status']("[*] درخواست لغو تست...")
            self.cancel_test_event.set()
        else:
            self.gui_callbacks['update_status']("[*] تستی در حال حاضر برای لغو وجود ندارد.")


    def on_closing(self):
        print("DEBUG: on_closing called")
        # Set all stop/cancel events
        self.server_stop_event.set()
        self.discovery_stop_event.set()
        self.network_test_server_stop_event.set()
        self.network_test_discovery_stop_event.set()
        self.cancel_transfer_event.set()
        self.cancel_test_event.set()

        # Attempt to unblock blocking network calls using temporary connections
        try:
            if self.active_server_port is not None:
                print(f"DEBUG: Attempting unblock connection for file server port {self.active_server_port}")
                # Using 127.0.0.1 (localhost) is usually sufficient to unblock a listener bound to 0.0.0.0
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1) # Short timeout for the connection attempt
                sock.connect(('127.0.0.1', self.active_server_port))
                # Sending a small amount of data might sometimes be necessary, but close is usually enough
                # sock.sendall(b'stop') # Note: If you send data here, handle_client_connection needs to expect it or discard it safely
                sock.close()
                print("DEBUG: Unblock connection sent for file server.")
        except Exception as e:
            # Ignore errors, the server socket might already be closed
            print(f"DEBUG: Failed to unblock file server on {self.active_server_port}: {e}", file=sys.stderr)
            pass

        try:
            if self.active_network_test_server_port is not None:
                print(f"DEBUG: Attempting unblock connection for network test server port {self.active_network_test_server_port}")
                 # Using 127.0.0.1 (localhost) is usually sufficient to unblock a listener bound to 0.0.0.0
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1) # Short timeout
                sock.connect(('127.0.0.1', self.active_network_test_server_port))
                # sock.sendall(b'stop')
                sock.close()
                print("DEBUG: Unblock connection sent for network test server.")
        except Exception as e:
             # Ignore errors
             print(f"DEBUG: Failed to unblock network test server on {self.active_network_test_server_port}: {e}", file=sys.stderr)
             pass


        # Manually reset state flags for UI clarity if needed (though they should be reset by finally blocks)
        # Adding a small delay before destroying GUI might help threads finish cleanup
        print("DEBUG: Giving threads a moment to shut down...")
        time.sleep(0.1) # Small delay


        self.is_server_running = False
        self.is_network_test_server_running = False
        self.is_transfer_active = False
        self.is_write_test_active = False
        self.is_read_test_active = False
        self.is_all_tests_active = False
        self.is_network_test_client_active = False


        print("DEBUG: Calling root.destroy()")
        if hasattr(self.root, 'destroy') and self.root.winfo_exists():
             self.root.destroy()
        print("DEBUG: root.destroy() called (if window existed).")

    def run(self):
        print("DEBUG: Starting root.mainloop()")
        self.root.mainloop()
        print("DEBUG: root.mainloop() finished.")