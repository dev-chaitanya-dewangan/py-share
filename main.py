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
import json # For loading users
from werkzeug.utils import secure_filename
# from werkzeug.security import generate_password_hash, check_password_hash # Keep commented unless implementing hashing
import time


# --- Configuration ---
UPLOAD_FOLDER = './nas_uploads_persistent'
SESSION_FOLDER = './nas_uploads_session'
USERS_FILE = './users.json'
SECRET_KEY = '123@123@telegram' # Use a strong, unique key in practice

# --- Flask Application Setup ---
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['UPLOAD_FOLDER'] = os.path.abspath(UPLOAD_FOLDER)
app.config['SESSION_FOLDER'] = os.path.abspath(SESSION_FOLDER)

# --- SocketIO Setup ---
# async_mode='eventlet' is implicitly handled now by monkey patching first
socketio = SocketIO(app, cors_allowed_origins="*")

# --- Real-time User Tracking ---
active_user_sids = {} # user_id -> set(sids)

# --- User Management ---
def load_users():
    """Loads user data from JSON file."""
    if not os.path.exists(USERS_FILE):
        default_users = [{
            "device_id": "admin",
            "password": "password123", # Plain text for simplicity here
            "is_admin": True
        }]
        try:
            with open(USERS_FILE, 'w') as f:
                json.dump(default_users, f, indent=2)
            print(f"Created default users file: {USERS_FILE} with user 'admin'")
            return default_users
        except IOError as e:
            print(f"FATAL: Could not create users file {USERS_FILE}: {e}")
            return []
    try:
        with open(USERS_FILE, 'r') as f:
            users = json.load(f)
            if not isinstance(users, list):
                print(f"Error: {USERS_FILE} does not contain a valid JSON list.")
                return []
            return users
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error loading users from {USERS_FILE}: {e}")
        return []

# --- Utility Functions ---

