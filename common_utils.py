"""
Shared utilities for the newsletter system.
High bar for inclusion - only genuinely platform-agnostic code.
"""

import sqlite3
import smtplib
import base64
import os
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional, Dict
from urllib.parse import urlparse

import yaml

# Configuration will be loaded via load_full_config() function


def load_full_config(config_path: str, secrets_path: str) -> Dict:
    """Load and merge configuration from config and secrets files"""
    # Load non-sensitive config
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    # Load sensitive config
    with open(secrets_path, 'r') as f:
        secrets = yaml.safe_load(f)
    
    # Merge configs - secrets override config for overlapping keys
    def merge_dicts(base: Dict, overlay: Dict) -> Dict:
        result = base.copy()
        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = merge_dicts(result[key], value)
            else:
                result[key] = value
        return result
    
    return merge_dicts(config, secrets)


def set_up_logging(platform: str) -> logging.Logger:
    """Set up logging to both console and file for the specified platform"""
    # Create logs directory if it doesn't exist
    logs_dir = Path('logs')
    logs_dir.mkdir(exist_ok=True)

    # Generate timestamp for log filename
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    log_filename = logs_dir / f"{timestamp}_{platform}.log"

    # Create logger
    logger = logging.getLogger('newsletter')
    logger.setLevel(logging.INFO)

    # Clear any existing handlers to avoid duplicates
    logger.handlers.clear()

    # Create formatters
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')

    # Console handler (stdout)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Log startup info
    logger.info(f"Newsletter run started: {platform} platform")

    return logger


def log_or_print(message: str, level: str = 'info', logger=None):
    """Log message to logger if available, otherwise print to console"""
    if logger:
        getattr(logger, level)(message)
    else:
        print(message)


