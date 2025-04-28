# config.py - Application configuration settings

# Ports
DISCOVERY_PORT = 5000       # UDP port for server discovery broadcast (File Transfer)
NETWORK_TEST_DISCOVERY_PORT = 5010 # UDP port for network test server discovery broadcast

# List of potential TCP ports for servers to try binding
FILE_TRANSFER_PORTS = [5001, 5002, 5003, 5004, 5005] # TCP ports for file transfer
NETWORK_TEST_PORTS = [5011, 5012, 5013, 5014, 5015] # TCP ports for network test

# Buffer sizes
BUFFER_SIZE_FOR_HEADER = 2048 # Buffer for receiving initial headers (should be large enough for header string)

# Separators used in network messages
HEADER_SEPARATOR = "|" # Separator for header parts (filename|filesize|buffersize or testsize|buffersize)

# Discovery messages
DISCOVERY_MESSAGE = "FIND_FILE_SERVER_XYZ" # Message sent by client to find file server
SERVER_RESPONSE_BASE = "IM_FILE_SERVER_XYZ" # Base response from file server
NETWORK_TEST_DISCOVERY_MESSAGE = "FIND_NETWORK_TEST_SERVER_XYZ" # Message sent by client to find network test server
NETWORK_TEST_SERVER_RESPONSE_BASE = "IM_NETWORK_TEST_SERVER_XYZ" # Base response from network test server

# Network Test Settings
NETWORK_TEST_SIZE = 100 * 1024 * 1024 # Amount of data to send/receive during network test (100 MB)
NETWORK_TEST_PROTOCOL_HEADER = "NET_TEST_START" # Message sent by client to initiate network test transfer

# Network timeouts and intervals
DISCOVERY_TIMEOUT = 5 # Seconds client waits for server discovery response
SPEED_UPDATE_INTERVAL = 0.5 # Seconds between updating speed display during transfer/test
CANCEL_CHECK_INTERVAL = 0.05 # Seconds timeout for blocking calls (like recv/send) or file I/O to allow checking cancel events periodically

# --- Drive Test Settings ---
TEST_FILE_SIZE = 100 * 1024 * 1024 # Size of the temporary file used for drive speed tests (100 MB)
TEST_FILE_NAME = "._speed_test_file_.tmp" # Name of the temporary file (hidden on some systems with '.')

# Options for Buffer Size Comboboxes (removed "Auto")
BUFFER_OPTIONS = {
    "Small (4 KB)": 4096,
    "Medium (16 KB)": 16384,
    "Large (64 KB)": 65536,
    "X-Large (256 KB)": 262144,
    "Mega (1 MB)": 1048576,
    # اضافه کردن بافرهای بزرگتر طبق درخواست:
    "4 Mega (4 MB)": 4 * 1024 * 1024,
    "16 Mega (16 MB)": 16 * 1024 * 1024,
    # می توانید بافرهای بزرگتر دیگر را نیز اینجا اضافه کنید.
    # "64 Mega (64 MB)": 64 * 1024 * 1024,
}