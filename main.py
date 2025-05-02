# --- IMPORTANT: Monkey patch must happen first ---
import eventlet
eventlet.monkey_patch()
# --- End Monkey Patch ---

# Import necessary libraries AFTER monkey patching
from flask import (
    Flask, request, redirect, url_for, session, render_template_string,
    send_from_directory, flash, abort, jsonify
)
from flask_socketio import SocketIO, emit
import os
import datetime
import math
import json
import uuid # For unique message IDs
import socket # To find local IP
from werkzeug.utils import secure_filename
# from werkzeug.security import generate_password_hash, check_password_hash
import time

# --- Configuration ---
UPLOAD_FOLDER = './nas_uploads_persistent'
SESSION_FOLDER = './nas_uploads_session'
USERS_FILE = './users.json'
SECRET_KEY = '123@123@telegram'
MAX_CHAT_HISTORY = 100 # Max number of recent messages to keep in memory

# --- Flask Application Setup ---
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['UPLOAD_FOLDER'] = os.path.abspath(UPLOAD_FOLDER)
app.config['SESSION_FOLDER'] = os.path.abspath(SESSION_FOLDER)

# --- SocketIO Setup ---
socketio = SocketIO(app, cors_allowed_origins="*")

# --- Real-time Tracking ---
active_user_sids = {} # user_id -> set(sids)
chat_log = [] # In-memory storage for recent chat messages

# --- Helper Functions ---

def get_local_ips():
    """Tries to get likely local IP addresses."""
    ips = []
    try:
        hostname = socket.gethostname()
        # Try getting all IPs associated with the hostname
        try:
            name, aliases, ipaddrs = socket.gethostbyname_ex(hostname)
            for ip in ipaddrs:
                if not ip.startswith("127."): # Exclude loopback
                    ips.append(ip)
        except socket.gaierror:
            pass # Could not resolve hostname fully

        # Fallback: Try connecting to an external host (doesn't send data)
        # This often reveals the primary outbound IP
        if not ips:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.settimeout(0.1) # Avoid long wait
                    s.connect(('8.8.8.8', 80)) # Google DNS server
                    ip = s.getsockname()[0]
                    if not ip.startswith("127."):
                         ips.append(ip)
            except Exception:
                pass # Connection failed

        if not ips:
             ips.append("127.0.0.1 (Loopback Only Found)")

        return list(set(ips)) # Return unique IPs
    except Exception as e:
        print(f"Warning: Could not determine local IP addresses: {e}")
        return ["<Could not determine IP>"]


def add_chat_message(sender, text):
    """Adds a message to the in-memory log and ensures max history size."""
    global chat_log
    if not text or not sender:
        return None

    timestamp = datetime.datetime.now(datetime.timezone.utc)
    message = {
        'id': str(uuid.uuid4()), # Unique ID for deletion
        'sender': sender,
        'text': text.strip(), # Remove leading/trailing whitespace
        'timestamp_iso': timestamp.isoformat(),
        'timestamp_formatted': timestamp.astimezone().strftime('%b %d, %H:%M'), # Local time for display
        'type': 'message'
    }
    chat_log.append(message)
    # Trim log if it exceeds max size
    if len(chat_log) > MAX_CHAT_HISTORY:
        chat_log = chat_log[-MAX_CHAT_HISTORY:]
    return message

def delete_chat_message(message_id, requesting_user):
    """Deletes a message if the requesting user is the sender."""
    global chat_log
    original_length = len(chat_log)
    # Filter out the message to be deleted, checking sender
    chat_log = [msg for msg in chat_log if not (msg['id'] == message_id and msg['sender'] == requesting_user)]
    return len(chat_log) < original_length # Return True if deletion happened


def load_users():
    if not os.path.exists(USERS_FILE):
        default_users = [{"device_id": "admin", "password": "password123", "is_admin": True}]
        try:
            with open(USERS_FILE, 'w') as f: json.dump(default_users, f, indent=2)
            print(f"Created default users file: {USERS_FILE} with user 'admin'")
            return default_users
        except IOError as e: print(f"FATAL: Could not create users file {USERS_FILE}: {e}"); return []
    try:
        with open(USERS_FILE, 'r') as f: users = json.load(f)
        if not isinstance(users, list): print(f"Error: {USERS_FILE} invalid format."); return []
        return users
    except (IOError, json.JSONDecodeError) as e: print(f"Error loading users from {USERS_FILE}: {e}"); return []

