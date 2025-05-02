# PyShare App - Share Files & Chat on Your Local Network

Welcome to PyShare! This simple application turns your computer into a mini-server, allowing you to easily share files and chat with others connected to the **same Wi-Fi or local network** (like your home or office network). No internet connection is needed for sharing!

## What it Does

- **File Sharing:** Upload files to the server running on your computer. Anyone else on your network can log in and download those files.
- **Chat:** Send and receive text messages in real-time with others logged into the app.
- **Local Network Only:** All communication and file transfers happen directly between devices on your network, without using the internet.
- **Two Storage Options:**
  - **Persistent Files:** Files uploaded here stay until an admin deletes them.
  - **Session Files:** Files uploaded here are temporary and can be deleted by the uploader or when they end their session.
- **Real-time Updates:** See new messages and files appear instantly without refreshing the page.
- **User Accounts:** Supports multiple users with basic admin privileges.

## Requirements

Before you start, you need a couple of things installed on the computer that will _run_ the server:

1.  **Python:** A programming language. You likely already have it. You can check by opening Command Prompt (Windows) or Terminal (Mac/Linux) and typing `python --version` or `python3 --version`. If not installed, download it from [python.org](https://www.python.org/). (Version 3.7 or higher recommended).
2.  **Pip:** Python's package installer, usually comes with Python.

## Setup Instructions

Follow these steps on the computer that will host the files and chat:

1.  **Get the Code:**

    - Download or copy the application code file (it likely ends with `.py`, for example `local_share_server.py` or `pyshare_server.py`).
    - Save this file into a dedicated folder on your computer (e.g., create a folder named `PyShareApp`).

2.  **Install Required Libraries:**

    - Open your Command Prompt or Terminal.
    - Navigate to the folder where you saved the code file using the `cd` command (e.g., `cd C:\Users\YourName\Documents\PyShareApp`).
    - Run the following command to install the necessary software libraries:
      ```bash
      pip install Flask Flask-SocketIO eventlet Werkzeug
      ```
      _(If `pip` doesn't work, try `pip3`)_

3.  **Configure Users (`users.json`):**

    - In the _same folder_ as the code file, create a new text file named exactly `users.json`.
    - Open `users.json` with a simple text editor (like Notepad or TextEdit).
    - Copy and paste the following structure into the file. You **must** change the passwords!

      ```json
      [
        {
          "device_id": "admin",
          "password": "replace_with_admin_password",
          "is_admin": true
        },
        {
          "device_id": "user1",
          "password": "replace_with_user1_password",
          "is_admin": false
        },
        {
          "device_id": "user2",
          "password": "replace_with_user2_password",
          "is_admin": false
        }
      ]
      ```

    - **Explanation:**
      - `device_id`: This is the username people will use to log in. Keep it simple (letters/numbers).
      - `password`: **Change these default passwords** to something secure!
      - `is_admin`: Set this to `true` for at least one user (the administrator). Admins can delete _any_ file in the "Persistent Files" section. Set it to `false` for regular users.
    - You can add more users by copying the blocks within the `[...]` and changing the details. Make sure each block is separated by a comma (except the last one).

4.  **Run the Server:**

    - Make sure you are still in the correct folder in your Command Prompt or Terminal.
    - Run the Python script using:
      ```bash
      python your_script_name.py
      ```
      _(Replace `your_script_name.py` with the actual name you saved the code file as. If `python` doesn't work, try `python3`)_
    - You should see messages indicating the server is starting, including potential IP addresses to use. It will look something like this:

      ```
       * Starting LocalShare Server...
       * Users File: ./users.json
       * Persistent Storage: C:\Path\To\Your\App\nas_uploads_persistent
       * Session Storage: C:\Path\To\Your\App\nas_uploads_session
       * Access URLs (try these on your local network):
         - [http://192.168.1.10:5000](http://192.168.1.10:5000)
         - [http://10.0.0.5:5000](http://10.0.0.5:5000)
       * Using eventlet for async SocketIO.
       * WARNING: Passwords in users.json are plain text...
       * WARNING: Debug mode should be OFF for production.
       * Running on [http://0.0.0.0:5000/](http://0.0.0.0:5000/) (Press CTRL+C to quit)
      ```

## How to Use

1.  **Find the Server Address:** Look at the output in the terminal where you ran the server. Find one of the `http://...:5000` addresses listed under "Access URLs". This is usually an address like `192.168.x.x` or `10.x.x.x`. This is the address of the computer running the server on your local network.
2.  **Connect from Other Devices:**
    - On any other computer, phone, or tablet connected to the **same Wi-Fi/local network**, open a web browser (Chrome, Firefox, Safari, etc.).
    - Type the server address you found in step 1 into the browser's address bar (e.g., `http://192.168.1.10:5000`) and press Enter.
3.  **Login:** You should see the PyShare login page. Enter the `device_id` and `password` for one of the users you configured in the `users.json` file.
4.  **Using the Interface:**
    - **Sidebar:** Use the icons on the left to switch between "Persistent Files & Chat" and "Session Files & Chat".
    - **Main Area:** This shows a combined view of chat messages and uploaded files, sorted by time (newest at the bottom). Files uploaded to the currently selected storage (Persistent or Session) will appear here. Chat messages appear in both views.
    - **Sending Messages:** Type your message in the input box at the bottom and click the send button (arrow icon).
    - **Uploading Files:**
      - Click the paperclip icon next to the message input box.
      - Choose the file you want to upload. The filename will appear next to the icon.
      - Click the upload button (up arrow icon).
      - The file will be saved to the currently selected storage type (Persistent or Session).
    - **Downloading Files:** Click on the name of any file shown in the main area to download it.
    - **Deleting Items:**
      - A small red 'X' button appears in the bottom-right corner of messages and files you have permission to delete.
      - **Messages:** You can only delete messages _you_ sent.
      - **Session Files:** You can only delete session files _you_ uploaded.
      - **Persistent Files:** Only users marked as `is_admin: true` in `users.json` can delete persistent files.
    - **Ending Session:** If you are in the "Session Files" view, you can click "End My Session Files" at the bottom to delete _all_ session files _you_ uploaded.

## Stopping the Server

- Go back to the Command Prompt or Terminal window where the server is running.
- Press `Ctrl + C` (hold the Ctrl key and press C).
- The server will stop.

## Troubleshooting

- **Cannot Connect:**
  - Make sure both the server computer and the device trying to connect are on the _exact same_ Wi-Fi network.
  - Double-check the IP address you are typing into the browser.
  - **Firewall:** The firewall on the server computer might be blocking connections. You may need to allow incoming connections for Python or specifically for port 5000. (Search online for "allow port through firewall windows/mac" for instructions).
- **Login Failed:** Double-check the `device_id` and `password` you entered match exactly what's in the `users.json` file (case-sensitive).
- **Errors on Startup:** Make sure you installed all the required libraries (`pip install ...`). Ensure the `eventlet.monkey_patch()` line is at the very top of the Python script. Check that the `users.json` file has the correct format.

Enjoy sharing locally with PyShare!
