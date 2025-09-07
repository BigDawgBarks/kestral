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
import base64
import re
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

# Self-hosted image server configuration
IMAGE_SERVER_PATH = os.getenv('IMAGE_SERVER_PATH')
IMAGE_SERVER_URL = os.getenv('IMAGE_SERVER_URL')

class AccountList:
    def __init__(self, name: str, accounts: List[str], 
                 max_posts: int = None, custom_settings: Dict = None):
        self.name = name
        self.accounts = accounts
        self.max_posts = max_posts  # Override global if set
        self.custom_settings = custom_settings or {}
    
    def get_email_subject(self) -> str:
        date_str = datetime.now().strftime('%Y-%m-%d')
        if len(self.accounts) == 1:
            return f"@{self.accounts[0]} Newsletter - {date_str}"
        else:
            return f"{self.name} Newsletter - {date_str}"

class Post:
    def __init__(self, id: str, handle: str, title: str, summary: str, 
                 published: datetime, nitter_url: str, image_urls: List[str] = None,
                 profile_pic_url: str = None, raw_description: str = None):
        self.id = id
        self.handle = handle
        self.title = title
        self.summary = summary
        self.published = published
        self.nitter_url = nitter_url
        self.x_url = nitter_url.replace(os.getenv('NITTER_BASE_URL', ''), 'https://x.com')
        self.image_urls = image_urls or []
        self.image_paths = []
        self.server_image_urls = []  # Image server URLs for embedded images
        self.profile_pic_url = profile_pic_url
        self.profile_pic_path = None
        self.profile_pic_server_url = None
        self.raw_description = raw_description or summary
        
        # Parse tweet type and content
        self.is_retweet = self.title.startswith('RT by @')
        self.is_reply = self.title.startswith('R to @')
        self.quote_tweet_url = self._extract_quote_tweet_url()
        self.retweet_author = self._extract_retweet_author()
        
        # Quote tweet content (filled in later if quote tweet exists)
        self.quote_author = None
        self.quote_text = None
        self.quote_image_urls = []
    
    def _extract_quote_tweet_url(self) -> Optional[str]:
        """Extract quote tweet URL from description"""
        if not self.raw_description:
            return None
        # Look for links to other tweets in the description
        match = re.search(r'<a href="([^"]*status/\d+[^"]*)">([^<]+)</a>', self.raw_description)
        if match:
            return match.group(1)
        return None
    
    def _extract_retweet_author(self) -> Optional[str]:
        """Extract original author from retweet title"""
        if self.is_retweet:
            # For "RT by @username:" format, the original author is in dc:creator
            # So we'll set this in the fetch function
            pass
        return None

def load_config() -> Dict:
    """Load configuration from accounts.yaml"""
    with open('accounts.yaml', 'r') as f:
        return yaml.safe_load(f)

def parse_account_lists(config: Dict) -> List[AccountList]:
    """Parse account lists from configuration"""
    account_lists = []
    
    # Handle new format
    if 'account_lists' in config:
        for list_config in config['account_lists']:
            account_lists.append(AccountList(
                name=list_config['name'],
                accounts=list_config['accounts'],
                max_posts=list_config.get('max_posts'),
                custom_settings=list_config.get('custom_settings', {})
            ))
    # Fallback to old format for backward compatibility
    elif 'accounts' in config:
        for account in config['accounts']:
            handle = account['handle']
            account_lists.append(AccountList(
                name=handle,
                accounts=[handle]
            ))
    
    return account_lists

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
        conn.commit()

def fetch_quoted_tweet_content(quote_url: str) -> tuple[Optional[str], Optional[str], List[str]]:
    """Fetch quoted tweet content from Nitter page and return (author, text, image_urls)"""
    if not quote_url:
        return None, None, []
    
    try:
        response = httpx.get(quote_url, timeout=15)
        response.raise_for_status()
        
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract quoted tweet author
        author = None
        author_elem = soup.select_one('.tweet-header .username')
        if author_elem:
            author = author_elem.get_text().strip()
            # Remove @ if present since we'll add it back in rendering
            if author.startswith('@'):
                author = author[1:]
        
        # Extract quoted tweet text
        text = None
        text_elem = soup.select_one('.tweet-content')
        if text_elem:
            # Remove quote tweet links and clean up
            for link in text_elem.select('a.quote-link'):
                link.decompose()
            text = text_elem.get_text().strip()
        
        # Extract images from quoted tweet
        image_urls = []
        # Look for images in the attachments section of the main tweet
        img_elems = soup.select('.main-tweet .attachments .still-image img')
        for img in img_elems:
            src = img.get('src')
            if src and src.startswith('/pic/'):
                # Convert relative URL to absolute
                base_url = os.getenv('NITTER_BASE_URL', '')
                full_url = base_url + src
                image_urls.append(full_url)
        
        return author, text, image_urls
        
    except Exception as e:
        print(f"Failed to fetch quoted tweet content from {quote_url}: {e}")
        return None, None, []

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