def get_human_readable_size(size_bytes):
    if size_bytes is None or size_bytes < 0: return "N/A"
    if size_bytes == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(max(1, size_bytes), 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def get_file_metadata(directory, file_type='persistent'):
    files_metadata = []
    if not os.path.isdir(directory): return []
    try:
        with app.app_context(): # Needed for url_for
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                if os.path.isfile(file_path):
                    try:
                        stats = os.stat(file_path)
                        parts = filename.split('_', 2)
                        uploader, timestamp_str, original_name = "Unknown", "0", filename
                        if len(parts) >= 3: uploader, timestamp_str, original_name = parts[0], parts[1], parts[2]
                        elif len(parts) == 2: uploader, original_name = parts[0], parts[1]
                        try: upload_dt = datetime.datetime.fromtimestamp(float(timestamp_str))
                        except ValueError: upload_dt = datetime.datetime.fromtimestamp(stats.st_mtime)
                        download_url = url_for('download_file', filename=filename, type=file_type, _external=False)
                        files_metadata.append({
                            'id': filename, # Use filename as ID for files
                            'full_name': filename, 'display_name': original_name, 'uploader': uploader,
                            'size_bytes': stats.st_size, 'size_human': get_human_readable_size(stats.st_size),
                            'timestamp_iso': upload_dt.isoformat(),
                            'timestamp_formatted': upload_dt.astimezone().strftime('%b %d, %H:%M'),
                            'type': 'file', # Explicitly mark as file
                            'file_type': file_type, # persistent or session
                            'download_url': download_url
                        })
                    except Exception as e: print(f"Warning: Error processing file {filename}: {e}")
            # No sorting here, will sort combined list later
            return files_metadata
    except Exception as e: print(f"Error accessing directory {directory}: {e}"); return []


def ensure_dirs_exist():
    for folder in [app.config['UPLOAD_FOLDER'], app.config['SESSION_FOLDER']]:
        if not os.path.exists(folder):
            try: os.makedirs(folder); print(f"Created directory: {folder}")
            except OSError as e: print(f"FATAL: Could not create directory {folder}: {e}"); import sys; sys.exit(1)

def get_active_user_count(): return len(active_user_sids)

def is_user_admin(user_id):
    users = load_users()
    for user in users:
        if user.get('device_id') == user_id: return user.get('is_admin', False)
    return False

# --- HTML Templates ---

# Login Template (Unchanged)
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Local Share</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap" rel="stylesheet">
    <style> body { font-family: 'Inter', sans-serif; } </style>
</head>
<body class="bg-gray-900 flex items-center justify-center min-h-screen p-4">
    <div class="bg-gray-800 p-8 rounded-xl shadow-lg w-full max-w-sm border border-gray-700">
        <h2 class="text-3xl font-bold mb-6 text-center text-gray-100">Local Share Login</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="mb-4 p-3 rounded-lg text-sm text-center border
                 {% if category == 'error' %} bg-red-900 border-red-700 text-red-100
                 {% else %} bg-blue-900 border-blue-700 text-blue-100 {% endif %}">
                {{ message }}
              </div>
            {% endfor %}
          {% endif %}
        {% endwith %}
        <form method="post">
            <div class="mb-5">
                <label for="device_id" class="block text-gray-400 text-sm font-semibold mb-2">Device ID:</label>
                <input type="text" id="device_id" name="device_id" required
                       class="shadow-sm appearance-none bg-gray-700 border border-gray-600 rounded-lg w-full py-2.5 px-4 text-gray-100 leading-tight placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent">
            </div>
            <div class="mb-6">
                <label for="password" class="block text-gray-400 text-sm font-semibold mb-2">Password:</label>
                <input type="password" id="password" name="password" required
                       class="shadow-sm appearance-none bg-gray-700 border border-gray-600 rounded-lg w-full py-2.5 px-4 text-gray-100 mb-3 leading-tight placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent">
            </div>
            <div class="flex items-center justify-center mt-8">
                <button type="submit"
                        class="w-full bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 text-white font-bold py-2.5 px-4 rounded-lg focus:outline-none focus:shadow-outline transition duration-150 ease-in-out">
                    Log In
                </button>
            </div>
        </form>
    </div>
</body>
</html>
"""

# Main UI Template (Updated for Chat Input and Combined Feed)
MAIN_UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" class="h-full">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Local Share</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #111827; }
        .chat-area::-webkit-scrollbar { width: 8px; }
        .chat-area::-webkit-scrollbar-track { background: #1f2937; border-radius: 4px;}
        .chat-area::-webkit-scrollbar-thumb { background: #4b5563; border-radius: 4px; }
        .chat-area::-webkit-scrollbar-thumb:hover { background: #6b7280; }
        html, body { height: 100%; margin: 0; padding: 0; overflow: hidden; }
        .main-container { display: flex; height: 100vh; }
        .sidebar { width: 70px; flex-shrink: 0; background-color: #1f2937; border-right: 1px solid #374151; }
        .content-wrapper { flex-grow: 1; display: flex; flex-direction: column; overflow: hidden; background-color: #111827; }
        .chat-header { flex-shrink: 0; background-color: #1f2937; border-bottom: 1px solid #374151; }
        .chat-area { flex-grow: 1; overflow-y: auto; position: relative; display: flex; flex-direction: column-reverse; /* Reverse order for chat */ }
        .input-area { flex-shrink: 0; background-color: #1f2937; border-top: 1px solid #374151; }
        .notification { position: absolute; top: 10px; left: 50%; transform: translateX(-50%); padding: 8px 16px; border-radius: 8px; font-size: 0.9em; z-index: 50; transition: opacity 0.5s ease-out; opacity: 0; pointer-events: none; }
        .notification.show { opacity: 1; }
        .notification.login { background-color: #10B981; color: white; }
        .notification.logout { background-color: #F59E0B; color: white; }
        .notification.error { background-color: #EF4444; color: white; }
        /* Style for message text to allow line breaks */
        .message-text { white-space: pre-wrap; word-wrap: break-word; }
        /* Delete button positioning */
        .item-bubble { position: relative; /* Parent needs to be relative */ }
        .delete-btn {
            position: absolute;
            bottom: 2px; /* Adjust as needed */
            right: 2px; /* Adjust as needed */
            opacity: 0.6; /* Make slightly visible */
            transition: opacity 0.2s ease-in-out;
        }
        .group:hover .delete-btn { opacity: 1; /* Fully visible on hover */ }

         @media (max-width: 768px) { .chat-header h1 { font-size: 1.1rem; } .chat-header .user-info { font-size: 0.8rem; } }
         .mobile-active-users { display: none; }
         @media (max-width: 640px) { .mobile-active-users { display: inline-block; } .desktop-user-info { display: none; } }
    </style>
</head>
<body class="text-gray-300 h-full">
    <div class="main-container h-full">
        <aside class="sidebar flex flex-col items-center py-4 space-y-6 h-full">
             <div class="w-10 h-10 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-full flex items-center justify-center font-bold text-white mb-4 shadow-md">
                {{ session.user_id[0].upper() if session.user_id else '?' }}
            </div>
            <nav class="flex flex-col items-center space-y-5 flex-grow">
                 <a href="{{ url_for('index') }}" title="Persistent Files" class="p-2 rounded-lg {% if current_page == 'index' %}bg-gray-700 text-white{% else %}text-gray-400 hover:bg-gray-700 hover:text-white{% endif %} transition duration-150">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" /></svg>
                </a>
                 <a href="{{ url_for('session_files') }}" title="Session Files" class="p-2 rounded-lg {% if current_page == 'session' %}bg-gray-700 text-white{% else %}text-gray-400 hover:bg-gray-700 hover:text-white{% endif %} transition duration-150">
                     <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                </a>
                <a href="#" title="Settings (Placeholder)" class="p-2 rounded-lg text-gray-400 hover:bg-gray-700 hover:text-white transition duration-150">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826 3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
                </a>
            </nav>
            <a href="{{ url_for('logout') }}" title="Logout" class="p-2 rounded-lg text-gray-400 hover:bg-gray-700 hover:text-white transition duration-150 mt-auto mb-2">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" /></svg>
             </a>
        </aside>

        <div class="content-wrapper">
            <header class="chat-header p-4 flex justify-between items-center">
                 <h1 class="text-xl font-semibold text-gray-100">
                    {% if current_page == 'session' %}Session Files & Chat{% else %}Persistent Files & Chat{% endif %}
                 </h1>
                 <div class="user-info text-sm text-gray-400">
                    <span class="desktop-user-info">User: <strong class="font-medium text-gray-200">{{ session.user_id }}</strong> {% if session.is_admin %}<span class="text-xs text-yellow-400">(Admin)</span>{% endif %} | Active: <strong id="active-users-count" class="text-green-400">{{ initial_active_users_count }}</strong></span>
                    <span class="mobile-active-users"><strong id="mobile-active-users-count" class="text-green-400">{{ initial_active_users_count }}</strong> online</span>
                 </div>
            </header>

            <main class="chat-area flex-grow p-4 md:p-6 lg:p-8 space-y-5" id="item-list-area"> <div id="notification-area" class="notification"></div>
                {# Display initial flashed messages #}
                {% with messages = get_flashed_messages(with_categories=true) %}
                  {% if messages %}
                    {% for category, message in messages %}
                      <div class="server-flash-message mb-4 p-3 rounded-lg text-sm sticky top-2 z-10 border text-center
                         {% if category == 'error' %} bg-red-900 border-red-700 text-red-100
                         {% elif category == 'success' %} bg-green-900 border-green-700 text-green-100
                         {% else %} bg-blue-900 border-blue-700 text-blue-100 {% endif %}">
                        {{ message }}
                      </div>
                    {% endfor %}
                  {% endif %}
                {% endwith %}
                <div id="no-items-message" class="flex justify-center my-auto {% if initial_items %}hidden{% endif %}"> <div class="bg-gray-800 border border-gray-700 p-6 rounded-lg shadow text-center max-w-md">
                        <p class="text-gray-400">No messages or files yet.</p>
                        <p class="text-gray-500 text-sm mt-2">Send a message or upload a file below.</p>
                    </div>
                </div>
                 <p class="text-center text-gray-600 text-xs pt-4 mt-auto"> Storage: {% if current_page == 'session' %}Session (Temporary){% else %}Persistent{% endif %}
                 </p>
            </main>

            <footer class="input-area p-4">
                 <form id="message-form" class="flex items-center space-x-3 max-w-4xl mx-auto mb-3">
                     <input type="text" id="message-input" placeholder="Type your message here..." required autocomplete="off"
                            class="flex-grow shadow-sm appearance-none bg-gray-700 border border-gray-600 rounded-lg w-full py-2.5 px-4 text-gray-100 leading-tight placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent">
                     <button type="submit" class="bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 text-white font-bold p-2.5 rounded-full focus:outline-none focus:shadow-outline transition duration-150 ease-in-out">
                         <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-8.707l-3-3a1 1 0 00-1.414 1.414L10.586 9H7a1 1 0 100 2h3.586l-1.293 1.293a1 1 0 101.414 1.414l3-3a1 1 0 000-1.414z" clip-rule="evenodd" /></svg>
                     </button>
                 </form>
                 <form id="upload-form" method="post" enctype="multipart/form-data" class="flex items-center space-x-3 max-w-4xl mx-auto">
                    <label for="file" class="p-2 text-gray-400 hover:text-indigo-400 rounded-full hover:bg-gray-700 cursor-pointer" title="Attach File">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" /></svg>
                    </label>
                    <div class="flex-grow">
                         <input type="file" name="file" id="file" class="hidden"> <span id="file-name-display" class="text-sm text-gray-500">No file chosen</span>
                    </div>
                    <button type="submit" id="upload-button" class="bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 text-white font-bold p-2.5 rounded-full focus:outline-none focus:shadow-outline transition duration-150 ease-in-out" title="Upload File">
                         <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 10l7-7m0 0l7 7m-7-7v18" /></svg>
                    </button>
                </form>
                 {% if current_page == 'session' %}
                 <div class="text-center mt-3">
                     <form id="end-session-form" method="post">
                          <button type="submit" class="text-xs text-red-400 hover:text-red-300 hover:underline">End My Session Files</button>
                     </form>
                 </div>
                 {% endif %}
            </footer>
        </div> </div> <script>
        const socket = io();
        const currentUserId = "{{ session.user_id }}";
        const currentUserIsAdmin = {{ session.is_admin | tojson }};
        const currentPage = "{{ current_page }}";
        const itemListArea = document.getElementById('item-list-area'); // Renamed
        const noItemsMessage = document.getElementById('no-items-message'); // Renamed
        const activeUsersCountSpan = document.getElementById('active-users-count');
        const mobileActiveUsersCountSpan = document.getElementById('mobile-active-users-count');
        const notificationArea = document.getElementById('notification-area');
        const uploadForm = document.getElementById('upload-form');
        const messageForm = document.getElementById('message-form');
        const messageInput = document.getElementById('message-input');
        const fileInput = document.getElementById('file');
        const fileNameDisplay = document.getElementById('file-name-display');
        const endSessionForm = document.getElementById('end-session-form');

        // --- Helper: Show Notification ---
        function showNotification(message, type = 'login', duration = 4000) {
            if (!notificationArea) return;
            notificationArea.textContent = message;
            const typeClass = type === 'error' ? 'error' : (type === 'logout' ? 'logout' : 'login');
            notificationArea.className = `notification ${typeClass} show`;
            setTimeout(() => { notificationArea.classList.remove('show'); }, duration);
        }

        // --- Helper: Add Item (File or Message) to DOM ---
        function addItemToDOM(item) {
            // Filter items not relevant to the current page (only applies to files)
            if (item.type === 'file' && item.file_type !== currentPage) return;

            if (noItemsMessage) noItemsMessage.classList.add('hidden');

            const isCurrentUser = item.sender === currentUserId || item.uploader === currentUserId; // Check both sender/uploader
            const justifyClass = isCurrentUser ? 'justify-end' : 'justify-start';
            const bubbleClasses = isCurrentUser ? 'bg-gradient-to-r from-indigo-700 to-purple-700 text-white' : 'bg-gray-700 text-gray-200';
            const uploaderColor = isCurrentUser ? 'text-purple-200' : 'text-indigo-300';
            const metaColor = isCurrentUser ? 'text-purple-300' : 'text-gray-400';
            // Use item ID (UUID for messages, filename for files)
            const safeItemId = `item-${item.type}-${item.id.replace(/[^a-zA-Z0-9]/g, '-')}`;

            let itemHTML = '';
            let deleteButtonHTML = '';

            // --- Delete Button Logic ---
            let canDelete = false;
            if (item.type === 'message' && isCurrentUser) {
                canDelete = true; // Sender can delete their message
            } else if (item.type === 'file') {
                if (item.file_type === 'session' && isCurrentUser) {
                    canDelete = true; // Uploader can delete own session file
                } else if (item.file_type === 'persistent' && currentUserIsAdmin) {
                    canDelete = true; // Admin can delete persistent file
                }
            }

            if (canDelete) {
                 deleteButtonHTML = `
                    <button type="button" data-itemid="${item.id}" data-itemtype="${item.type}" data-filename="${item.full_name || ''}" data-filetype="${item.file_type || ''}"
                            class="p-1 bg-red-600 hover:bg-red-700 rounded-full text-white text-xs delete-btn"
                            title="Delete ${item.type === 'message' ? 'Message' : 'File'}">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                    </button>`;
            }
            // --- End Delete Button Logic ---


            if (item.type === 'file') {
                itemHTML = `
                    <p class="text-xs font-semibold ${uploaderColor} mb-1">${item.uploader}</p>
                    <a href="${item.download_url}" class="block font-medium hover:underline break-words text-base" title="Download ${item.display_name}">${item.display_name}</a>
                    <div class="text-xs mt-1.5 ${metaColor} flex justify-between items-center">
                        <span>${item.size_human}</span>
                        <span>${item.timestamp_formatted}</span>
                    </div>`;
            } else if (item.type === 'message') {
                itemHTML = `
                    <p class="text-xs font-semibold ${uploaderColor} mb-1">${item.sender}</p>
                    <p class="message-text text-base">${item.text}</p> <div class="text-xs mt-1.5 ${metaColor} text-right">
                        <span>${item.timestamp_formatted}</span>
                    </div>`;
            }

            const itemDiv = document.createElement('div');
            itemDiv.className = `flex ${justifyClass} group chat-item`; // Use generic class
            itemDiv.id = safeItemId;

            // Added item-bubble div for relative positioning of delete button
            itemDiv.innerHTML = `
                <div class="max-w-xs md:max-w-md lg:max-w-lg p-3 rounded-xl shadow-md item-bubble ${bubbleClasses}">
                    ${itemHTML}
                    ${deleteButtonHTML}
                </div>`;

            // Append new item to the bottom (since container is flex-reversed)
            itemListArea.appendChild(itemDiv);
             // Optional: Scroll to bottom (top in reversed view) after adding
             // itemListArea.scrollTop = itemListArea.scrollHeight; // Might be jerky
        }

        function removeItemFromDOM(itemId, itemType) {
            const safeId = `item-${itemType}-${itemId.replace(/[^a-zA-Z0-9]/g, '-')}`;
            const itemElement = document.getElementById(safeId);
            if (itemElement) itemElement.remove();
            if (itemListArea.querySelectorAll('.chat-item').length === 0 && noItemsMessage) {
                 noItemsMessage.classList.remove('hidden');
            }
        }

        function clearItemList() {
             itemListArea.querySelectorAll('.chat-item').forEach(el => el.remove());
             if (noItemsMessage) noItemsMessage.classList.remove('hidden');
        }

        // --- Socket Event Listeners ---
        socket.on('connect', () => { console.log('Connected'); socket.emit('request_initial_data', { page: currentPage }); });
        socket.on('disconnect', () => { console.log('Disconnected'); });

        socket.on('initial_data', (data) => {
            console.log('Initial data:', data);
            if (activeUsersCountSpan) activeUsersCountSpan.textContent = data.active_users_count;
            if (mobileActiveUsersCountSpan) mobileActiveUsersCountSpan.textContent = data.active_users_count;
            clearItemList();
            if (data.items && data.items.length > 0) {
                 // Add items in reverse order because of flex-direction: column-reverse
                 data.items.slice().reverse().forEach(item => addItemToDOM(item));
                 if (noItemsMessage) noItemsMessage.classList.add('hidden');
            } else if (noItemsMessage) {
                 noItemsMessage.classList.remove('hidden');
            }
        });

        socket.on('update_active_users', (data) => {
            if (activeUsersCountSpan) activeUsersCountSpan.textContent = data.count;
            if (mobileActiveUsersCountSpan) mobileActiveUsersCountSpan.textContent = data.count;
        });

        socket.on('user_activity', (data) => { showNotification(data.message, data.type); });

        // Unified event for new files or messages
        socket.on('new_item', (item) => { addItemToDOM(item); });

        // Unified event for deleted files or messages
        socket.on('item_deleted', (data) => { removeItemFromDOM(data.id, data.type); });

        socket.on('session_ended', (data) => {
             console.log(`Session ended event for user ${data.user_id}. Files deleted: ${data.deleted_count}`);
             if (currentPage === 'session') {
                 // Remove only the files belonging to the user whose session ended
                 itemListArea.querySelectorAll('.chat-item').forEach(el => {
                     const deleteBtn = el.querySelector('.delete-btn');
                     if (deleteBtn && deleteBtn.dataset.filetype === 'session') {
                         // Infer uploader from filename (assuming ID starts with uploader_)
                         // This is fragile; ideally server sends list of deleted IDs
                         const filename = deleteBtn.dataset.filename;
                         if (filename && filename.startsWith(`${data.user_id}_`)) {
                             el.remove();
                         }
                     }
                 });
                 if (data.user_id === currentUserId) {
                     showNotification(`Your session files (${data.deleted_count}) deleted.`, 'logout');
                 }
                 // Check if list is now empty
                 if (itemListArea.querySelectorAll('.chat-item').length === 0 && noItemsMessage) {
                     noItemsMessage.classList.remove('hidden');
                 }
             }
         });
         socket.on('upload_error', (data) => { showNotification(`Upload Error: ${data.message}`, 'error', 6000); });
         socket.on('message_error', (data) => { showNotification(`Message Error: ${data.message}`, 'error', 6000); }); // Handle message errors


        // --- Form Handling ---
        if (messageForm) {
            messageForm.addEventListener('submit', function(event) {
                event.preventDefault();
                const messageText = messageInput.value.trim();
                if (messageText) {
                    socket.emit('send_message', { text: messageText });
                    messageInput.value = ''; // Clear input
                }
            });
        }

        // Update file name display when a file is chosen
        if (fileInput) {
            fileInput.addEventListener('change', function() {
                if (fileInput.files.length > 0) {
                    fileNameDisplay.textContent = fileInput.files[0].name;
                } else {
                    fileNameDisplay.textContent = 'No file chosen';
                }
            });
        }

        if (uploadForm) {
            uploadForm.addEventListener('submit', function(event) {
                event.preventDefault();
                const file = fileInput.files[0];
                if (!file) { showNotification('Please select a file.', 'error'); return; }
                const formData = new FormData();
                formData.append('file', file);
                fetch(`/upload/${currentPage}`, { method: 'POST', body: formData })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        fileInput.value = ''; // Clear input
                        fileNameDisplay.textContent = 'No file chosen'; // Reset display
                    } else {
                        showNotification(`Upload Failed: ${data.message}`, 'error', 6000);
                    }
                })
                .catch(error => { console.error('Upload fetch error:', error); showNotification('Network error during upload.', 'error', 6000); });
            });
        }

        // --- Delete Button Handling (Event Delegation for Files & Messages) ---
        itemListArea.addEventListener('click', function(event) {
            const target = event.target.closest('.delete-btn');
            if (target) {
                const itemId = target.dataset.itemid; // UUID for messages, filename for files
                const itemType = target.dataset.itemtype; // 'message' or 'file'
                const filename = target.dataset.filename; // Only for files
                const filetype = target.dataset.filetype; // Only for files ('persistent' or 'session')

                const confirmMsg = `Are you sure you want to delete this ${itemType}?`;

                if (itemId && itemType && confirm(confirmMsg)) {
                    if (itemType === 'message') {
                        socket.emit('delete_message', { id: itemId });
                    } else if (itemType === 'file' && filename && filetype) {
                        const deleteUrl = filetype === 'session'
                            ? `/session/delete/${filename}`
                            : `/persistent/delete/${filename}`;
                        fetch(deleteUrl, { method: 'POST' })
                        .then(response => response.json())
                        .then(data => { if (!data.success) showNotification(`Delete Failed: ${data.message}`, 'error'); })
                        .catch(error => { console.error(`Error deleting ${filetype} file:`, error); showNotification('Network error during deletion.', 'error'); });
                    }
                }
            }
        });

         // Handle End Session form submission
         if (endSessionForm) {
             endSessionForm.addEventListener('submit', function(event) {
                 event.preventDefault();
                 if (confirm('Are you sure you want to delete ALL YOUR session files?')) {
                     fetch("{{ url_for('end_session') }}", { method: 'POST' })
                     .then(response => response.json())
                     .then(data => { if (!data.success) showNotification(`End Session Failed: ${data.message}`, 'error'); })
                     .catch(error => { console.error('Error ending session:', error); showNotification('Network error ending session.', 'error'); });
                 }
             });
         }

         document.addEventListener('DOMContentLoaded', () => {
             setTimeout(() => { document.querySelectorAll('.server-flash-message').forEach(el => el.remove()); }, 5000);
         });
    </script>
</body>
</html>
"""

# --- SocketIO Event Handlers ---

@socketio.on('connect')
def handle_connect():
    user_id = session.get('user_id')
    if not user_id: return
    if user_id not in active_user_sids: active_user_sids[user_id] = set()
    active_user_sids[user_id].add(request.sid)
    print(f"User {user_id} connected SID: {request.sid}")
    emit('update_active_users', {'count': get_active_user_count()}, broadcast=True)
    emit('user_activity', {'message': f'{user_id} connected', 'type': 'login'}, broadcast=True, include_self=False)

@socketio.on('disconnect')
def handle_disconnect():
    user_id = None
    for uid, sids in list(active_user_sids.items()):
        if request.sid in sids:
            user_id = uid
            sids.remove(request.sid)
            if not sids: del active_user_sids[user_id]
            break
    if user_id:
        print(f"User {user_id} disconnected SID: {request.sid}")
        emit('update_active_users', {'count': get_active_user_count()}, broadcast=True)
        emit('user_activity', {'message': f'{user_id} disconnected', 'type': 'logout'}, broadcast=True, include_self=False)
    else: print(f"Anonymous client disconnected: {request.sid}")


@socketio.on('request_initial_data')
def handle_initial_data_request(data):
    # Combine files and chat messages, sort by timestamp
    with app.app_context(): # Needed for get_file_metadata -> url_for
        page_type = data.get('page', 'index')
        target_folder = app.config['SESSION_FOLDER'] if page_type == 'session' else app.config['UPLOAD_FOLDER']
        files = get_file_metadata(target_folder, file_type=page_type)

        # Combine files relevant to the page with chat log
        items = []
        if page_type == 'index':
            items.extend([f for f in files if f['file_type'] == 'persistent'])
        elif page_type == 'session':
            items.extend([f for f in files if f['file_type'] == 'session'])

        # Add chat messages (always shown, regardless of page)
        items.extend(chat_log)

        # Sort combined list by timestamp (newest first for reversed flex display)
        items.sort(key=lambda x: x['timestamp_iso'], reverse=True)

        emit('initial_data', { 'items': items, 'active_users_count': get_active_user_count() })

@socketio.on('send_message')
def handle_send_message(data):
    """Handles receiving and broadcasting chat messages."""
    user_id = session.get('user_id')
    if not user_id: return # Ignore if not logged in
    text = data.get('text')
    if not text:
        emit('message_error', {'message': 'Cannot send empty message.'}) # Send error back to sender
        return

    message = add_chat_message(user_id, text)
    if message:
        socketio.emit('new_item', message, broadcast=True) # Broadcast new message
        print(f"User {user_id} sent message: {text[:30]}...")
    else:
         emit('message_error', {'message': 'Failed to save message.'})

@socketio.on('delete_message')
def handle_delete_message(data):
    """Handles deleting a chat message."""
    user_id = session.get('user_id')
    message_id = data.get('id')
    if not user_id or not message_id: return

    if delete_chat_message(message_id, user_id):
        socketio.emit('item_deleted', {'id': message_id, 'type': 'message'}, broadcast=True)
        print(f"User {user_id} deleted message ID: {message_id}")
    else:
        # Optionally notify user if they tried to delete someone else's message
        print(f"User {user_id} failed to delete message ID: {message_id} (permission denied or not found)")
        emit('message_error', {'message': 'Could not delete message.'}) # Send error back to sender


# --- Flask Routes ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'): return redirect(url_for('index'))
    if request.method == 'POST':
        submitted_id = request.form.get('device_id')
        submitted_password = request.form.get('password')
        users = load_users()
        user_found = None
        for user in users:
            if user.get('device_id') == submitted_id and user.get('password') == submitted_password:
                user_found = user
                break
        if user_found:
            session['logged_in'] = True
            session['user_id'] = user_found['device_id']
            session['is_admin'] = user_found.get('is_admin', False)
            flash(f"Welcome back, {session['user_id']}!", "success")
            return redirect(url_for('index'))
        else:
            flash('Invalid Device ID or Password.', 'error')
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/')
def index():
    if not session.get('logged_in'): return redirect(url_for('login'))
    # Pass initial count, items loaded via JS
    return render_template_string(
        MAIN_UI_TEMPLATE, current_page='index',
        initial_active_users_count=get_active_user_count(), initial_items=[]
    )

@app.route('/session')
def session_files():
    if not session.get('logged_in'): return redirect(url_for('login'))
    # Pass initial count, items loaded via JS
    return render_template_string(
        MAIN_UI_TEMPLATE, current_page='session',
        initial_active_users_count=get_active_user_count(), initial_items=[]
    )

@app.route('/upload/<string:type>', methods=['POST'])
def upload_file(type):
    if not session.get('logged_in'): abort(401)
    if type not in ['index', 'session']: return jsonify(success=False, message='Invalid upload type.'), 400
    target_folder = app.config['UPLOAD_FOLDER'] if type == 'index' else app.config['SESSION_FOLDER']
    if 'file' not in request.files: return jsonify(success=False, message='No file part.'), 400
    file = request.files['file']
    if file.filename == '': return jsonify(success=False, message='No selected file.'), 400

    if file:
        original_filename = secure_filename(file.filename)
        uploader_prefix = session.get('user_id', 'unknown')
        timestamp = str(time.time())
        prefixed_filename = f"{uploader_prefix}_{timestamp}_{original_filename}"
        save_path = os.path.join(target_folder, prefixed_filename)
        try:
            file.save(save_path)
            with app.app_context(): # Context needed for url_for in get_file_metadata
                all_files_meta = get_file_metadata(target_folder, file_type=type)
            new_file_data = next((f for f in all_files_meta if f['full_name'] == prefixed_filename), None)
            if new_file_data:
                try:
                    # Use unified 'new_item' event
                    socketio.emit('new_item', new_file_data, broadcast=True)
                    print(f"File {original_filename} uploaded. Emitted 'new_item'.")
                    return jsonify(success=True, message='File uploaded successfully.', item=new_file_data)
                except Exception as emit_error:
                    print(f"Error emitting 'new_item' event for file: {emit_error}")
                    return jsonify(success=True, message='File uploaded, but real-time update failed.', item=new_file_data)
            else:
                 print(f"Error: Could not get metadata for uploaded file {prefixed_filename}")
                 return jsonify(success=False, message='File saved but metadata retrieval failed.'), 500
        except Exception as e:
            print(f"Error saving file to {target_folder}: {e}")
            return jsonify(success=False, message=f'Server error during upload: {e}'), 500
    else:
        return jsonify(success=False, message='Invalid file.'), 400

# Download route (Unchanged)
@app.route('/download/<path:filename>')
def download_file(filename):
    if not session.get('logged_in'): abort(401)
    file_type = request.args.get('type', 'persistent')
    directory = app.config['SESSION_FOLDER'] if file_type == 'session' else app.config['UPLOAD_FOLDER']
    safe_filename = secure_filename(filename)
    if safe_filename != filename: abort(400)
    try:
        file_path = os.path.join(directory, safe_filename)
        if not os.path.isfile(file_path):
             other_dir = app.config['UPLOAD_FOLDER'] if file_type == 'session' else app.config['SESSION_FOLDER']
             file_path = os.path.join(other_dir, safe_filename)
             if not os.path.isfile(file_path): raise FileNotFoundError
             directory = other_dir
        return send_from_directory(directory, safe_filename, as_attachment=True)
    except FileNotFoundError: abort(404)
    except Exception as e:
        print(f"Error downloading file {filename}: {e}")
        flash(f"An error occurred during download: {e}", "error")
        return redirect(url_for('index'))


# Delete session file route
@app.route('/session/delete/<path:filename>', methods=['POST'])
def delete_session_file(filename):
    if not session.get('logged_in'): abort(401)
    safe_filename = secure_filename(filename)
    if safe_filename != filename: return jsonify(success=False, message='Invalid filename.'), 400
    file_path = os.path.join(app.config['SESSION_FOLDER'], safe_filename)
    parts = safe_filename.split('_', 2)
    uploader = parts[0] if len(parts) >= 2 else None
    display_name = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) == 2 else safe_filename)

    if uploader != session.get('user_id'): return jsonify(success=False, message='Permission denied.'), 403
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
            # Use unified 'item_deleted' event
            socketio.emit('item_deleted', {'id': safe_filename, 'type': 'file', 'file_type': 'session'}, broadcast=True)
            print(f"Session file {safe_filename} deleted by {uploader}. Emitted 'item_deleted'.")
            return jsonify(success=True, message=f"File '{display_name}' deleted.")
        else: return jsonify(success=False, message='File not found.'), 404
    except OSError as e:
        print(f"Error deleting session file {file_path}: {e}")
        return jsonify(success=False, message=f'Server error deleting file: {e}'), 500

