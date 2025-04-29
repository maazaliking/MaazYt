import os
import re
import logging
import tempfile
import subprocess
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, jsonify
import yt_dlp
from urllib.parse import urlparse
import time
import threading
import json

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "youtube-downloader-secret")

# Store download progress info
downloads = {}

def find_ffmpeg():
    """Find ffmpeg executable path"""
    try:
        # Try to get ffmpeg path using 'which' command
        result = subprocess.run(['which', 'ffmpeg'], 
                          capture_output=True, 
                          text=True, 
                          check=False)
        if result.returncode == 0:
            ffmpeg_path = result.stdout.strip()
            logger.info(f"Found ffmpeg at: {ffmpeg_path}")
            return ffmpeg_path
    except Exception as e:
        logger.error(f"Error finding ffmpeg: {str(e)}")
    
    # Try known locations
    known_paths = [
        '/usr/bin/ffmpeg',
        '/usr/local/bin/ffmpeg',
        '/opt/homebrew/bin/ffmpeg'
    ]
    
    for path in known_paths:
        if os.path.exists(path) and os.access(path, os.X_OK):
            logger.info(f"Found ffmpeg at known location: {path}")
            return path
    
    # If not found, return None and let yt-dlp try to find it
    logger.warning("Could not find ffmpeg path")
    return None

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

def parse_time_to_seconds(time_str):
    """Parse time string in MM:SS or HH:MM:SS format to seconds."""
    if not time_str or time_str.strip() == '':
        return 0
        
    parts = time_str.split(':')
    
    if len(parts) == 3:  # HH:MM:SS
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    elif len(parts) == 2:  # MM:SS
        minutes, seconds = parts
        return int(minutes) * 60 + int(seconds)
    else:
        try:
            return int(time_str)  # Seconds only
        except ValueError:
            return 0

def download_video(video_url, quality, start_time, end_time, download_id):
    """Download YouTube video using yt-dlp."""
    try:
        global downloads
        downloads[download_id] = {'progress': 0, 'status': 'starting', 'filename': '', 'error': None}
        
        # Create temporary directory for downloads
        temp_dir = tempfile.mkdtemp()
        
        # Force single-format downloads that don't require ffmpeg
        video_format = {
            '1080p': 'best[height<=1080][ext=mp4]',
            '720p': 'best[height<=720][ext=mp4]',
            '360p': 'best[height<=360][ext=mp4]'
        }
        
        # Create a progress hook function that includes the download_id
        def hook(d):
            progress_hook(d, download_id)
            
        ydl_opts = {
            'format': video_format.get(quality, 'best[ext=mp4]'),
            'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
            'progress_hooks': [hook],
            'no_warnings': True,
        }
        
        # Add time segment using postprocessor arguments
        start_seconds = parse_time_to_seconds(start_time)
        end_seconds = parse_time_to_seconds(end_time)
        
        if start_seconds > 0 or (end_seconds > 0 and end_seconds > start_seconds):
            # Use postprocessor options instead of download_ranges
            pp_args = []
            
            if start_seconds > 0:
                pp_args.extend(['-ss', str(start_seconds)])
            
            if end_seconds > 0 and end_seconds > start_seconds:
                pp_args.extend(['-to', str(end_seconds)])
            
            if pp_args:
                ydl_opts['postprocessor_args'] = {
                    'ffmpeg': pp_args
                }
        
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
    start_time = request.form.get('start_time', '00:00:00')
    end_time = request.form.get('end_time', '')
    
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
        args=(video_url, quality, start_time, end_time, download_id)
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

@app.route('/get_video_info', methods=['POST'])
def get_video_info():
    """Get video information including duration."""
    video_url = request.form.get('video_url', '')
    
    if not video_url or not is_valid_youtube_url(video_url):
        return jsonify({'error': 'Invalid YouTube URL'})
    
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            if 'entries' in info:  # Playlist
                info = info['entries'][0]
            
            duration = info.get('duration')
            title = info.get('title', 'Video')
            
            hours = duration // 3600
            minutes = (duration % 3600) // 60
            seconds = duration % 60
            
            formatted_duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes:02d}:{seconds:02d}"
            
            return jsonify({
                'title': title,
                'duration': duration,
                'formatted_duration': formatted_duration
            })
    
    except Exception as e:
        logger.error(f"Error getting video info: {str(e)}")
        return jsonify({'error': str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
