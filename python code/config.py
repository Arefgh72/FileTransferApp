# config.py - Application configuration settings

# Ports
DISCOVERY_PORT = 5000       # UDP port for server discovery broadcast (File Transfer)
NETWORK_TEST_DISCOVERY_PORT = 5010 # UDP port for network test server discovery broadcast

# List of potential TCP ports for servers to try binding
FILE_TRANSFER_PORTS = [5001, 5002, 5003, 5004, 5005] # TCP ports for file transfer (used for both single file and folder transfer)
NETWORK_TEST_PORTS = [5011, 5012, 5013, 5014, 5015] # TCP ports for network test

# Buffer sizes
BUFFER_SIZE_FOR_HEADER = 2048 # Buffer for receiving initial headers (should be large enough for header string)

# Separators used in network messages
HEADER_SEPARATOR = "|" # Separator for header parts (used in all protocols)

# Discovery messages
DISCOVERY_MESSAGE = "FIND_FILE_SERVER_XYZ" # Message sent by client to find file server
SERVER_RESPONSE_BASE = "IM_FILE_SERVER_XYZ" # Base response from file server
NETWORK_TEST_DISCOVERY_MESSAGE = "FIND_NETWORK_TEST_SERVER_XYZ" # Message sent by client to find network test server
NETWORK_TEST_SERVER_RESPONSE_BASE = "IM_NETWORK_TEST_SERVER_XYZ" # Base response from network test server

# --- Folder Transfer Protocol Constants (without compression) ---
# These constants define the messages exchanged for folder transfers.
# The server will need to read the initial header bytes to determine if it's a single file transfer
# (using the old protocol filename|filesize|buffersize) or a folder transfer (using the new protocol prefix).

FOLDER_PROTOCOL_PREFIX = "FDR_V1" # Prefix to identify the new folder transfer protocol version

# Item types within a folder transfer
FOLDER_HEADER_TYPE_FOLDER = "FOLDER" # Indicates the item being described is a directory
FOLDER_HEADER_TYPE_FILE = "FILE"     # Indicates the item being described is a file
FOLDER_HEADER_TYPE_END_TRANSFER = "END_TRANSFER" # Indicates the end of the entire folder transfer

# --- New constants for Count/Size Verification ---
FOLDER_HEADER_TYPE_TOTAL_INFO = "TOTAL_INFO" # Indicates the item being described is total count/size info
TOTAL_INFO_COUNT_SIZE_SEPARATOR = "," # Separator specifically for count and size within the TOTAL_INFO header


# Handshake messages (used after END_TRANSFER)
HANDSHAKE_REQUEST_SIGNAL = b"IS_TRANSFER_COMPLETE?" # Client sends this after END_TRANSFER
HANDSHAKE_COMPLETE_OK_SIGNAL = b"TRANSFER_COMPLETE_OK" # Server response if OK
HANDSHAKE_ERROR_SIGNAL = b"TRANSFER_ERROR" # Server response if error occurred (e.g., verification failed)

# NEW: Buffer size for reading handshake responses/requests
HANDSHAKE_READ_BUFFER_SIZE = 1024 # Buffer size for receiving handshake signals


# Network Test Settings
NETWORK_TEST_SIZE = 100 * 1024 * 1024 # Amount of data to send/receive during network test (100 MB)
NETWORK_TEST_PROTOCOL_HEADER = "NET_TEST_START" # Message sent by client to initiate network test transfer

# Network timeouts and intervals
DISCOVERY_TIMEOUT = 5 # Seconds client waits for server discovery response
SPEED_UPDATE_INTERVAL = 0.5 # Seconds between updating speed display during transfer/test
CANCEL_CHECK_INTERVAL = 0.5 # Seconds timeout for blocking calls (like recv/send) or file I/O to allow checking cancel events periodically

# Handshake timeout (client side waits for server response)
HANDSHAKE_TIMEOUT = 30 # Seconds to wait for handshake response

# NEW: Timeout specifically for socket send/recv operations during data transfer
# Using a reasonable value that allows for network latency but not too long if peer is truly stuck.
DATA_TRANSFER_TIMEOUT = 10.0 # Increased from 5.0 based on testing potential issues


# --- Drive Test Settings ---
TEST_FILE_SIZE = 100 * 1024 * 1024 # Size of the temporary file used for drive speed tests (100 MB)
TEST_FILE_NAME = "._speed_test_file_.tmp" # Name of the temporary file (hidden on some systems with '.')

# Options for Buffer Size Comboboxes
BUFFER_OPTIONS = {
    "Small (4 KB)": 4096,
    "Medium (16 KB)": 16384,
    "Large (64 KB)": 65536,
    "X-Large (256 KB)": 262144,
    "Mega (1 MB)": 1048576,
    "4 Mega (4 MB)": 4 * 1024 * 1024,
    "16 Mega (16 MB)": 16 * 1024 * 1024,
    # می توانید بافرهای بزرگتر دیگر را نیز اینجا اضافه کنید.
    # "64 Mega (64 MB)": 64 * 1024 * 1024,
}