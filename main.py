import os
import re
import logging
import tempfile
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
import yt_dlp
from urllib.parse import urlparse
import time
import threading

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "youtube-downloader-secret")

# Store download progress info
downloads = {}

def is_valid_youtube_url(url):
    """Check if the provided URL is a valid YouTube URL."""
    youtube_regex = (
        r'(https?://)?(www\.)?'
        r'(youtube|youtu|youtube-nocookie)\.(com|be)/'
        r'(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})')
    
    youtube_regex_match = re.match(youtube_regex, url)
    if youtube_regex_match:
        return True
    return False

def download_video(video_url, quality, duration, download_id):
    """Download YouTube video using yt-dlp."""
    try:
        global downloads
        downloads[download_id] = {'progress': 0, 'status': 'starting', 'filename': '', 'error': None}
        
        # Create temporary directory for downloads
        temp_dir = tempfile.mkdtemp()
        
        # Configure yt-dlp options
        video_format = {
            '1080p': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
            '720p': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
            '360p': 'bestvideo[height<=360]+bestaudio/best[height<=360]'
        }
        
        ydl_opts = {
            'format': video_format.get(quality, 'best'),
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'progress_hooks': [lambda d: progress_hook(d, download_id)],
        }
        
        # Add duration if specified
        if duration:
            try:
                duration_seconds = float(duration) * 60  # Convert minutes to seconds
                if duration_seconds > 0:
                    ydl_opts['download_ranges'] = lambda info_dict: [
                        {"start_time": 0, "end_time": duration_seconds}
                    ]
            except ValueError:
                downloads[download_id]['status'] = 'error'
                downloads[download_id]['error'] = 'Invalid duration format'
                return
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            downloads[download_id]['status'] = 'downloading'
            info = ydl.extract_info(video_url, download=True)
            
            if 'entries' in info:  # Playlist
                info = info['entries'][0]
            
            filename = ydl.prepare_filename(info)
            downloads[download_id]['filename'] = filename
            downloads[download_id]['status'] = 'complete'
        
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        downloads[download_id]['status'] = 'error'
        downloads[download_id]['error'] = str(e)

def progress_hook(d, download_id):
    """Update download progress."""
    global downloads
    
    if d['status'] == 'downloading':
        if 'total_bytes' in d and d['total_bytes'] > 0:
            downloads[download_id]['progress'] = int(d['downloaded_bytes'] / d['total_bytes'] * 100)
        elif 'total_bytes_estimate' in d and d['total_bytes_estimate'] > 0:
            downloads[download_id]['progress'] = int(d['downloaded_bytes'] / d['total_bytes_estimate'] * 100)
    
    elif d['status'] == 'finished':
        downloads[download_id]['progress'] = 100
        downloads[download_id]['status'] = 'processing'

@app.route('/')
def home():
    """Render the home page."""
    return render_template('home.html')

@app.route('/download', methods=['POST'])
def download():
    """Handle video download request."""
    video_url = request.form.get('video_url', '')
    quality = request.form.get('quality', '720p')
    duration = request.form.get('duration', '')
    
    # Validate URL
    if not video_url or not is_valid_youtube_url(video_url):
        flash('Please enter a valid YouTube URL', 'danger')
        return redirect(url_for('home'))
    
    # Generate a unique download ID
    download_id = str(int(time.time()))
    session['download_id'] = download_id
    
    # Start download in a separate thread
    thread = threading.Thread(
        target=download_video,
        args=(video_url, quality, duration, download_id)
    )
    thread.daemon = True
    thread.start()
    
    # Redirect to result page
    return redirect(url_for('result'))

@app.route('/result')
def result():
    """Render the result page."""
    download_id = session.get('download_id')
    if not download_id or download_id not in downloads:
        flash('No download in progress', 'warning')
        return redirect(url_for('home'))
    
    return render_template('result.html', download_id=download_id)

@app.route('/check_progress/<download_id>')
def check_progress(download_id):
    """Check download progress."""
    global downloads
    
    if download_id in downloads:
        return {
            'progress': downloads[download_id]['progress'],
            'status': downloads[download_id]['status'],
            'error': downloads[download_id].get('error')
        }
    
    return {'progress': 0, 'status': 'not_found', 'error': 'Download not found'}

@app.route('/get_file/<download_id>')
def get_file(download_id):
    """Serve the downloaded file."""
    global downloads
    
    if download_id in downloads and downloads[download_id]['status'] == 'complete':
        filename = downloads[download_id]['filename']
        if os.path.exists(filename):
            # Get just the file name without the path
            base_filename = os.path.basename(filename)
            return send_file(
                filename,
                as_attachment=True,
                download_name=base_filename
            )
    
    flash('File not found or download incomplete', 'danger')
    return redirect(url_for('result'))

@app.route('/new_download')
def new_download():
    """Start a new download."""
    # Clear current download session
    if 'download_id' in session:
        session.pop('download_id')
    
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
