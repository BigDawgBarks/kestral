#!/usr/bin/env python3
"""
LLM-powered newsletter system - MVP implementation
Fetches RSS from Nitter, stores tweets with images, sends email digest.
"""

import argparse
import json
import sqlite3
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from random import uniform
from typing import List, Dict, Optional
from urllib.parse import urlparse

import feedparser
import httpx
import yaml
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

class Post:
    def __init__(self, id: str, handle: str, title: str, summary: str, 
                 published: datetime, nitter_url: str, image_urls: List[str] = None):
        self.id = id
        self.handle = handle
        self.title = title
        self.summary = summary
        self.published = published
        self.nitter_url = nitter_url
        self.x_url = nitter_url.replace(os.getenv('NITTER_BASE_URL', ''), 'https://x.com')
        self.image_urls = image_urls or []
        self.image_paths = []

def load_config() -> Dict:
    """Load configuration from accounts.yaml"""
    with open('accounts.yaml', 'r') as f:
        return yaml.safe_load(f)

def init_database():
    """Initialize SQLite database with tweets table"""
    with sqlite3.connect('newsletter.db') as conn:
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
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                included_in_newsletter BOOLEAN DEFAULT FALSE,
                llm_reason TEXT
            )
        ''')
        conn.commit()

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

def download_images(tweet_id: str, handle: str, image_urls: List[str]) -> List[str]:
    """Download images and return local file paths"""
    if not image_urls:
        return []
    
    date_folder = datetime.now().strftime('%Y-%m-%d')
    images_dir = Path(f'images/{date_folder}')
    images_dir.mkdir(parents=True, exist_ok=True)
    
    local_paths = []
    for i, url in enumerate(image_urls):
        try:
            response = httpx.get(url, timeout=10)
            response.raise_for_status()
            
            ext = get_image_extension(url, response.headers)
            filename = f"{handle}_{tweet_id}_{i+1}{ext}"
            filepath = images_dir / filename
            
            filepath.write_bytes(response.content)
            local_paths.append(str(filepath))
            
            # Be polite - small delay between downloads
            time.sleep(uniform(0.1, 0.3))
            
        except Exception as e:
            print(f"Failed to download {url}: {e}")
    
    return local_paths

def fetch_feed(handle: str, window_hours: int) -> List[Post]:
    """Fetch RSS feed for a handle and return new posts"""
    base_url = os.getenv('NITTER_BASE_URL')
    if not base_url:
        raise ValueError("NITTER_BASE_URL not set in environment")
    
    feed_url = f"{base_url}/{handle}/rss"
    
    try:
        response = httpx.get(feed_url, timeout=30)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        
        if feed.bozo:
            print(f"Warning: Feed parsing issues for {handle}")
        
        posts = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        
        for entry in feed.entries:
            # Parse published date
            published = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            
            if not published or published < cutoff_time:
                continue
            
            # Extract image URLs from media content
            image_urls = []
            if hasattr(entry, 'media_content'):
                for media in entry.media_content:
                    if media.get('type', '').startswith('image/'):
                        image_urls.append(media['url'])
            
            # Use guid or link as ID
            post_id = entry.get('guid', entry.get('link', ''))
            if not post_id:
                continue
            
            post = Post(
                id=post_id,
                handle=handle,
                title=entry.get('title', ''),
                summary=entry.get('summary', ''),
                published=published,
                nitter_url=entry.get('link', ''),
                image_urls=image_urls
            )
            
            posts.append(post)
        
        return posts
        
    except Exception as e:
        print(f"Error fetching feed for {handle}: {e}")
        return []

def is_new_post(post_id: str) -> bool:
    """Check if post is new (not in database)"""
    with sqlite3.connect('newsletter.db') as conn:
        cursor = conn.execute('SELECT id FROM tweets WHERE id = ?', (post_id,))
        return cursor.fetchone() is None

def save_posts(posts: List[Post]):
    """Save posts to database"""
    with sqlite3.connect('newsletter.db') as conn:
        for post in posts:
            conn.execute('''
                INSERT OR REPLACE INTO tweets 
                (id, handle, title, summary, published, nitter_url, x_url, 
                 image_urls, image_paths, included_in_newsletter)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                post.id, post.handle, post.title, post.summary, 
                post.published.isoformat(), post.nitter_url, post.x_url,
                json.dumps(post.image_urls), json.dumps(post.image_paths),
                True  # MVP includes all posts
            ))
        conn.commit()