def init_database():
    """Initialize SQLite database with all tables"""
    with sqlite3.connect('newsletter.db') as conn:
        # Twitter/tweets table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tweets (
                id TEXT PRIMARY KEY,
                handle TEXT,
                title TEXT,
                summary TEXT,
                published TIMESTAMP,
                nitter_url TEXT,
                x_url TEXT,
                image_urls TEXT,
                image_paths TEXT,
                profile_pic_url TEXT,
                profile_pic_path TEXT,
                profile_pic_server_url TEXT,
                server_image_urls TEXT,
                video_attachments TEXT,
                raw_description TEXT,
                is_retweet BOOLEAN,
                is_reply BOOLEAN,
                quote_tweet_url TEXT,
                quote_author TEXT,
                quote_text TEXT,
                quote_image_urls TEXT,
                retweet_author TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                included_in_newsletter BOOLEAN DEFAULT FALSE,
                llm_reason TEXT
            )
        ''')

        # Backfill new columns for existing databases
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(tweets);").fetchall()}
        if 'video_attachments' not in existing_columns:
            conn.execute('ALTER TABLE tweets ADD COLUMN video_attachments TEXT')
        if 'first_seen' not in existing_columns:
            conn.execute('ALTER TABLE tweets ADD COLUMN first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
        if 'included_in_newsletter' not in existing_columns:
            conn.execute('ALTER TABLE tweets ADD COLUMN included_in_newsletter BOOLEAN DEFAULT FALSE')
        if 'llm_reason' not in existing_columns:
            conn.execute('ALTER TABLE tweets ADD COLUMN llm_reason TEXT')
        
        # Discord tables (for future use)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS discord_messages (
                id TEXT PRIMARY KEY,
                channel_id TEXT,
                channel_name TEXT,
                author_name TEXT,
                author_id TEXT,
                content TEXT,
                timestamp TIMESTAMP,
                message_type TEXT,
                thread_id TEXT,
                attachments TEXT,
                reactions TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                included_in_newsletter BOOLEAN DEFAULT FALSE,
                llm_reason TEXT
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS discord_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                channel_name TEXT,
                summary_type TEXT,
                summary_text TEXT,
                message_ids TEXT,
                block_number INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()


def send_email(text_content: str, html_content: str, subject: str = None, recipient_email: str = None, config: Dict = None, logger=None):
    """Send email via SMTP - generic email sender"""
    if logger is None:
        logger = logging.getLogger('newsletter')

    if not config:
        logger.error("Configuration required for sending email")
        return
        
    smtp_host = config.get('email', {}).get('smtp_host')
    smtp_port = config.get('email', {}).get('smtp_port', 587)
    smtp_user = config.get('email', {}).get('smtp_user')
    smtp_pass = config.get('email', {}).get('smtp_pass')
    mail_to = recipient_email
    mail_from = config.get('email', {}).get('mail_from')
    
    if not all([smtp_host, smtp_user, smtp_pass, mail_to, mail_from]):
        logger.error("Missing email configuration in config files")
        return
    
    # Use provided subject or default
    if not subject:
        subject = f"Newsletter Digest - {datetime.now().strftime('%Y-%m-%d')}"
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = mail_from
    msg['To'] = mail_to
    
    text_part = MIMEText(text_content, 'plain')
    html_part = MIMEText(html_content, 'html')
    
    msg.attach(text_part)
    msg.attach(html_part)
    
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logger.info(f"Email sent successfully to {mail_to}")
    except Exception as e:
        logger.error(f"Error sending email: {e}")


def image_to_base64(image_path: str) -> Optional[str]:
    """Convert image file to base64 data URL for embedding in email"""
    if not image_path or not Path(image_path).exists():
        return None
    
    try:
        with open(image_path, 'rb') as f:
            image_data = f.read()
        
        # Determine MIME type from extension
        ext = Path(image_path).suffix.lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg', 
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
        mime_type = mime_types.get(ext, 'image/jpeg')
        
        # Convert to base64
        base64_data = base64.b64encode(image_data).decode('utf-8')
        return f"data:{mime_type};base64,{base64_data}"
    
    except Exception as e:
        logger = logging.getLogger('newsletter')
        logger.warning(f"Failed to convert {image_path} to base64: {e}")
        return None


def get_image_extension(url: str, headers: Dict[str, str]) -> str:
    """Extract file extension from URL or content-type"""
    # Try URL first
    parsed = urlparse(url)
    path = parsed.path
    if '.' in path:
        ext = Path(path).suffix
        if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            return ext
    
    # Fallback to content-type
    content_type = headers.get('content-type', '').lower()
    if 'jpeg' in content_type:
        return '.jpg'
    elif 'png' in content_type:
        return '.png'
    elif 'gif' in content_type:
        return '.gif'
    elif 'webp' in content_type:
        return '.webp'
    
    return '.jpg'  # Default fallback


def upload_to_image_server(image_path: str, config: Dict = None, logger=None) -> Optional[str]:
    """Copy image to self-hosted server and return the URL"""
    if logger is None:
        logger = logging.getLogger('newsletter')

    if not image_path or not Path(image_path).exists():
        return None

    if not config:
        logger.warning("No config provided, skipping image server upload")
        return None
        
    image_server_path = config.get('image_server', {}).get('path')
    image_server_url = config.get('image_server', {}).get('url')
    
    if not image_server_path or not image_server_url:
        logger.warning("IMAGE_SERVER_PATH or IMAGE_SERVER_URL not set, skipping upload")
        return None
    
    try:
        import shutil
        source_path = Path(image_path)
        
        # Create date-based subdirectory
        date_folder = datetime.now().strftime('%Y-%m-%d')
        dest_dir = Path(image_server_path) / date_folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate filename (sanitize for web URLs)
        filename = source_path.name.replace('#', '_').replace('?', '_').replace('&', '_')
        dest_path = dest_dir / filename
        
        # Copy file to image server directory
        shutil.copy2(source_path, dest_path)
        
        # Make sure nginx can read it
        os.chmod(dest_path, 0o644)
        
        # Return public URL
        public_url = f"{image_server_url}/{date_folder}/{filename}"
        logger.info(f"Uploaded {filename} to image server: {public_url}")
        return public_url

    except Exception as e:
        logger.warning(f"Failed to upload {image_path} to image server: {e}")
        return None


def load_accounts_config() -> Dict:
    """Load accounts configuration from accounts.yaml"""
    with open('accounts.yaml', 'r') as f:
        return yaml.safe_load(f)