def get_human_readable_size(size_bytes):
    if size_bytes is None or size_bytes < 0: return "N/A"
    if size_bytes == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(max(1, size_bytes), 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def get_file_metadata(directory, file_type='persistent'):
    # This function now needs application context to use url_for
    # We will call it within routes or wrap calls with app.app_context() if needed outside routes
    files_metadata = []
    if not os.path.isdir(directory):
        print(f"Warning: Directory not found: {directory}")
        return []
    try:
        # Use app context if url_for is needed outside a request
        with app.app_context():
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

                        # Generate download URL safely within context
                        download_url = url_for('download_file', filename=filename, type=file_type, _external=False) # Use _external=False for relative path

                        files_metadata.append({
                            'full_name': filename, 'display_name': original_name, 'uploader': uploader,
                            'size_bytes': stats.st_size, 'size_human': get_human_readable_size(stats.st_size),
                            'upload_time_iso': upload_dt.isoformat(),
                            'upload_time_formatted': upload_dt.strftime('%b %d, %H:%M'),
                            'file_type': file_type,
                            'download_url': download_url # Use generated URL
                        })
                    except Exception as e: print(f"Warning: Error processing file {filename}: {e}")
            files_metadata.sort(key=lambda x: x['upload_time_iso'], reverse=True)
            return files_metadata
    except OSError as e:
        print(f"Error accessing directory {directory}: {e}")
        return []
    except RuntimeError as e:
        # Catch context errors if somehow still occurring
        print(f"Context error in get_file_metadata for {directory}: {e}")
        print("Ensure this function is called within an application or request context.")
        return []


def ensure_dirs_exist():
    for folder in [app.config['UPLOAD_FOLDER'], app.config['SESSION_FOLDER']]:
        if not os.path.exists(folder):
            try: os.makedirs(folder); print(f"Created directory: {folder}")
            except OSError as e: print(f"FATAL: Could not create directory {folder}: {e}"); import sys; sys.exit(1)

def get_active_user_count(): return len(active_user_sids)

def is_user_admin(user_id):
    users = load_users()
    for user in users:
        if user.get('device_id') == user_id:
            return user.get('is_admin', False)
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

# Main UI Template (Unchanged from v6)
MAIN_UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" class="h-full">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Local File Share</title>
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
        .chat-area { flex-grow: 1; overflow-y: auto; position: relative; }
        .input-area { flex-shrink: 0; background-color: #1f2937; border-top: 1px solid #374151; }
        .notification { position: absolute; top: 10px; left: 50%; transform: translateX(-50%); padding: 8px 16px; border-radius: 8px; font-size: 0.9em; z-index: 50; transition: opacity 0.5s ease-out; opacity: 0; pointer-events: none; }
        .notification.show { opacity: 1; }
        .notification.login { background-color: #10B981; color: white; }
        .notification.logout { background-color: #F59E0B; color: white; }
        .notification.error { background-color: #EF4444; color: white; } /* Added error style */

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
                    {% if current_page == 'session' %}Session Files{% else %}Persistent Files{% endif %}
                 </h1>
                 <div class="user-info text-sm text-gray-400">
                    <span class="desktop-user-info">User: <strong class="font-medium text-gray-200">{{ session.user_id }}</strong> {% if session.is_admin %}<span class="text-xs text-yellow-400">(Admin)</span>{% endif %} | Active: <strong id="active-users-count" class="text-green-400">{{ initial_active_users_count }}</strong></span>
                    <span class="mobile-active-users"><strong id="mobile-active-users-count" class="text-green-400">{{ initial_active_users_count }}</strong> online</span>
                 </div>
            </header>

            <main class="chat-area flex-grow p-4 md:p-6 lg:p-8 space-y-5" id="file-list-area">
                 <div id="notification-area" class="notification"></div>
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
                <div id="no-files-message" class="flex justify-center {% if initial_files %}hidden{% endif %}">
                     <div class="bg-gray-800 border border-gray-700 p-6 rounded-lg shadow text-center max-w-md">
                        <p class="text-gray-400">No files here yet.</p>
                        <p class="text-gray-500 text-sm mt-2">Use the form below to upload a file.</p>
                    </div>
                </div>
                 <p class="text-center text-gray-600 text-xs pt-4">
                    Storage: {% if current_page == 'session' %}Session (Temporary){% else %}Persistent{% endif %}
                 </p>
            </main>

            <footer class="input-area p-4">
                <form id="upload-form" method="post" enctype="multipart/form-data" class="flex items-center space-x-3 max-w-4xl mx-auto">
                    <button type="button" class="p-2 text-gray-400 hover:text-indigo-400 rounded-full hover:bg-gray-700">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" /></svg>
                    </button>
                    <div class="flex-grow">
                         <label for="file" class="sr-only">Choose file</label>
                         <input type="file" name="file" id="file" required
                                class="block w-full text-sm text-gray-400 border border-gray-600 rounded-lg cursor-pointer bg-gray-700 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-transparent placeholder-gray-500
                                file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0
                                file:text-sm file:font-semibold file:bg-indigo-600 file:text-indigo-100
                                hover:file:bg-indigo-700">
                    </div>
                    <button type="submit" class="bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700 text-white font-bold p-2.5 rounded-full focus:outline-none focus:shadow-outline transition duration-150 ease-in-out">
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
        // --- Client-Side JS (Unchanged from v6) ---
        const socket = io();
        const currentUserId = "{{ session.user_id }}";
        const currentUserIsAdmin = {{ session.is_admin | tojson }}; // Pass admin status to JS
        const currentPage = "{{ current_page }}";
        const fileListArea = document.getElementById('file-list-area');
        const noFilesMessage = document.getElementById('no-files-message');
        const activeUsersCountSpan = document.getElementById('active-users-count');
        const mobileActiveUsersCountSpan = document.getElementById('mobile-active-users-count');
        const notificationArea = document.getElementById('notification-area');
        const uploadForm = document.getElementById('upload-form');
        const endSessionForm = document.getElementById('end-session-form');

        function showNotification(message, type = 'login', duration = 4000) {
            if (!notificationArea) return;
            notificationArea.textContent = message;
            const typeClass = type === 'error' ? 'error' : (type === 'logout' ? 'logout' : 'login');
            notificationArea.className = `notification ${typeClass} show`;
            setTimeout(() => { notificationArea.classList.remove('show'); }, duration);
        }

        function addFileToDOM(file) {
            if (file.file_type !== currentPage) return;
            if (noFilesMessage) noFilesMessage.classList.add('hidden');

            const isCurrentUser = file.uploader === currentUserId;
            const justifyClass = isCurrentUser ? 'justify-end' : 'justify-start';
            const bubbleClasses = isCurrentUser ? 'bg-gradient-to-r from-indigo-700 to-purple-700 text-white' : 'bg-gray-700 text-gray-200';
            const uploaderColor = isCurrentUser ? 'text-purple-200' : 'text-indigo-300';
            const metaColor = isCurrentUser ? 'text-purple-300' : 'text-gray-400';
            const safeFileId = `file-${file.file_type}-${file.full_name.replace(/[^a-zA-Z0-9]/g, '-')}`;

            let deleteButtonHTML = '';
            if (file.file_type === 'session' && isCurrentUser) {
                deleteButtonHTML = `
                    <button type="button" data-filename="${file.full_name}" data-filetype="${file.file_type}"
                            class="absolute -top-2 -right-2 opacity-0 group-hover:opacity-100 transition-opacity p-1 bg-red-600 hover:bg-red-700 rounded-full text-white text-xs delete-btn"
                            title="Delete Session File">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                    </button>`;
            }
            else if (file.file_type === 'persistent' && currentUserIsAdmin) {
                 deleteButtonHTML = `
                    <button type="button" data-filename="${file.full_name}" data-filetype="${file.file_type}"
                            class="absolute -top-2 -right-2 opacity-0 group-hover:opacity-100 transition-opacity p-1 bg-red-600 hover:bg-red-700 rounded-full text-white text-xs delete-btn"
                            title="Delete Persistent File (Admin)">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                    </button>`;
            }

            const fileDiv = document.createElement('div');
            fileDiv.className = `flex ${justifyClass} group file-message`;
            fileDiv.id = safeFileId;

            fileDiv.innerHTML = `
                <div class="max-w-xs md:max-w-md lg:max-w-lg p-3 rounded-xl shadow-md relative ${bubbleClasses}">
                    <p class="text-xs font-semibold ${uploaderColor} mb-1">${file.uploader}</p>
                    <a href="${file.download_url}" class="block font-medium hover:underline break-words text-base" title="Download ${file.display_name}">${file.display_name}</a>
                    <div class="text-xs mt-1.5 ${metaColor} flex justify-between items-center">
                        <span>${file.size_human}</span>
                        <span>${file.upload_time_formatted}</span>
                    </div>
                    ${deleteButtonHTML}
                </div>`;

            const firstFileMessage = fileListArea.querySelector('.file-message');
            if (firstFileMessage) fileListArea.insertBefore(fileDiv, firstFileMessage);
            else {
                const insertionPoint = noFilesMessage || fileListArea.querySelector('p.text-center');
                if (insertionPoint) fileListArea.insertBefore(fileDiv, insertionPoint);
                else fileListArea.appendChild(fileDiv);
            }
        }

        function removeFileFromDOM(filename, fileType) {
            if (fileType !== currentPage) return;
            const safeId = `file-${fileType}-${filename.replace(/[^a-zA-Z0-9]/g, '-')}`;
            const fileElement = document.getElementById(safeId);
            if (fileElement) fileElement.remove();
            if (fileListArea.querySelectorAll('.file-message').length === 0 && noFilesMessage) {
                 noFilesMessage.classList.remove('hidden');
            }
        }

        function clearFileList() {
             fileListArea.querySelectorAll('.file-message').forEach(el => el.remove());
             if (noFilesMessage) noFilesMessage.classList.remove('hidden');
        }

        // --- Socket Event Listeners ---
        socket.on('connect', () => { console.log('Connected'); socket.emit('request_initial_data', { page: currentPage }); });
        socket.on('disconnect', () => { console.log('Disconnected'); });

        socket.on('initial_data', (data) => {
            console.log('Initial data:', data);
            if (activeUsersCountSpan) activeUsersCountSpan.textContent = data.active_users_count;
            if (mobileActiveUsersCountSpan) mobileActiveUsersCountSpan.textContent = data.active_users_count;
            clearFileList();
            if (data.files && data.files.length > 0) {
                 data.files.forEach(file => addFileToDOM(file));
                 if (noFilesMessage) noFilesMessage.classList.add('hidden');
            } else if (noFilesMessage) {
                 noFilesMessage.classList.remove('hidden');
            }
        });

        socket.on('update_active_users', (data) => {
            if (activeUsersCountSpan) activeUsersCountSpan.textContent = data.count;
            if (mobileActiveUsersCountSpan) mobileActiveUsersCountSpan.textContent = data.count;
        });

        socket.on('user_activity', (data) => { showNotification(data.message, data.type); });
        socket.on('new_file', (file) => { addFileToDOM(file); });
        socket.on('file_deleted', (data) => { removeFileFromDOM(data.filename, data.type); });
        socket.on('session_ended', (data) => {
             console.log(`Session ended event for user ${data.user_id}. Files deleted: ${data.deleted_count}`);
             if (currentPage === 'session' && data.user_id === currentUserId) {
                 clearFileList();
                 showNotification(`Your session files (${data.deleted_count}) deleted.`, 'logout');
             }
         });
         socket.on('upload_error', (data) => { showNotification(`Upload Error: ${data.message}`, 'error', 6000); });

        // --- Form Handling ---
        if (uploadForm) {
            uploadForm.addEventListener('submit', function(event) {
                event.preventDefault();
                const fileInput = document.getElementById('file');
                const file = fileInput.files[0];
                if (!file) { showNotification('Please select a file.', 'error'); return; }
                const formData = new FormData();
                formData.append('file', file);
                fetch(`/upload/${currentPage}`, { method: 'POST', body: formData })
                .then(response => response.json())
                .then(data => {
                    if (data.success) fileInput.value = '';
                    else showNotification(`Upload Failed: ${data.message}`, 'error', 6000);
                })
                .catch(error => { console.error('Upload fetch error:', error); showNotification('Network error during upload.', 'error', 6000); });
            });
        }

        // --- Delete Button Handling (Event Delegation) ---
        fileListArea.addEventListener('click', function(event) {
            const target = event.target.closest('.delete-btn');
            if (target) {
                const filename = target.dataset.filename;
                const filetype = target.dataset.filetype;
                const confirmMsg = `Are you sure you want to delete this ${filetype} file?`;

                if (filename && filetype && confirm(confirmMsg)) {
                    const deleteUrl = filetype === 'session'
                        ? `/session/delete/${filename}`
                        : `/persistent/delete/${filename}`;

                    fetch(deleteUrl, { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                         if (!data.success) showNotification(`Delete Failed: ${data.message}`, 'error');
                    })
                    .catch(error => {
                         console.error(`Error deleting ${filetype} file:`, error);
                         showNotification('Network error during deletion.', 'error');
                    });
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
                     .then(data => {
                         if (!data.success) showNotification(`End Session Failed: ${data.message}`, 'error');
                     })
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
    # Needs app context because get_file_metadata uses url_for
    with app.app_context():
        page_type = data.get('page', 'index')
        target_folder = app.config['SESSION_FOLDER'] if page_type == 'session' else app.config['UPLOAD_FOLDER']
        files = get_file_metadata(target_folder, file_type=page_type)
        emit('initial_data', { 'files': files, 'active_users_count': get_active_user_count() })

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
    return render_template_string(
        MAIN_UI_TEMPLATE, current_page='index',
        initial_active_users_count=get_active_user_count(), initial_files=[]
    )

@app.route('/session')
def session_files():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template_string(
        MAIN_UI_TEMPLATE, current_page='session',
        initial_active_users_count=get_active_user_count(), initial_files=[]
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
            # --- Get Metadata and Emit (Needs App Context for url_for) ---
            with app.app_context():
                all_files_meta = get_file_metadata(target_folder, file_type=type)
            new_file_data = next((f for f in all_files_meta if f['full_name'] == prefixed_filename), None)

            if new_file_data:
                try:
                    socketio.emit('new_file', new_file_data, broadcast=True)
                    print(f"File {original_filename} uploaded. Emitted 'new_file'.")
                    return jsonify(success=True, message='File uploaded successfully.', file=new_file_data)
                except Exception as emit_error:
                    print(f"Error emitting 'new_file' event: {emit_error}")
                    return jsonify(success=True, message='File uploaded, but real-time update failed.', file=new_file_data)
            else:
                 print(f"Error: Could not get metadata for uploaded file {prefixed_filename}")
                 return jsonify(success=False, message='File saved but metadata retrieval failed.'), 500
        except Exception as e:
            print(f"Error saving file to {target_folder}: {e}")
            return jsonify(success=False, message=f'Server error during upload: {e}'), 500
    else:
        return jsonify(success=False, message='Invalid file.'), 400

@app.route('/download/<path:filename>')
def download_file(filename):
    # This route inherently has request context, so url_for works fine if called from here
    # However, get_file_metadata might be called elsewhere, hence the context wrapper there
    if not session.get('logged_in'): abort(401)
    file_type = request.args.get('type', 'persistent')
    directory = app.config['SESSION_FOLDER'] if file_type == 'session' else app.config['UPLOAD_FOLDER']
    safe_filename = secure_filename(filename)
    if safe_filename != filename: abort(400)
    try:
        # Check existence before sending
        file_path = os.path.join(directory, safe_filename)
        if not os.path.isfile(file_path):
             other_dir = app.config['UPLOAD_FOLDER'] if file_type == 'session' else app.config['SESSION_FOLDER']
             file_path = os.path.join(other_dir, safe_filename) # Check other dir
             if not os.path.isfile(file_path):
                 raise FileNotFoundError # Not found in either
             directory = other_dir # Update directory if found in fallback

        return send_from_directory(directory, safe_filename, as_attachment=True)
    except FileNotFoundError: abort(404)
    except Exception as e:
        print(f"Error downloading file {filename}: {e}")
        flash(f"An error occurred during download: {e}", "error")
        # Redirect to a sensible default page
        return redirect(url_for('index'))


@app.route('/session/delete/<path:filename>', methods=['POST'])
def delete_session_file(filename):
    if not session.get('logged_in'): abort(401)
    safe_filename = secure_filename(filename)
    if safe_filename != filename: return jsonify(success=False, message='Invalid filename.'), 400
    file_path = os.path.join(app.config['SESSION_FOLDER'], safe_filename)
    parts = safe_filename.split('_', 2)
    uploader = parts[0] if len(parts) >= 2 else None
    display_name = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) == 2 else safe_filename)

    if uploader != session.get('user_id'):
        return jsonify(success=False, message='Permission denied.'), 403
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
            socketio.emit('file_deleted', {'filename': safe_filename, 'type': 'session'}, broadcast=True)
            print(f"Session file {safe_filename} deleted by {uploader}. Emitted 'file_deleted'.")
            return jsonify(success=True, message=f"File '{display_name}' deleted.")
        else: return jsonify(success=False, message='File not found.'), 404
    except OSError as e:
        print(f"Error deleting session file {file_path}: {e}")
        return jsonify(success=False, message=f'Server error deleting file: {e}'), 500

@app.route('/persistent/delete/<path:filename>', methods=['POST'])
def delete_persistent_file(filename):
    if not session.get('logged_in'): abort(401)
    if not session.get('is_admin'):
        return jsonify(success=False, message='Admin privileges required.'), 403
    safe_filename = secure_filename(filename)
    if safe_filename != filename: return jsonify(success=False, message='Invalid filename.'), 400
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
    parts = safe_filename.split('_', 2)
    display_name = parts[2] if len(parts) >= 3 else (parts[1] if len(parts) == 2 else safe_filename)
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
            socketio.emit('file_deleted', {'filename': safe_filename, 'type': 'persistent'}, broadcast=True)
            print(f"Persistent file {safe_filename} deleted by admin {session.get('user_id')}. Emitted 'file_deleted'.")
            return jsonify(success=True, message=f"Persistent file '{display_name}' deleted.")
        else: return jsonify(success=False, message='File not found.'), 404
    except OSError as e:
        print(f"Error deleting persistent file {file_path}: {e}")
        return jsonify(success=False, message=f'Server error deleting file: {e}'), 500

@app.route('/session/end', methods=['POST'])
def end_session():
    if not session.get('logged_in'): abort(401)
    folder = app.config['SESSION_FOLDER']
    user_id = session.get('user_id')
    deleted_count, error_count = 0, 0
    try:
        for filename in os.listdir(folder):
            if filename.startswith(f"{user_id}_"):
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path): os.remove(file_path); deleted_count += 1
                except Exception as e: print(f"Could not delete user session file {filename}: {e}"); error_count += 1
        socketio.emit('session_ended', {'user_id': user_id, 'deleted_count': deleted_count}, broadcast=True)
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
    print(f" * Starting Local File Share Server (Multi-User, Real-time)...")
    print(f" * Users File: {USERS_FILE}")
    print(f" * Persistent Uploads: {app.config['UPLOAD_FOLDER']}")
    print(f" * Session Uploads: {app.config['SESSION_FOLDER']}")
    print(f" * Access URL: http://<your-computer-ip>:5000")
    print(f" * Using eventlet for async SocketIO.")
    print(f" * WARNING: Passwords in users.json are plain text (for demo). Use hashing in production!")
    print(f" * WARNING: Debug mode should be OFF for production.")
    # Use socketio.run, debug=True enables Flask debugger AND SocketIO logging
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=True)