def render_email(posts: List[Post]) -> tuple[str, str]:
    """Render email content as text and HTML"""
    if not posts:
        return "No new posts found.", "<p>No new posts found.</p>"
    
    # Group by handle
    by_handle = {}
    for post in posts:
        if post.handle not in by_handle:
            by_handle[post.handle] = []
        by_handle[post.handle].append(post)
    
    # Text version
    text_parts = ["Newsletter Digest\n" + "="*50 + "\n"]
    for handle, handle_posts in by_handle.items():
        text_parts.append(f"\n@{handle} ({len(handle_posts)} posts):")
        text_parts.append("-" * 30)
        for post in handle_posts:
            text_parts.append(f"• {post.title}")
            if post.summary and post.summary != post.title:
                text_parts.append(f"  {post.summary[:100]}...")
            text_parts.append(f"  Nitter: {post.nitter_url}")
            text_parts.append(f"  X: {post.x_url}")
            if post.image_paths:
                text_parts.append(f"  Images: {len(post.image_paths)} saved locally")
            text_parts.append("")
    
    text_content = "\n".join(text_parts)
    
    # HTML version
    html_parts = ["""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto;">
    <h1>Newsletter Digest</h1>
    """]
    
    for handle, handle_posts in by_handle.items():
        html_parts.append(f'<h2>@{handle} ({len(handle_posts)} posts)</h2>')
        for post in handle_posts:
            html_parts.append('<div style="margin-bottom: 20px; padding: 15px; border-left: 3px solid #1da1f2;">')
            html_parts.append(f'<h3>{post.title}</h3>')
            if post.summary and post.summary != post.title:
                html_parts.append(f'<p>{post.summary}</p>')
            html_parts.append(f'<p><a href="{post.nitter_url}">View on Nitter</a> | <a href="{post.x_url}">View on X</a></p>')
            if post.image_paths:
                html_parts.append(f'<p><small>📷 {len(post.image_paths)} images saved locally</small></p>')
            html_parts.append('</div>')
    
    html_parts.append('</body></html>')
    html_content = "".join(html_parts)
    
    return text_content, html_content

def send_email(text_content: str, html_content: str):
    """Send email via SMTP"""
    smtp_host = os.getenv('SMTP_HOST')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER')
    smtp_pass = os.getenv('SMTP_PASS')
    mail_to = os.getenv('MAIL_TO')
    mail_from = os.getenv('MAIL_FROM')
    
    if not all([smtp_host, smtp_user, smtp_pass, mail_to, mail_from]):
        print("Error: Missing email configuration in .env file")
        return
    
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"Newsletter Digest - {datetime.now().strftime('%Y-%m-%d')}"
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

def main():
    parser = argparse.ArgumentParser(description='LLM-powered newsletter system')
    parser.add_argument('--dry-run', action='store_true', 
                       help='Print to console only, do not send email')
    parser.add_argument('--send', action='store_true',
                       help='Actually send email')
    parser.add_argument('--window', type=int, 
                       help='Override window hours from config')
    
    args = parser.parse_args()
    
    if not args.dry_run and not args.send:
        print("Error: Must specify either --dry-run or --send")
        return
    
    # Load configuration
    config = load_config()
    window_hours = args.window or config.get('window_hours', 24)
    max_per_account = config.get('max_per_account', 10)
    
    # Initialize database
    init_database()
    
    # Collect new posts
    all_new_posts = []
    for account in config['accounts']:
        handle = account['handle']
        print(f"Fetching feed for @{handle}...")
        
        posts = fetch_feed(handle, window_hours)
        new_posts = [post for post in posts if is_new_post(post.id)]
        
        # Apply max_per_account limit
        if len(new_posts) > max_per_account:
            new_posts = new_posts[:max_per_account]
            print(f"Limited to {max_per_account} posts for @{handle}")
        
        # Download images for new posts
        for post in new_posts:
            if post.image_urls:
                post.image_paths = download_images(
                    post.id.split('/')[-1],  # Use last part of ID as tweet ID
                    handle, 
                    post.image_urls
                )
        
        all_new_posts.extend(new_posts)
        print(f"Found {len(new_posts)} new posts from @{handle}")
        
        # Be polite - sleep between feeds
        time.sleep(uniform(0.3, 0.8))
    
    if not all_new_posts:
        print("No new posts found.")
        return
    
    # Save to database
    save_posts(all_new_posts)
    
    # Render email
    text_content, html_content = render_email(all_new_posts)
    
    if args.dry_run:
        print("\n" + "="*60)
        print("DRY RUN - Email content:")
        print("="*60)
        print(text_content)
        print("\n" + "="*60)
        print(f"Would email {len(all_new_posts)} posts to {os.getenv('MAIL_TO', 'unknown')}")
    else:
        send_email(text_content, html_content)
        print(f"Newsletter sent with {len(all_new_posts)} posts!")

if __name__ == '__main__':
    main()