File Transfer App - Version 1.3 Latest

This release, Version 1.3, introduces the Folder Transfer capability, allowing users to send and receive entire directories along with their contents. This version also includes significant refinements to the new folder transfer protocol for enhanced reliability.

Key New Features and Improvements in this version (v1.3):

New Feature: Folder Transfer: Added the ability to select and transfer entire folders, preserving the directory structure on the receiving end.
Robust Folder Protocol Implementation: Addressed critical issues discovered during the development and testing of the folder transfer feature, ensuring stable and reliable transfer of all files and subdirectories, including large files.
Folder Handshake with Verification: Implemented a handshake mechanism specifically for folder transfers. This handshake includes a verification step at the end to confirm that the receiver has received the correct total number of items (files and folders) and the correct total accumulated size declared by the sender.
Refined Protocol State Management: Improved the handling of different states during folder receive to correctly process headers and data, and manage the transition to the handshake phase.
Enhanced Error Handling for Folder Transfers: Improved error detection and reporting specifically for the folder transfer process, providing clearer feedback in case of issues.
Improved File Handling within Folders: Ensured proper handling of various file types and sizes within folder transfers, including efficient management of 0-byte files.
General Stability Improvements: Applied minor refinements to general socket communication and threading logic based on insights gained during the development of the folder transfer feature.
This version represents a major step forward by adding folder transfer, making the application much more versatile for sharing data over the local network.

For File/Folder Transfer (including disk I/O speed impact): Run the application on two devices, start Receiver mode on one and select/send a file or folder from the other.
For Pure Network Speed Test (excluding disk I/O speed): Run the application on two devices, start Network Test Receiver on one and Network Test Sender on the other.
Drive tests are run locally on a single machine.
