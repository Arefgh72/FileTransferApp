# transfer_core/handlers.py
# این فایل فقط برای اینکه server.py بتواند توابع را از اینجا ایمپورت کند وجود دارد.
# منطق اصلی در فایل های single_file_handler.py و folder_handler.py قرار دارد.

from .single_file_handler import handle_client_connection
from .folder_handler import handle_client_folder_transfer

# حالا توابع handle_client_connection و handle_client_folder_transfer
# از طریق import transfer_core.handlers.handle_client_connection
# و import transfer_core.handlers.handle_client_folder_transfer در دسترس هستند.
# و همچنین از طریق import transfer_core.handlers
# و سپس دسترسی به handlers.handle_client_connection