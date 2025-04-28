import sys
from cx_Freeze import setup, Executable

# Base can be "None" to include the console window, or "Win32GUI" to hide it.
# Use "Win32GUI" for a windowed application without console.
base = None
if sys.platform == "win32":
    base = "Win32GUI"

# Optional: List any non-Python data files (like your icon) here.
# The icon itself is specified in the Executable definition.
files_to_include = [] # لیست فایل های دیتای اضافی (مثل تصاویر یا فایل های متنی که در کد باز میشن)

# List packages to include directly in the executable zip file
# By default, cx_Freeze includes packages in a separate lib directory.
# For onefile, we want them included inside the .exe itself (in a zip).
# Specify packages your application directly uses.
packages_to_zip = ["tkinter", "socket", "os", "threading", "time", "sys", "math", "random",
                   "config", "utils", "filetransfer", "tests", "json", "base64", "email"] # Add any other standard libs your code uses

# You can exclude packages that are large and maybe not strictly necessary,
# or exclude packages that are already in the standard library that cx_Freeze
# might package separately but you want inside the zip.
# This is often for fine-tuning size. For simplicity, include most.
# packages_to_exclude_from_zip = []

setup(
    name="FileTransferApp", # Change this to your application name
    version="1.2",
    description="File Transfer and Network Test Application",
    options={
        "build_exe": {
            "packages": [], # Packages are handled by zip_include_packages
            "include_files": files_to_include,
            # IMPORTANT for onefile: Include your source files and other modules
            # as data files, so cx_Freeze can find them.
            # Or, rely on cx_Freeze auto-detection, but specifying can help.
            # "include_files": [(r"C:\Users\...\file_transfer-v1.2\config.py", "config.py"), ...], # specify full paths if not in same dir

             # Configuration for the executable zip file
             "zip_include_packages": packages_to_zip,
             "zip_exclude_packages": [], # Adjust if you want to exclude specific packages from the zip
             "excludes": [], # Exclude unwanted modules
             "include_msvcr": True, # Include Visual C++ runtime DLLs
         }
    },
    executables=[
        Executable(
            "file_transfer-v1.2.py", # Your main script
            base=base, # Hide console window
            icon="Paimon.ico", # Your icon file (.ico)
            # For onefile, set target_name to the desired executable name
            target_name="FileTransferApp.exe" # Desired name of the final .exe file
        )
    ]
)