def download_profile_pic(handle: str, profile_pic_url: str) -> tuple[Optional[str], Optional[str]]:
    """Download profile picture and return (local_path, server_url)"""
    if not profile_pic_url:
        return None, None
    
    date_folder = datetime.now().strftime('%Y-%m-%d')
    images_dir = Path(f'images/{date_folder}')
    images_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        response = httpx.get(profile_pic_url, timeout=10)
        response.raise_for_status()
        
        ext = get_image_extension(profile_pic_url, response.headers)
        filename = f"{handle}_profile{ext}"
        filepath = images_dir / filename
        
        # Only download if we don't already have it
        if not filepath.exists():
            filepath.write_bytes(response.content)
            time.sleep(uniform(0.1, 0.3))
        
        # Upload to image server
        server_url = upload_to_image_server(str(filepath))
        
        return str(filepath), server_url
    except Exception as e:
        print(f"Failed to download profile pic for {handle}: {e}")
        return None, None

def download_images(tweet_id: str, handle: str, image_urls: List[str]) -> tuple[List[str], List[str]]:
    """Download images and return (local_paths, server_urls)"""
    if not image_urls:
        return [], []
    
    date_folder = datetime.now().strftime('%Y-%m-%d')
    images_dir = Path(f'images/{date_folder}')
    images_dir.mkdir(parents=True, exist_ok=True)
    
    local_paths = []
    server_urls = []
    
    for i, url in enumerate(image_urls):
        try:
            response = httpx.get(url, timeout=10)
            response.raise_for_status()
            
            ext = get_image_extension(url, response.headers)
            filename = f"{handle}_{tweet_id}_{i+1}{ext}"
            filepath = images_dir / filename
            
            filepath.write_bytes(response.content)
            local_paths.append(str(filepath))
            
            # Upload to image server
            server_url = upload_to_image_server(str(filepath))
            if server_url:
                server_urls.append(server_url)
            
            # Be polite - small delay between downloads
            time.sleep(uniform(0.1, 0.3))
            
        except Exception as e:
            print(f"Failed to download {url}: {e}")
    
    return local_paths, server_urls

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
        
        # Extract profile picture from feed metadata
        profile_pic_url = None
        if hasattr(feed.feed, 'image') and hasattr(feed.feed.image, 'url'):
            profile_pic_url = feed.feed.image.url
        
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
            
            # Extract image URLs from description HTML
            image_urls = []
            description = entry.get('description', '')
            if description:
                # Find all img tags in the description
                img_matches = re.findall(r'<img src="([^"]+)"[^>]*>', description)
                image_urls.extend(img_matches)
            
            # Also check media_content for fallback
            if hasattr(entry, 'media_content'):
                for media in entry.media_content:
                    if media.get('type', '').startswith('image/'):
                        image_urls.append(media['url'])
            
            # Use guid or link as ID
            post_id = entry.get('guid', entry.get('link', ''))
            if not post_id:
                continue
            
            # Create post object
            title = entry.get('title', '')
            post = Post(
                id=post_id,
                handle=handle,
                title=title,
                summary=entry.get('summary', ''),
                published=published,
                nitter_url=entry.get('link', ''),
                image_urls=image_urls,
                profile_pic_url=profile_pic_url,
                raw_description=description
            )
            
            # Handle retweets - get original author's info
            if post.is_retweet:
                original_author = entry.get('author', '')
                if original_author.startswith('@'):
                    original_author = original_author[1:]  # Remove @ symbol
                post.retweet_author = original_author
                
                # Try to get original author's profile pic
                if original_author:
                    try:
                        orig_feed_url = f"{base_url}/{original_author}/rss"
                        orig_response = httpx.get(orig_feed_url, timeout=10)
                        if orig_response.status_code == 200:
                            orig_feed = feedparser.parse(orig_response.content)
                            if hasattr(orig_feed.feed, 'image') and hasattr(orig_feed.feed.image, 'url'):
                                post.profile_pic_url = orig_feed.feed.image.url
                    except:
                        pass  # Use retweeter's profile pic as fallback
            
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
            # Ensure all values are properly formatted
            values = (
                post.id, 
                post.handle, 
                post.title, 
                post.summary, 
                post.published.isoformat(), 
                post.nitter_url, 
                post.x_url,
                json.dumps(post.image_urls), 
                json.dumps(post.image_paths),
                post.profile_pic_url, 
                post.profile_pic_path,
                post.profile_pic_server_url, 
                json.dumps(post.server_image_urls),
                post.raw_description, 
                post.is_retweet, 
                post.is_reply, 
                post.quote_tweet_url,
                post.quote_author,
                post.quote_text,
                json.dumps(post.quote_image_urls),
                post.retweet_author,
                True  # MVP includes all posts
            )
            
            conn.execute('''
                INSERT OR REPLACE INTO tweets 
                (id, handle, title, summary, published, nitter_url, x_url, 
                 image_urls, image_paths, profile_pic_url, profile_pic_path,
                 profile_pic_server_url, server_image_urls, raw_description, is_retweet, is_reply, 
                 quote_tweet_url, quote_author, quote_text, quote_image_urls, retweet_author, included_in_newsletter)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', values)
        conn.commit()

def render_tweet_html(post: Post) -> str:
    """Render a single tweet in Twitter-like HTML format"""
    # Get profile picture from Imgur URL or fallback
    profile_pic_html = ''
    if post.profile_pic_server_url:
        profile_pic_html = f'<img src="{post.profile_pic_server_url}" style="width: 48px; height: 48px; border-radius: 50%; margin-right: 12px;">'
    else:
        # Fallback placeholder
        profile_pic_html = '<div style="width: 48px; height: 48px; border-radius: 50%; background: #1da1f2; margin-right: 12px; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; font-size: 18px;">{}</div>'.format(post.handle[0].upper())
    
    # Handle retweet formatting
    if post.is_retweet:
        retweet_header = f'<div style="color: #657786; font-size: 13px; margin-bottom: 8px;">🔁 Retweeted by @{post.handle}</div>'
        display_handle = post.retweet_author or 'unknown'
        # For retweets, use the original tweet content from description
        tweet_text = re.sub(r'<[^>]+>', '', post.raw_description) if post.raw_description else ''
        tweet_text = re.sub(r'\s+', ' ', tweet_text).strip()  # Clean up whitespace
    else:
        retweet_header = ''
        display_handle = post.handle
        # Clean up tweet text (remove HTML tags from summary for display)
        tweet_text = re.sub(r'<[^>]+>', '', post.summary) if post.summary else ''
    
    
    # Handle quote tweet with actual content
    quote_tweet_html = ''
    if post.quote_tweet_url and (post.quote_author or post.quote_text):
        # Render quote tweet with actual content
        author_display = f"@{post.quote_author}" if post.quote_author else "Unknown"
        text_display = post.quote_text if post.quote_text else "[No text content]"
        
        # Handle quote tweet images
        quote_images_html = ''
        if post.quote_image_urls:
            quote_images_html = '<div style="margin-top: 8px;">'
            for quote_img_url in post.quote_image_urls:
                quote_images_html += f'<img src="{quote_img_url}" style="max-width: 100%; height: auto; border-radius: 8px; margin: 2px 0; display: block;">'
            quote_images_html += '</div>'
        
        quote_tweet_html = f'''
        <div style="border: 1px solid #e1e8ed; border-radius: 12px; padding: 12px; margin-top: 12px; background: #f7f9fa;">
            <div style="color: #657786; font-size: 13px; margin-bottom: 8px; font-weight: bold;">💬 Quoting {author_display}</div>
            <div style="color: #14171a; font-size: 14px; line-height: 1.3; margin-bottom: 8px;">{text_display}</div>
            {quote_images_html}
            <div style="color: #1da1f2; font-size: 12px; margin-top: 8px;">
                <a href="{post.quote_tweet_url}" style="color: #1da1f2; text-decoration: none;">View original →</a>
            </div>
        </div>
        '''
    elif post.quote_tweet_url:
        # Fallback for quotes where we couldn't fetch content
        quote_author = 'unknown'
        if '/status/' in post.quote_tweet_url:
            url_parts = post.quote_tweet_url.split('/')
            for i, part in enumerate(url_parts):
                if part == 'status' and i > 0:
                    quote_author = url_parts[i-1].split('.')[-1]
                    break
        
        quote_tweet_html = f'''
        <div style="border: 1px solid #e1e8ed; border-radius: 12px; padding: 12px; margin-top: 12px; background: #f7f9fa;">
            <div style="color: #657786; font-size: 13px; margin-bottom: 6px;">💬 Quoting @{quote_author}</div>
            <div style="color: #1da1f2; font-size: 13px;">
                <a href="{post.quote_tweet_url}" style="color: #1da1f2; text-decoration: none;">View quoted tweet →</a>
            </div>
        </div>
        '''
    
    # Embed images using server URLs
    images_html = ''
    if post.server_image_urls:
        images_html = '<div style="margin-top: 12px;">'
        for server_url in post.server_image_urls:
            images_html += f'<img src="{server_url}" style="max-width: 100%; height: auto; border-radius: 12px; margin: 4px 0; display: block;">'
        images_html += '</div>'
    
    # Format timestamp
    time_str = post.published.strftime('%I:%M %p · %b %d, %Y')
    
    return f'''
    <div style="border: 1px solid #e1e8ed; border-radius: 12px; padding: 16px; margin: 12px 0; background: white;">
        {retweet_header}
        <div style="display: flex; align-items: flex-start;">
            {profile_pic_html}
            <div style="flex: 1;">
                <div style="font-weight: bold; color: #14171a;">@{display_handle}</div>
                <div style="color: #657786; font-size: 13px; margin-bottom: 8px;">{time_str}</div>
                <div style="color: #14171a; font-size: 15px; line-height: 1.4; white-space: pre-wrap;">{tweet_text}</div>
                {quote_tweet_html}
                {images_html}
                <div style="margin-top: 12px; padding-top: 8px; border-top: 1px solid #e1e8ed;">
                    <a href="{post.nitter_url}" style="color: #1da1f2; text-decoration: none; font-size: 13px; margin-right: 16px;">View on Nitter</a>
                    <a href="{post.x_url}" style="color: #1da1f2; text-decoration: none; font-size: 13px;">View on X</a>
                </div>
            </div>
        </div>
    </div>
    '''

def render_email(posts: List[Post], account_list: AccountList) -> tuple[str, str]:
    """Render email content as text and HTML for an account list"""
    if not posts:
        return f"No new posts found for {account_list.name}.", f"<p>No new posts found for {account_list.name}.</p>"
    
    # Determine title and header based on account list
    if len(account_list.accounts) == 1:
        title = f"@{account_list.accounts[0]} Newsletter"
        header_text = f"📧 @{account_list.accounts[0]} Newsletter"
    else:
        title = f"{account_list.name} Newsletter"
        header_text = f"📧 {account_list.name} Newsletter"
    
    # Text version
    text_parts = [f"{title}\n" + "="*50 + "\n"]
    
    if len(account_list.accounts) == 1:
        # Single account - simple list
        text_parts.append(f"\n{len(posts)} new posts:")
        text_parts.append("-" * 30)
        for post in posts:
            if post.is_retweet:
                text_parts.append(f"🔁 Retweeted: {post.title}")
            else:
                text_parts.append(f"• {post.title}")
            text_parts.append(f"  {post.nitter_url}")
            text_parts.append("")
    else:
        # Multiple accounts - group by handle
        by_handle = {}
        for post in posts:
            if post.handle not in by_handle:
                by_handle[post.handle] = []
            by_handle[post.handle].append(post)
        
        for handle, handle_posts in by_handle.items():
            text_parts.append(f"\n@{handle} ({len(handle_posts)} posts):")
            text_parts.append("-" * 30)
            for post in handle_posts:
                if post.is_retweet:
                    text_parts.append(f"🔁 Retweeted: {post.title}")
                else:
                    text_parts.append(f"• {post.title}")
                text_parts.append(f"  {post.nitter_url}")
                text_parts.append("")
    
    text_content = "\n".join(text_parts)
    
    # HTML version with tweet-like formatting
    html_parts = [f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{title}</title>
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; background: #f7f9fa; padding: 20px;">
    <div style="background: white; border-radius: 16px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
        <h1 style="color: #14171a; margin: 0 0 24px 0; font-size: 24px;">{header_text}</h1>
        <div style="color: #657786; font-size: 14px; margin-bottom: 24px;">{len(posts)} new posts</div>
    """]
    
    # Render all tweets chronologically (oldest first)
    for post in sorted(posts, key=lambda p: p.published, reverse=False):
        html_parts.append(render_tweet_html(post))
    
    html_parts.append("""
    </div>
    <div style="text-align: center; margin-top: 20px; color: #657786; font-size: 12px;">
        Generated by Newsletter System
    </div>
    </body>
    </html>
    """)
    
    html_content = "".join(html_parts)
    
    return text_content, html_content

def send_email(text_content: str, html_content: str, subject: str = None):
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
    
    # Load configuration and parse account lists
    config = load_config()
    account_lists = parse_account_lists(config)
    window_hours = args.window or config.get('window_hours', 24)
    max_per_account = config.get('max_per_account', 10)
    
    # Initialize database
    init_database()
    
    print(f"Processing {len(account_lists)} account list(s)...")
    
    # Process each account list separately
    for account_list in account_lists:
        print(f"\n--- Processing {account_list.name} ---")
        
        # Collect new posts for this account list
        list_new_posts = []
        for handle in account_list.accounts:
            print(f"Fetching feed for @{handle}...")
            
            posts = fetch_feed(handle, window_hours)
            new_posts = [post for post in posts if is_new_post(post.id)]
            
            # Apply per-account limit (use account list override or global setting)
            limit = account_list.max_posts or max_per_account
            if len(new_posts) > limit:
                new_posts = new_posts[:limit]
                print(f"Limited to {limit} posts for @{handle}")
            
            # Download profile picture and images for new posts
            for post in new_posts:
                # Download profile picture (once per handle per day) and upload to Imgur
                if post.profile_pic_url:
                    post.profile_pic_path, post.profile_pic_server_url = download_profile_pic(handle, post.profile_pic_url)
                
                # Download tweet images and upload to image server
                if post.image_urls:
                    post.image_paths, post.server_image_urls = download_images(
                        post.id.split('/')[-1],  # Use last part of ID as tweet ID
                        handle, 
                        post.image_urls
                    )
                
                # Fetch quoted tweet content if quote tweet exists
                if post.quote_tweet_url:
                    print(f"Fetching quoted tweet content from {post.quote_tweet_url}")
                    post.quote_author, post.quote_text, post.quote_image_urls = fetch_quoted_tweet_content(post.quote_tweet_url)
                    
                    # Download quoted tweet images and upload to image server
                    if post.quote_image_urls:
                        quote_paths, quote_server_urls = download_images(
                            post.id.split('/')[-1] + "_quote",  # Add _quote suffix to distinguish from regular images
                            handle, 
                            post.quote_image_urls
                        )
                        # Replace the Nitter URLs with server URLs for email display
                        post.quote_image_urls = quote_server_urls
                    
                    # Small delay to be polite to Nitter instance
                    time.sleep(uniform(0.5, 1.0))
            
            list_new_posts.extend(new_posts)
            print(f"Found {len(new_posts)} new posts from @{handle}")
            
            # Be polite - sleep between feeds
            time.sleep(uniform(0.3, 0.8))
        
        if not list_new_posts:
            print(f"No new posts found for {account_list.name}.")
            continue
        
        # Save posts to database
        save_posts(list_new_posts)
        
        # Render email for this account list
        text_content, html_content = render_email(list_new_posts, account_list)
        subject = account_list.get_email_subject()
        
        if args.dry_run:
            print(f"\n" + "="*60)
            print(f"DRY RUN - {account_list.name} Newsletter:")
            print("="*60)
            print(f"Subject: {subject}")
            print(f"Posts: {len(list_new_posts)}")
            print("-"*30)
            print(text_content[:500] + "..." if len(text_content) > 500 else text_content)
            print("\n" + "="*60)
            print(f"Would email {len(list_new_posts)} posts to {os.getenv('MAIL_TO', 'unknown')}")
        else:
            send_email(text_content, html_content, subject)
            print(f"{account_list.name} newsletter sent with {len(list_new_posts)} posts!")
    
    print("\nAll newsletters processed!")

if __name__ == '__main__':
    main()