
# main.py - Application entry point

import tkinter as tk
import sys
import os

# Add the directory containing the modules to the Python path.
if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)

# Now perform absolute imports of the main GUI module
import gui

print("DEBUG: Application starting...")
root = tk.Tk()
app = gui.Application(root)
app.run()
print("DEBUG: Application finished.")