# Delete persistent file route
@app.route('/persistent/delete/<path:filename>', methods=['POST'])
def delete_persistent_file(filename):
    if not session.get('logged_in'): abort(401)
    if not session.get('is_admin'): return jsonify(success=False, message='Admin privileges required.'), 403
    safe_filename = secure_filename(filename)
    if safe_filename != filename: return jsonify(success=False, message='Invalid filename.'), 400
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
    parts = safe_filename.split('_', 2)
    display_name = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) == 2 else safe_filename)
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
            # Use unified 'item_deleted' event
            socketio.emit('item_deleted', {'id': safe_filename, 'type': 'file', 'file_type': 'persistent'}, broadcast=True)
            print(f"Persistent file {safe_filename} deleted by admin {session.get('user_id')}. Emitted 'item_deleted'.")
            return jsonify(success=True, message=f"Persistent file '{display_name}' deleted.")
        else: return jsonify(success=False, message='File not found.'), 404
    except OSError as e:
        print(f"Error deleting persistent file {file_path}: {e}")
        return jsonify(success=False, message=f'Server error deleting file: {e}'), 500

# End session route (deletes only current user's session files)
@app.route('/session/end', methods=['POST'])
def end_session():
    if not session.get('logged_in'): abort(401)
    folder = app.config['SESSION_FOLDER']
    user_id = session.get('user_id')
    deleted_count, error_count = 0, 0
    deleted_ids = [] # Track IDs of deleted items (filenames in this case)
    try:
        for filename in os.listdir(folder):
            if filename.startswith(f"{user_id}_"):
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        deleted_ids.append(filename) # Use filename as ID for files
                        deleted_count += 1
                except Exception as e: print(f"Could not delete user session file {filename}: {e}"); error_count += 1
        # Emit event indicating which user's session files were cleared
        socketio.emit('session_ended', {'user_id': user_id, 'deleted_count': deleted_count, 'deleted_ids': deleted_ids}, broadcast=True)
        print(f"Session ended for {user_id}. {deleted_count} files deleted. Emitted 'session_ended'.")
        message = f"Your session ended. {deleted_count} file(s) deleted."
        if error_count > 0: message += f" {error_count} failed."
        return jsonify(success=True, message=message)
    except OSError as e:
        print(f"Error clearing session folder for user {user_id} in {folder}: {e}")
        return jsonify(success=False, message=f'Error clearing session files: {e}'), 500

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('user_id', None)
    session.pop('is_admin', None)
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

# --- Run the Application ---
if __name__ == '__main__':
    ensure_dirs_exist()
    load_users()
    local_ips = get_local_ips()
    print(f" * Starting LocalShare Server (Chat, Multi-User, Real-time)...")
    print(f" * Users File: {USERS_FILE}")
    print(f" * Persistent Storage: {app.config['UPLOAD_FOLDER']}")
    print(f" * Session Storage: {app.config['SESSION_FOLDER']}")
    print(f" * Access URLs (try these on your local network):")
    for ip in local_ips:
        print(f"   - http://{ip}:5000")
    print(f" * Using eventlet for async SocketIO.")
    print(f" * WARNING: Passwords in users.json are plain text. Use hashing in production!")
    print(f" * WARNING: Debug mode should be OFF for production.")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=True)

