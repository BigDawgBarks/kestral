"""
Shared utilities for the newsletter system.
High bar for inclusion - only genuinely platform-agnostic code.
"""

import sqlite3
import smtplib
import base64
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional, Dict
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Self-hosted image server configuration
IMAGE_SERVER_PATH = os.getenv('IMAGE_SERVER_PATH')
IMAGE_SERVER_URL = os.getenv('IMAGE_SERVER_URL')


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


def send_email(text_content: str, html_content: str, subject: str = None):
    """Send email via SMTP - generic email sender"""
    smtp_host = os.getenv('SMTP_HOST')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER')
    smtp_pass = os.getenv('SMTP_PASS')
    mail_to = os.getenv('MAIL_TO')
    mail_from = os.getenv('MAIL_FROM')
    
    if not all([smtp_host, smtp_user, smtp_pass, mail_to, mail_from]):
        print("Error: Missing email configuration in .env file")
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
        print(f"Email sent successfully to {mail_to}")
    except Exception as e:
        print(f"Error sending email: {e}")


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
        print(f"Failed to convert {image_path} to base64: {e}")
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


def upload_to_image_server(image_path: str) -> Optional[str]:
    """Copy image to self-hosted server and return the URL"""
    if not image_path or not Path(image_path).exists():
        return None
    
    if not IMAGE_SERVER_PATH or not IMAGE_SERVER_URL:
        print("Warning: IMAGE_SERVER_PATH or IMAGE_SERVER_URL not set, skipping upload")
        return None
    
    try:
        import shutil
        source_path = Path(image_path)
        
        # Create date-based subdirectory
        date_folder = datetime.now().strftime('%Y-%m-%d')
        dest_dir = Path(IMAGE_SERVER_PATH) / date_folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate filename (sanitize for web URLs)
        filename = source_path.name.replace('#', '_').replace('?', '_').replace('&', '_')
        dest_path = dest_dir / filename
        
        # Copy file to image server directory
        shutil.copy2(source_path, dest_path)
        
        # Make sure nginx can read it
        os.chmod(dest_path, 0o644)
        
        # Return public URL
        public_url = f"{IMAGE_SERVER_URL}/{date_folder}/{filename}"
        print(f"Uploaded {filename} to image server: {public_url}")
        return public_url
        
    except Exception as e:
        print(f"Failed to upload {image_path} to image server: {e}")
        return None


def load_config() -> Dict:
    """Load configuration from accounts.yaml"""
    with open('accounts.yaml', 'r') as f:
        return yaml.safe_load(f)