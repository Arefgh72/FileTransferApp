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
# gui.py should import config, utils, tests (still in root), and filetransfer (the new orchestrator in root)
try:
    import config
    import utils
    import filetransfer # This now imports the orchestrator filetransfer.py in the root
    import tests # Assuming tests.py is still in the root
except ImportError as e:
    print(f"FATAL ERROR: Could not import required modules. Make sure config.py, utils.py, filetransfer.py, and tests.py are in the same directory or accessible within the package structure.", file=sys.stderr)
    print(f"Import Error: {e}", file=sys.stderr)
    # Attempt to show a messagebox error before exiting
    try:
        # Need to initialize Tkinter root briefly to show messagebox
        dummy_root = tk.Tk()
        dummy_root.withdraw() # Hide the main window
        messagebox.showerror("خطای اجرا", f"قادر به بارگذاری ماژول‌های لازم برنامه نبود.\n{e}\nلطفاً مطمئن شوید تمام فایل‌های اصلی برنامه و پوشه transfer_core در کنار یکدیگر قرار دارند.")
        dummy_root.destroy()
    except Exception:
        # If Tkinter fails too, just print to console/log
        pass
    sys.exit(1) # Exit the application if core imports fail


class Application:
    def __init__(self, root):
        self.root = root
        self.root.title("ارسال و دریافت فایل و پوشه در شبکه") # Updated title
        self.root.geometry("700x650")

        # --- Global State and Events ---
        self.selected_filepath = ""
        self.selected_folder_path = "" # New variable for selected folder

        # Events to signal threads to stop/cancel
        self.server_stop_event = threading.Event() # For main file/folder server TCP accept loop
        self.discovery_stop_event = threading.Event() # For file/folder discovery UDP listener
        self.network_test_server_stop_event = threading.Event() # For network test server TCP accept loop
        self.network_test_discovery_stop_event = threading.Event() # For network test discovery UDP listener
        self.cancel_transfer_event = threading.Event() # For ongoing file or folder transfer client/handler
        self.cancel_test_event = threading.Event()     # For any ongoing test (drive or network test client/handler)

        # Boolean flags indicating *which* operation is currently active (Managed by GUI)
        self.is_server_running = False             # File/Folder transfer server is active (TCP listening)
        self.is_transfer_active = False            # A file/folder transfer (client sender or server handler) is in progress
        self.is_write_test_active = False          # A write drive test is in progress
        self.is_read_test_active = False           # A read drive test is in progress
        self.is_all_tests_active = False           # Sequential drive tests are in progress
        self.is_network_test_server_running = False   # Network test server is active (TCP listening)
        self.is_network_test_client_active = False # Network test client sender is active (using shared progress/speed)

        self.active_server_port = None # TCP port file/folder transfer server is bound to
        self.active_network_test_server_port = None # TCP port network test server is bound to

        # --- GUI Callbacks for Worker Threads ---
        # Callbacks allow worker threads to safely interact with the GUI thread and get/set GUI state.
        # They are wrapped in utils.safe_gui_update where necessary before passing to threads.
        self.gui_callbacks = {
            'root': self.root, # Pass the root window object (needed by utils.safe_gui_update)
            'update_status': lambda msg: utils.safe_gui_update(self.root, utils._update_status_direct, self.status_area, msg),
            'update_progress': lambda val: utils.safe_gui_update(self.root, utils._update_progress_direct, self.progress_bar, val),
            'update_speed': lambda msg: utils.safe_gui_update(self.root, utils._update_speed_direct, self.speed_var, msg),
            'show_info': lambda title, msg: utils.safe_gui_update(self.root, utils._show_messagebox_direct, 'info', title, msg),
            'show_warning': lambda title, msg: utils.safe_gui_update(self.root, utils._show_messagebox_direct, 'warning', title, msg),
            'show_error': lambda title, msg: utils.safe_gui_update(self.root, utils._show_messagebox_direct, 'error', title, msg),

            # Callbacks for state changes signalled by worker threads (run in GUI thread by definition)
            'on_transfer_started': self._on_transfer_started,
            'on_transfer_finished': self._on_transfer_finished,
            'on_server_stopped': self._on_server_stopped,

            'on_test_started': self._on_test_started, # Takes test_type ('write', 'read', 'network_receive')
            'on_test_finished': self._on_test_finished, # Takes test_type
            'on_test_sequence_finished': self._on_test_sequence_finished, # Called when a test sequence (write, read, all, net_client) finishes
            'on_network_test_server_stopped': self._on_network_test_server_stopped,

            # Callbacks for threads to set internal GUI state needed for their logic (run in GUI thread)
            'set_active_server_port': self._set_active_server_port,
            'set_active_network_test_server_port': self._set_active_network_test_server_port,

            # Callbacks for worker threads to get GUI state (safe access to GUI thread variables) (run in GUI thread)
            # These callbacks are methods of Application but are called by threads via the dict.
            'get_active_server_port': self._get_active_server_port, # Used by file discovery listener
            'get_active_network_test_server_port': self._get_active_network_test_server_port, # Used by network test discovery listener
            'is_transfer_active_cb': self._is_transfer_active_cb, # Used by file transfer server to prevent multiple connections
            'is_network_test_client_active_cb': self._is_network_test_client_active_cb, # Used by network test server? (Logic seems debatable here)
            'is_server_running_cb': self._is_server_running_cb, # Used by discovery listener
            'is_network_test_server_running_cb': self._is_network_test_server_running_cb, # Used by network test discovery listener

            # Callbacks to get the selected buffer sizes from GUI (run in GUI thread)
            'get_test_buffer_size': self._get_selected_test_buffer_size, # Used by Test tasks (drive & network test client/handler)
            'get_receive_buffer_size': self._get_selected_receive_buffer_size, # Used by File/Folder transfer receive handlers

            # Pass cancel events to the threads that need to check them
            # The thread receives the Event object directly and checks .is_set()
            'cancel_transfer_event': self.cancel_transfer_event,
            'cancel_test_event': self.cancel_test_event,

            # Callbacks from server tasks (running in threads) to trigger starting other threads (GUI thread)
            # These callbacks are methods of Application, passed via gui_callbacks, and called by server threads.
            # They use root.after to ensure the target thread is started safely in the GUI loop.
            'start_discovery_thread': self._start_discovery_thread, # Called by FileServerTCP task
            'start_network_test_discovery_thread': self._start_network_test_discovery_thread, # Called by NetTestServerTCP task
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

            # Set default for Send Buffer (client)
            if not self.buffer_size_var.get():
                 self.buffer_size_combobox.set(default_option_key)
            self.buffer_size_combobox['values'] = options # Ensure values are set here

            # Set default for Test Buffer
            if not self.test_buffer_size_var.get():
                 self.test_buffer_size_combobox.set(default_option_key)
            self.test_buffer_size_combobox['values'] = options # Ensure values are set here

            # Set default for Receive Buffer (server)
            # Check for initial empty or specific default like "Medium (16 KB)"
            # Try setting medium as default, fallback to first
            default_receive_option = "Medium (16 KB)"
            if not self.server_buffer_size_var.get():
                 if default_receive_option in config.BUFFER_OPTIONS:
                      self.server_buffer_size_combobox.set(default_receive_option)
                 elif options:
                      self.server_buffer_size_combobox.set(options[0]) # Fallback to first option
                 # If options is empty, it will be handled below

            self.server_buffer_size_combobox['values'] = options # Ensure values are set here

        # Handle case where BUFFER_OPTIONS is empty or None
        if not config.BUFFER_OPTIONS:
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
            # Use a theme that looks decent on multiple platforms if possible
            style.theme_use('clam') # 'clam', 'alt', 'default', 'classic' might be more portable than 'vista'
        except tk.TclError:
            print("Preferred theme not found, using default theme.")
            try: style.theme_use('default')
            except tk.TclError: pass # Fallback to whatever works

        self.notebook = ttk.Notebook(self.root)

        # --- File/Folder Transfer Tab ---
        self.transfer_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.transfer_frame, text=' انتقال فایل / پوشه ') # Updated tab text

        self.server_frame = ttk.LabelFrame(self.transfer_frame, text="حالت دریافت کننده", padding="10") # Updated label
        self.start_server_button = ttk.Button(self.server_frame, text="شروع دریافت فایل/پوشه", command=self.start_server_ui) # Updated button text
        self.stop_server_button = ttk.Button(self.server_frame, text="توقف دریافت", command=self.stop_server_ui, state=tk.DISABLED)

        # Added Receive Buffer selection for Server
        self.server_buffer_frame = ttk.Frame(self.server_frame)
        self.server_buffer_label = ttk.Label(self.server_buffer_frame, text="Receive Buffer:")
        # Initial value will be set in __init__ or after widget creation
        self.server_buffer_size_var = tk.StringVar()
        self.server_buffer_size_combobox = ttk.Combobox(self.server_buffer_frame, textvariable=self.server_buffer_size_var, state='readonly', width=12)
        # values set later in __init__

        self.client_frame = ttk.LabelFrame(self.transfer_frame, text="حالت فرستنده", padding="10") # Updated label
        # Use two buttons for selecting File or Folder
        self.select_file_button = ttk.Button(self.client_frame, text="1. انتخاب فایل", command=self.select_file_ui)
        self.select_folder_button = ttk.Button(self.client_frame, text="1. انتخاب پوشه", command=self.select_folder_ui) # New button

        # Use one label/entry for displaying selected path
        self.file_var = tk.StringVar(value="مسیر انتخاب شده: ندارد") # Updated initial text
        self.file_label = ttk.Entry(self.client_frame, textvariable=self.file_var, state='readonly', width=30)

        # Send Buffer for client side
        self.buffer_frame = ttk.Frame(self.client_frame)
        self.buffer_label = ttk.Label(self.buffer_frame, text="Send Buffer:")
        # Initial value will be set in __init__ or after widget creation
        self.buffer_size_var = tk.StringVar()
        self.buffer_size_combobox = ttk.Combobox(self.buffer_frame, textvariable=self.buffer_size_var, state=tk.DISABLED, width=12)
        # values set later in __init__


        self.send_button = ttk.Button(self.client_frame, text="2. ارسال", command=self.send_file_ui, state=tk.DISABLED) # Updated button text


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
                                                    "\n- در کامپیوتر اول در تب 'انتقال فایل / پوشه' روی 'شروع دریافت' کلیک کنید." # Updated text
                                                    "\n- در کامپیوتر دوم در تب 'انتقال فایل / پوشه'، فایل یا پوشه را انتخاب و روی 'ارسال' کلیک کنید." # Updated text
                                                    "\n\nبرای تست سرعت شبکه خالص (بدون در نظر گرفتن درایو):"
                                                    "\n- در کامپیوتر اول در تب 'تست سرعت' روی 'شروع تست شبکه (دریافت)' کلیک کنید."
                                                    "\n- در کامپیوتر دوم در تب 'تست سرعت' روی 'شروع تست شبکه (ارسال)' کلیک کنید."
                                                    "\n- سرعت نمایش داده شده در سمت ارسال کننده تست، سرعت آپلود و در سمت دریافت کننده تست، سرعت دانلود را نشان می‌دهد.",
                                     justify=tk.LEFT)

        # --- Shared Progress Bar, Speed Display, and Cancel Buttons ---
        self.progress_speed_cancel_frame = ttk.Frame(self.root)
        # Progress bar mode can be 'determinate' or 'indeterminate'
        # Determinate mode is good when total size is known (single file, total folder size calc)
        # Indeterminate mode is good when total size is unknown or progress is sequential steps (like folder transfer receiver)
        # Let's keep it determinate for now, but be aware it might not be perfectly accurate for folder receive without total size info in protocol.
        self.progress_bar = ttk.Progressbar(self.progress_speed_cancel_frame, orient="horizontal", mode="determinate")
        self.speed_var = tk.StringVar(value="Speed: N/A")
        self.speed_label = ttk.Label(self.progress_speed_cancel_frame, textvariable=self.speed_var)
        self.cancel_frame = ttk.Frame(self.progress_speed_cancel_frame)
        self.cancel_button = ttk.Button(self.cancel_frame, text="لغو انتقال", command=self.cancel_transfer_ui, state=tk.DISABLED)
        self.test_cancel_button = ttk.Button(self.cancel_frame, text="لغو تست", command=self.cancel_test_ui, state=tk.DISABLED)

        # --- Shared Status Area ---
        self.status_frame = ttk.Frame(self.root)
        self.status_label = ttk.Label(self.status_frame, text="وضعیت عملیات:")
        self.status_area = scrolledtext.ScrolledText(self.status_frame, wrap=tk.WORD, height=8, state=tk.DISABLED) # Set state to DISABLED initially


    def _setup_layout(self):
        self.notebook.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)

        self.server_frame.pack(fill=tk.X, padx=10, pady=5)
        self.start_server_button.pack(side=tk.LEFT, padx=5)
        self.stop_server_button.pack(side=tk.LEFT, padx=5)
        self.server_buffer_frame.pack(side=tk.LEFT, padx=10) # Pack the server buffer frame
        self.server_buffer_label.pack(side=tk.LEFT)
        self.server_buffer_size_combobox.pack(side=tk.LEFT)


        self.client_frame.pack(fill=tk.X, padx=10, pady=5)
        # Arrange select file/folder buttons and the entry
        select_buttons_frame = ttk.Frame(self.client_frame)
        select_buttons_frame.pack(side=tk.LEFT, padx=(0, 5))
        self.select_file_button.pack(side=tk.LEFT, padx=5)
        self.select_folder_button.pack(side=tk.LEFT, padx=5) # Pack the new button

        self.file_label.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True) # This entry now shows file or folder path

        # Pack buffer frame and send button
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
        # print(f"DEBUG: _update_button_state called. server_running: {self.is_server_running}, is_transfer_active: {self.is_transfer_active}, is_write_test_active: {self.is_write_test_active}, is_read_test_active: {self.is_read_test_active}, is_all_tests_active: {self.is_all_tests_active}, is_network_test_server_running: {self.is_network_test_server_running}, is_network_test_client_active: {self.is_network_test_client_active}, selected_filepath: {self.selected_filepath}, selected_folder_path: {self.selected_folder_path}")

        is_any_test_active = self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active

        # Determine global state
        is_any_operation_active = self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or is_any_test_active

        # Manage Cancel Buttons
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.cancel_button, tk.NORMAL if self.is_transfer_active else tk.DISABLED)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.test_cancel_button, tk.NORMAL if is_any_test_active else tk.DISABLED)


        # --- File/Folder Transfer Tab Buttons ---
        # These buttons are disabled if ANY operation is active, UNLESS it's the specific server/transfer button itself.
        # File Server buttons are enabled only if no operation is active OR if only the server is running (to allow stopping).
        # File Client buttons are enabled only if no operation is active.

        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.start_server_button, tk.NORMAL if not is_any_operation_active else tk.DISABLED)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.stop_server_button, tk.NORMAL if self.is_server_running else tk.DISABLED)
        # Enable server buffer selection only when idle AND there are buffer options
        server_buffer_state = 'readonly' if not is_any_operation_active and config.BUFFER_OPTIONS else tk.DISABLED
        utils.safe_gui_update(self.root, utils._update_combobox_state_direct, self.server_buffer_size_combobox, server_buffer_state)

        # Select buttons are enabled only when no operation is active
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.select_file_button, tk.NORMAL if not is_any_operation_active else tk.DISABLED)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.select_folder_button, tk.NORMAL if not is_any_operation_active else tk.DISABLED) # New folder select button


        # Send button needs file OR folder selected AND no operation active
        can_send = not is_any_operation_active and (self.selected_filepath or self.selected_folder_path)
        utils.safe_gui_update(self.root, utils._update_button_state_direct, self.send_button, tk.NORMAL if can_send else tk.DISABLED)

        # Send buffer combobox state depends on item selection AND overall state AND buffer options existence
        send_buffer_state = tk.DISABLED
        if not is_any_operation_active and (self.selected_filepath or self.selected_folder_path) and config.BUFFER_OPTIONS:
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
        # This is called by filetransfer.run_tcp_server_task *after* accepting a connection (and protocol detection),
        # and by filetransfer.send_file_task/send_folder_task *before* connecting.
        self.is_transfer_active = True
        # Update UI state on the next GUI loop iteration
        utils.safe_gui_update(self.root, self._update_button_state)


    def _on_transfer_finished(self):
        print("DEBUG: _on_transfer_finished callback received")
        # This is called by the finally block of transfer handlers (server side)
        # and the finally block of client send tasks (client side).
        self.is_transfer_active = False
        print(f"DEBUG: is_transfer_active set to {self.is_transfer_active}")
        # Update UI state on the next GUI loop iteration
        utils.safe_gui_update(self.root, self._update_button_state)


    def _on_server_stopped(self):
        print("DEBUG: _on_server_stopped callback received")
        # This is called by the finally block of filetransfer.run_tcp_server_task.
        self.is_server_running = False
        # If server stopped while a transfer was active, reset transfer state too.
        # This might happen if stop_server_ui is called while a transfer is in progress.
        self.is_transfer_active = False
        # Update UI state on the next GUI loop iteration
        utils.safe_gui_update(self.root, self._update_button_state)


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
         # Update UI state on the next GUI loop iteration
         utils.safe_gui_update(self.root, self._update_button_state)


    def _on_network_test_server_stopped(self):
        print("DEBUG: _on_network_test_server_stopped callback received")
        # This is called by the finally block of tests.run_network_test_server_task.
        self.is_network_test_server_running = False
        # Update UI state on the next GUI loop iteration
        utils.safe_gui_update(self.root, self._update_button_state)


    def _set_active_server_port(self, port):
         print(f"DEBUG: _set_active_server_port callback received: {port}")
         self.active_server_port = port
         if port is not None:
              # Update status message here using root.after for safety
              utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], f"[*] سرور انتقال فایل/پوشه در حال گوش دادن روی TCP پورت {port}") # Updated status text
              # Get local IP for display (optional, but helpful)
              local_ip = "N/A"
              try:
                  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                  s.connect(("8.8.8.8", 80)) # Connect to a common public server just to get local IP
                  local_ip = s.getsockname()[0]
                  s.close()
              except Exception: pass # Ignore errors, IP might not be available
              utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], f"    آماده دریافت در: {local_ip}:{port}") # Updated status text
              utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] منتظر دریافت اتصال برای انتقال...") # Updated status text

              # Start discovery only AFTER the port is successfully bound and UI is updated.
              # The server task calls this callback (_set_active_server_port) when it's ready.
              # We then use root.after to schedule the start of the discovery thread in the GUI loop.
              self.root.after(50, self._start_discovery_thread)
         else:
              # Port is None, means server stopped or failed to bind
              utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] سرور انتقال فایل/پوشه متوقف شد یا موفق به راه‌اندازی نشد.") # Updated status text


    def _get_active_server_port(self):
         """ Callback for file discovery listener to get the server's bound port. Runs in GUI thread."""
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
         """ Callback for network test discovery listener. Runs in GUI thread."""
         return self.active_network_test_server_port

    # --- CALLBACK METHODS TO GET STATE ---
    def _is_transfer_active_cb(self):
         """ Callback for worker threads (like file/folder server's accept loop) to check if a transfer is active. Runs in GUI thread."""
         # Accessing self.is_transfer_active directly here is safe.
         return self.is_transfer_active

    def _is_network_test_client_active_cb(self):
         """ Callback for network test server to check if a network test client (sender) is active. Runs in GUI thread."""
         # Note: This flag is set by the sender side.
         return self.is_network_test_client_active

    def _is_server_running_cb(self):
        """ Callback for worker threads (like discovery listener) to check if file/folder server is running. Runs in GUI thread."""
        return self.is_server_running

    def _is_network_test_server_running_cb(self):
        """ Callback for worker threads (like network test discovery listener) to check if net test server is running. Runs in GUI thread."""
        return self.is_network_test_server_running


    # --- CALLBACK TO GET BUFFER SIZE FOR TESTS ---
    def _get_selected_test_buffer_size(self):
        """ Callback for test tasks (drive tests, network test receiver/sender) to get the user's selected test buffer size. Runs in GUI thread."""
        selected_option = self.test_buffer_size_var.get()
        buffer_size = utils.get_buffer_size(selected_option)
        print(f"DEBUG: _get_selected_test_buffer_size called, returning {buffer_size}")
        return buffer_size

    # --- CALLBACK TO GET RECEIVE BUFFER SIZE FOR FILE TRANSFER ---
    def _get_selected_receive_buffer_size(self):
        """ Callback for the file/folder server task's client handler to get the user's selected receive buffer size. Runs in GUI thread."""
        selected_option = self.server_buffer_size_var.get()
        buffer_size = utils.get_buffer_size(selected_option)
        print(f"DEBUG: _get_selected_receive_buffer_size called, returning {buffer_size}")
        return buffer_size


    # --- Callbacks to start other threads (Scheduled via root.after by server threads) ---
    # These methods MUST run in the GUI thread.
    def _start_discovery_thread(self):
         """ Called by the file server thread after successful bind to start the discovery listener. Runs in GUI thread."""
         print("DEBUG: _start_discovery_thread called (via root.after)")
         # Check if the server is still intended to be running and discovery isn't stopped
         # Use the state flag directly as we are in the GUI thread.
         if self.is_server_running and not self.discovery_stop_event.is_set():
              # Call the orchestrator function to start the discovery thread
              filetransfer.start_file_discovery_listener(
                  self.discovery_stop_event,
                  self.gui_callbacks, # Pass all GUI callbacks
                  self._get_active_server_port # Pass the specific callback needed by discovery listener
              )
              print("DEBUG: File transfer Discovery thread started.")
         else:
              print("DEBUG: File transfer Discovery thread not started because server is not running or stop event is set.")


    def _start_network_test_discovery_thread(self):
        """ Called by the network test server thread after successful bind to start the discovery listener. Runs in GUI thread."""
        print("DEBUG: _start_network_test_discovery_thread called (via root.after)")
        # Check if the network test server is still intended to be running and discovery isn't stopped
        # Use the state flag directly as we are in the GUI thread.
        if self.is_network_test_server_running and not self.network_test_discovery_stop_event.is_set():
             # Call the tests module function (assuming tests module is still orchestrated differently or simple)
             # Ensure tests.py has a start_network_test_discovery_listener function.
             tests.start_network_test_discovery_listener(
                 self.network_test_discovery_stop_event,
                 self.gui_callbacks, # Pass all GUI callbacks
                 self._get_active_network_test_server_port # Pass the specific callback needed
             )
             print("DEBUG: Network test Discovery thread started.")
        else:
             print("DEBUG: Network test Discovery thread not started.")


    # --- UI Event Handlers (Called by Tkinter, run in GUI thread) ---

    def select_file_ui(self):
        print("DEBUG: select_file_ui called")
        # Check if any operation is active before allowing selection
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از انتخاب مورد جدید منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: select_file_ui called during active operation, ignoring.")
            return

        filepath = filedialog.askopenfilename(title="فایل مورد نظر را انتخاب کنید")
        if filepath:
            self.selected_filepath = filepath
            self.selected_folder_path = "" # Clear selected folder when file is selected
            utils.safe_gui_update(self.root, utils._update_entry_var_direct, self.file_var, f"فایل: {os.path.basename(filepath)}") # Updated text
            self.gui_callbacks['update_status'](f"فایل انتخاب شده: {filepath}")
            print(f"DEBUG: File selected: {self.selected_filepath}")
            self._update_button_state() # Update button state based on selection
        else:
            # Clear selected file if selection was cancelled or failed
            self.selected_filepath = ""
            # If no folder is selected either, update the label to "None selected"
            if not self.selected_folder_path:
                 utils.safe_gui_update(self.root, utils._update_entry_var_direct, self.file_var, "مسیر انتخاب شده: ندارد")
            self.gui_callbacks['update_status']("انتخاب فایل لغو شد.")
            print("DEBUG: File selection cancelled")
            self._update_button_state() # Update button state after clearing file


    # New function for selecting a folder
    def select_folder_ui(self):
        print("DEBUG: select_folder_ui called")
        # Check if any operation is active before allowing selection
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از انتخاب مورد جدید منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: select_folder_ui called during active operation, ignoring.")
            return

        folder_path = filedialog.askdirectory(title="پوشه مورد نظر را انتخاب کنید")
        if folder_path:
            # Basic check if it's a directory
            if not os.path.isdir(folder_path):
                 messagebox.showerror("خطا", "مسیر انتخاب شده یک پوشه معتبر نیست.")
                 self.gui_callbacks['update_status'](f"[!] مسیر انتخاب شده پوشه نیست: {folder_path}")
                 print(f"DEBUG: Selected path is not a directory: {folder_path}", file=sys.stderr)
                 return # Do not select non-directory path


            self.selected_folder_path = folder_path
            self.selected_filepath = "" # Clear selected file when folder is selected
            # Display the base name of the folder, or the full path if it's a root directory (like C:/)
            display_path = os.path.basename(folder_path) if os.path.basename(folder_path) else folder_path
            utils.safe_gui_update(self.root, utils._update_entry_var_direct, self.file_var, f"پوشه: {display_path}") # Updated text
            self.gui_callbacks['update_status'](f"پوشه انتخاب شده: {folder_path}")
            print(f"DEBUG: Folder selected: {self.selected_folder_path}")
            self._update_button_state() # Update button state based on selection
        else:
            # Clear selected folder if selection was cancelled or failed
            self.selected_folder_path = ""
             # If no file is selected either, update the label to "None selected"
            if not self.selected_filepath:
                 utils.safe_gui_update(self.root, utils._update_entry_var_direct, self.file_var, "مسیر انتخاب شده: ندارد")
            self.gui_callbacks['update_status']("انتخاب پوشه لغو شد.")
            print("DEBUG: Folder selection cancelled")
            self._update_button_state() # Update button state after clearing folder


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
        # gui_callbacks['update_status']("--- شروع حالت سرور...")
        print("DEBUG: Starting file/folder transfer server mode")

        # Clear all relevant stop/cancel events for this new operation
        self.server_stop_event.clear()
        self.discovery_stop_event.clear()
        self.cancel_transfer_event.clear() # Clear transfer cancel event
        self.cancel_test_event.clear() # Clear test cancel event too in case it was left set

        # Set state flags (is_transfer_active starts False, will be set true when connection is accepted)
        self.is_server_running = True
        self.active_server_port = None # Will be set by the server thread callback after bind

        # Update UI state based on new flags
        utils.safe_gui_update(self.root, self._update_button_state) # Update UI state immediately

        # Start the file/folder server thread (calls the orchestrator function)
        # Pass all necessary parameters including the specific callbacks the server task needs.
        filetransfer.start_file_server(
             self.gui_callbacks, # Pass the entire callbacks dictionary
             self.server_stop_event,
             self.discovery_stop_event,
             self._get_selected_receive_buffer_size # Pass the callback to get receive buffer size
        )
        print("DEBUG: start_server_ui finished, TCP server thread requested.")


    def stop_server_ui(self):
        print("DEBUG: stop_server_ui called")
        if not self.is_server_running:
            self.gui_callbacks['update_status']("[*] سرور انتقال فایل/پوشه در حال حاضر در حال اجرا نیست.") # Updated text
            print("DEBUG: File transfer server not running, stop_server_ui ignored")
            return

        utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] در حال متوقف کردن سرور انتقال فایل/پوشه...") # Updated text
        print("DEBUG: Stopping file/folder transfer server mode")

        # Call the orchestrator function to stop the server components
        filetransfer.stop_file_server(
            self.server_stop_event,
            self.discovery_stop_event,
            self.cancel_transfer_event, # Pass the cancel event to stop active transfers handled by server
            self.active_server_port # Pass the active port to unblock accept
        )

        utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] درخواست توقف سرور ارسال شد. منتظر تکمیل...")
        print("DEBUG: stop_server_ui finished.")
        # The GUI state flags (is_server_running, is_transfer_active) will be reset
        # by the _on_server_stopped callback which is called by the server thread's finally block.


    # Renamed from send_file_ui functionally, but keeping name for simplicity in UI code
    def send_file_ui(self):
        print("DEBUG: send_file_ui (now send_item_ui) called")
        # Check if any operation is active
        if self.is_server_running or self.is_network_test_server_running or self.is_transfer_active or \
           self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            messagebox.showwarning("هشدار", "عملیات دیگری در حال اجرا است. لطفاً قبل از ارسال مورد جدید منتظر بمانید یا عملیات را لغو کنید.")
            print("DEBUG: send_file_ui called during active operation, ignoring.")
            return

        # Check if either a file or a folder is selected
        if not self.selected_filepath and not self.selected_folder_path:
            messagebox.showerror("خطا", "لطفاً ابتدا یک فایل یا پوشه برای ارسال انتخاب کنید.") # Updated message
            print("DEBUG: No file or folder selected, cannot send")
            return

        selected_buffer_option = self.buffer_size_var.get()
        # Get the chosen buffer size using the helper function
        chosen_buffer_size = utils.get_buffer_size(selected_buffer_option)

        # Validate buffer size (basic check if options are defined)
        if config.BUFFER_OPTIONS and chosen_buffer_size <= 0:
             messagebox.showerror("خطا", "اندازه بافر ارسال معتبر نیست.")
             print(f"DEBUG: Invalid send buffer size selected: {chosen_buffer_size}", file=sys.stderr)
             return


        self.status_area.delete('1.0', tk.END)
        utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "--- شروع حالت کلاینت (فرستنده) ---") # Updated text
        print("DEBUG: Starting client sender mode")

        # Set state flag (Transfer is now active on sender side)
        self.is_transfer_active = True
        utils.safe_gui_update(self.root, self._update_button_state) # Update UI state immediately

        # Clear cancel events for the new operation
        self.cancel_transfer_event.clear() # Clear transfer cancel event
        self.cancel_test_event.clear() # Clear test cancel too


        # Initial UI updates for progress/speed
        utils.safe_gui_update(self.root, self.gui_callbacks['update_progress'], 0)
        utils.safe_gui_update(self.root, self.gui_callbacks['update_speed'], "Speed: Searching for server...")

        # Start the appropriate client task in a separate thread using the orchestrator functions
        if self.selected_filepath:
             # Send a single file
             print(f"DEBUG: Starting file client for {self.selected_filepath}")
             # Call the orchestrator function to start the file send task
             filetransfer.start_file_client(
                 self.selected_filepath,
                 chosen_buffer_size,
                 self.gui_callbacks, # Pass all GUI callbacks
                 self.cancel_transfer_event # Pass the cancel event
             )
             print("DEBUG: Client file transfer thread requested.")
        elif self.selected_folder_path:
             # Send a folder
             print(f"DEBUG: Starting folder client for {self.selected_folder_path}")
             # Call the orchestrator function to start the folder send task
             filetransfer.start_folder_client(
                 self.selected_folder_path,
                 chosen_buffer_size,
                 self.gui_callbacks, # Pass all GUI callbacks
                 self.cancel_transfer_event # Pass the cancel event
             )
             print("DEBUG: Client folder transfer thread requested.")
        # The is_transfer_active flag will be reset by _on_transfer_finished callback
        # called by the send_file_task/send_folder_task's finally block.


    def cancel_transfer_ui(self):
        print("DEBUG: Cancel transfer button pressed. Setting cancel_transfer_event.")
        # Check if a transfer is actually active before showing message/setting event
        if self.is_transfer_active:
            utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] درخواست لغو انتقال فایل/پوشه...") # Updated text
            self.cancel_transfer_event.set() # Set the event to signal cancellation
        else:
             utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] انتقالی در حال حاضر برای لغو وجود ندارد.")


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
        # Get the chosen buffer size using the helper function
        chosen_buffer_size = utils.get_buffer_size(selected_buffer_option)

        # Validate buffer size (basic check if options are defined)
        if config.BUFFER_OPTIONS and chosen_buffer_size <= 0:
             messagebox.showerror("خطا", "اندازه بافر تست معتبر نیست.")
             print(f"DEBUG: Invalid test buffer size selected: {chosen_buffer_size}", file=sys.stderr)
             return


        # Set state flag BEFORE starting thread
        # The wrapper will call _on_test_started('write') eventually, but setting flag here updates UI immediately.
        self.is_write_test_active = True
        utils.safe_gui_update(self.root, self._update_button_state) # Update UI state immediately

        # Clear cancel events for the new operation
        self.cancel_transfer_event.clear() # Clear transfer cancel too
        self.cancel_test_event.clear() # Clear test cancel event

        # Initial UI updates for progress/speed
        utils.safe_gui_update(self.root, self.gui_callbacks['update_progress'], 0)
        utils.safe_gui_update(self.root, self.gui_callbacks['update_speed'], "Speed: Starting Write Test...")


        # Start the test task in a separate thread using the tests module function
        # Assuming tests module still works as is.
        tests.start_write_test(
            chosen_buffer_size,
            self.gui_callbacks, # Pass all GUI callbacks
            self.cancel_test_event # Pass the cancel event
        )
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
        # Get the chosen buffer size using the helper function
        chosen_buffer_size = utils.get_buffer_size(selected_buffer_option)

        # Validate buffer size (basic check if options are defined)
        if config.BUFFER_OPTIONS and chosen_buffer_size <= 0:
             messagebox.showerror("خطا", "اندازه بافر تست معتبر نیست.")
             print(f"DEBUG: Invalid test buffer size selected: {chosen_buffer_size}", file=sys.stderr)
             return


        # Set state flag BEFORE starting thread
        # The wrapper will call _on_test_started('read') eventually.
        self.is_read_test_active = True
        utils.safe_gui_update(self.root, self._update_button_state) # Update UI state immediately

        # Clear cancel events for the new operation
        self.cancel_transfer_event.clear() # Clear transfer cancel too
        self.cancel_test_event.clear() # Clear test cancel event

        # Initial UI updates for progress/speed
        utils.safe_gui_update(self.root, self.gui_callbacks['update_progress'], 0)
        utils.safe_gui_update(self.root, self.gui_callbacks['update_speed'], "Speed: Starting Read Test...")

        # Start the test task in a separate thread using the tests module function
        tests.start_read_test(
            chosen_buffer_size,
            self.gui_callbacks, # Pass all GUI callbacks
            self.cancel_test_event # Pass the cancel event
        )
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
        # Get the chosen buffer size using the helper function
        chosen_buffer_size = utils.get_buffer_size(selected_buffer_option)

        # Validate buffer size (basic check if options are defined)
        if config.BUFFER_OPTIONS and chosen_buffer_size <= 0:
             messagebox.showerror("خطا", "اندازه بافر تست معتبر نیست.")
             print(f"DEBUG: Invalid test buffer size selected: {chosen_buffer_size}", file=sys.stderr)
             return


        # Set state flag BEFORE starting thread
        # The task will call _on_test_started for each sub-test ('write', then 'read')
        # The overall flag indicates the sequence is active.
        self.is_all_tests_active = True
        utils.safe_gui_update(self.root, self._update_button_state) # Update UI state immediately

        # Clear cancel events for the new operation
        self.cancel_transfer_event.clear() # Clear transfer cancel too
        self.cancel_test_event.clear() # Clear test cancel event

        # Initial UI updates for progress/speed
        utils.safe_gui_update(self.root, self.gui_callbacks['update_progress'], 0)
        utils.safe_gui_update(self.root, self.gui_callbacks['update_speed'], "Speed: Starting Tests...") # Initial speed status for the sequence


        # Start the sequential test task in a separate thread using the tests module function
        tests.start_all_tests(
            chosen_buffer_size,
            self.gui_callbacks, # Pass all GUI callbacks
            self.cancel_test_event # Pass the cancel event
        )
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

        # Clear relevant stop/cancel events for this new operation
        self.network_test_server_stop_event.clear()
        self.network_test_discovery_stop_event.clear()
        self.cancel_transfer_event.clear() # Clear transfer cancel too
        self.cancel_test_event.clear() # Use the shared test cancel event

        # Set state flag
        self.is_network_test_server_running = True
        # is_network_test_client_active is for the SENDER side, keep False here.
        self.active_network_test_server_port = None # Will be set by callback after bind

        # Update UI state
        utils.safe_gui_update(self.root, self._update_button_state) # Update UI state immediately

        # Start the network test server thread (calls the tests module function)
        tests.start_network_test_server(
            self.gui_callbacks, # Pass all GUI callbacks
            self.network_test_server_stop_event,
            self.network_test_discovery_stop_event
        )
        print("DEBUG: Network test TCP server thread requested.")
        # State will be reset by _on_network_test_server_stopped called by the server thread's finally block.


    def _start_network_test_discovery_thread(self):
        """ Called by the network test server thread after successful bind to start the discovery listener. Runs in GUI thread."""
        print("DEBUG: _start_network_test_discovery_thread called (via root.after)")
        # Check if the network test server is still intended to be running and discovery isn't stopped
        # Use the state flag directly as we are in the GUI thread.
        if self.is_network_test_server_running and not self.network_test_discovery_stop_event.is_set():
             # Call the tests module function to start the discovery listener thread
             tests.start_network_test_discovery_listener(
                 self.network_test_discovery_stop_event,
                 self.gui_callbacks, # Pass all GUI callbacks
                 self._get_active_network_test_server_port # Pass callback to get bound port
             )
             print("DEBUG: Network test Discovery thread started.")
        else:
             print("DEBUG: Network test Discovery thread not started.")


    def stop_network_test_server_ui(self):
        print("DEBUG: stop_network_test_server_ui called")
        if not self.is_network_test_server_running:
            self.gui_callbacks['update_status']("[*] دریافت کننده تست شبکه در حال حاضر در حال اجرا نیست.")
            print("DEBUG: Network test receiver not running, stop_network_test_server_ui ignored")
            return

        utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] در حال متوقف کردن دریافت کننده تست شبکه...")
        print("DEBUG: Stopping network test receiver mode")

        # Call the tests module function to stop the server components
        tests.stop_network_test_server(
            self.network_test_server_stop_event,
            self.network_test_discovery_stop_event,
            self.cancel_test_event, # Pass the cancel event to stop active test receives
            self.active_network_test_server_port # Pass the active port to unblock accept
        )

        utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] درخواست توقف دریافت کننده تست شبکه ارسال شد. منتظر تکمیل...")
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
        # Get the chosen buffer size using the helper function
        chosen_buffer_size = utils.get_buffer_size(selected_buffer_option)

        # Validate buffer size (basic check if options are defined)
        if config.BUFFER_OPTIONS and chosen_buffer_size <= 0:
             messagebox.showerror("خطا", "اندازه بافر تست معتبر نیست.")
             print(f"DEBUG: Invalid test buffer size selected: {chosen_buffer_size}", file=sys.stderr)
             return


        # Set state flag BEFORE starting thread
        self.is_network_test_client_active = True
        utils.safe_gui_update(self.root, self._update_button_state) # Update UI state immediately

        # Clear cancel events for the new operation
        self.cancel_transfer_event.clear() # Clear transfer cancel too
        self.cancel_test_event.clear() # Clear test cancel event

        # Initial UI updates for progress/speed
        utils.safe_gui_update(self.root, self.gui_callbacks['update_progress'], 0)
        utils.safe_gui_update(self.root, self.gui_callbacks['update_speed'], "Speed: Starting Network Test Client...")


        # Start the network test client task in a separate thread using the tests module function
        tests.start_network_test_client(
            chosen_buffer_size,
            self.gui_callbacks, # Pass all GUI callbacks
            self.cancel_test_event # Pass the cancel event
        )
        print("DEBUG: Network test client thread requested.")
        # State will be reset by _on_test_sequence_finished called by the client task's finally block.


    def cancel_test_ui(self):
        print("DEBUG: Cancel test button pressed. Setting cancel_test_event.")
        # Check if any test operation is actually active before showing message/setting event
        if self.is_write_test_active or self.is_read_test_active or self.is_all_tests_active or self.is_network_test_client_active:
            utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] درخواست لغو تست...")
            self.cancel_test_event.set() # Set the event to signal cancellation
        else:
            utils.safe_gui_update(self.root, self.gui_callbacks['update_status'], "[*] تستی در حال حاضر برای لغو وجود ندارد.")


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
        # This is crucial for threads blocked on socket.accept() or socket.recv() with None timeout.
        try:
            if self.active_server_port is not None:
                print(f"DEBUG: Attempting unblock connection for file/folder server port {self.active_server_port}")
                # Using 127.0.0.1 (localhost) is usually sufficient to unblock a listener bound to 0.0.0.0
                # Use a short timeout for the connection attempt itself to avoid blocking here if the server is already fully down.
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5) # Short timeout
                try:
                     sock.connect(('127.0.0.1', self.active_server_port))
                     # Sending a minimal amount of data is sometimes necessary to fully unblock accept.
                     # The handler side (if started) should be robust enough to read/discard this small amount.
                     # Or, just closing immediately after connect might be enough depending on OS.
                     sock.sendall(b'\0') # Send a null byte to trigger activity
                except (ConnectionRefusedError, socket.timeout):
                     # Server socket is already closed or connection timed out, this is okay.
                     pass # Ignore expected errors during unblock attempt
                except Exception as e:
                     print(f"DEBUG: Unexpected error during unblock socket operation on {self.active_server_port}: {e}", file=sys.stderr)
                finally:
                     # Ensure the temporary socket is closed regardless of connect success/failure
                     sock.close()
                     print("DEBUG: Unblock connection attempt finished for file/folder server.")
        except Exception as e:
            # Catch any error during the initial socket creation for the unblocking attempt
            print(f"DEBUG: Failed to create socket for unblock attempt on {self.active_server_port}: {e}", file=sys.stderr)
            pass # Ignore errors during the unblocking attempt

        # Repeat for network test server if active
        try:
            if self.active_network_test_server_port is not None:
                print(f"DEBUG: Attempting unblock connection for network test server port {self.active_network_test_server_port}")
                 # Using 127.0.0.1 (localhost) is usually sufficient to unblock a listener bound to 0.0.0.0
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5) # Short timeout
                try:
                     sock.connect(('127.0.0.1', self.active_network_test_server_port))
                     sock.sendall(b'\0') # Send a null byte
                except (ConnectionRefusedError, socket.timeout):
                     pass # Ignore expected errors
                except Exception as e:
                     print(f"DEBUG: Unexpected error during unblock socket operation on {self.active_network_test_server_port}: {e}", file=sys.stderr)
                finally:
                     sock.close()
                     print("DEBUG: Unblock connection attempt finished for network test server.")
        except Exception as e:
             print(f"DEBUG: Failed to create socket for unblock attempt on {self.active_network_test_server_port}: {e}", file=sys.stderr)
             pass # Ignore errors


        # Manually reset state flags for UI clarity if needed (though they should be reset by finally blocks)
        # Adding a small delay before destroying GUI might help daemon threads finish cleanup
        print("DEBUG: Giving daemon threads a moment to shut down gracefully after receiving stop signals...")
        time.sleep(0.2) # Slightly increased delay to allow more graceful shutdown


        self.is_server_running = False
        self.is_network_test_server_running = False
        self.is_transfer_active = False
        self.is_write_test_active = False
        self.is_read_test_active = False
        self.is_all_tests_active = False
        self.is_network_test_client_active = False

        # Attempt to destroy the root window
        print("DEBUG: Calling root.destroy()")
        # Check if root window still exists before destroying
        if self.root and hasattr(self.root, 'destroy') and self.root.winfo_exists():
             try:
                 self.root.destroy()
                 print("DEBUG: root.destroy() succeeded.")
             except Exception as e:
                 print(f"DEBUG: Error during root.destroy(): {e}", file=sys.stderr)
        else:
             print("DEBUG: root window did not exist or was already destroyed.")

        # In a simple script, sys.exit(0) can be used to force exit if daemon threads are still stuck,
        # but ideally, root.destroy() and daemon=True should be enough for most cases.
        # sys.exit(0) # Use with caution if needed

    def run(self):
        print("DEBUG: Starting root.mainloop()")
        self.root.mainloop()
        print("DEBUG: root.mainloop() finished.")