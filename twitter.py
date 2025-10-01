"""
Twitter-specific functionality for the newsletter system.
Handles RSS feed fetching, tweet processing, and email generation.
"""

import json
import sqlite3
import time
import re
import html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import feedparser
import httpx
import os

from bs4 import BeautifulSoup, NavigableString
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from common_utils import send_email, upload_to_image_server, get_image_extension, log_or_print


MAX_QUOTE_DEPTH = 3


def convert_to_local_timezone(dt: datetime, timezone_str: str) -> datetime:
    """Convert UTC datetime to local timezone"""
    if dt.tzinfo is None:
        # Assume UTC if no timezone info
        dt = dt.replace(tzinfo=timezone.utc)

    try:
        local_tz = ZoneInfo(timezone_str)
        return dt.astimezone(local_tz)
    except Exception:
        # Fallback to UTC if timezone conversion fails
        return dt



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
        self._x_url = None  # Will be set via set_x_url method
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

        # New nested quote structure
        self.quote_data = None  # Will store nested dict or be None
    
    def set_x_url(self, config: Dict):
        """Set the x.com URL by replacing the Nitter base URL"""
        base_url = config.get('nitter', {}).get('base_url', '')
        self.x_url = self.nitter_url.replace(base_url, 'https://x.com')
    
    @property
    def x_url(self):
        """Get the x.com URL"""
        return self._x_url
    
    @x_url.setter
    def x_url(self, value):
        """Set the x.com URL"""
        self._x_url = value
    
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

    def set_quote_data(self, quote_data_dict):
        """Set nested quote data and populate legacy fields for compatibility"""
        self.quote_data = quote_data_dict
        if quote_data_dict:
            self.quote_tweet_url = quote_data_dict.get("url")
            self.quote_author = quote_data_dict.get("author")
            self.quote_text = quote_data_dict.get("text")
            self.quote_image_urls = quote_data_dict.get("image_urls", [])

    def serialize_quote_data_for_db(self):
        """Serialize quote data for database storage"""
        if self.quote_data:
            return json.dumps(self.quote_data)
        return self.quote_text  # Fallback to legacy text

    @staticmethod
    def deserialize_quote_data_from_db(quote_text_field):
        """Deserialize quote data from database field"""
        if not quote_text_field:
            return None
        if quote_text_field.startswith('{'):
            try:
                return json.loads(quote_text_field)
            except json.JSONDecodeError:
                return None
        return None  # Legacy text format, handled by legacy fields


def format_tweet_body_html(raw_html: Optional[str]) -> str:
    """Return sanitized HTML for tweet body, preserving full hyperlink targets."""
    if not raw_html:
        return ''

    soup = BeautifulSoup(raw_html, 'html.parser')

    for text_node in list(soup.find_all(string=True)):
        if isinstance(text_node, NavigableString) and not text_node.strip():
            text_node.extract()

    block_level_tags = {'p', 'div', 'blockquote', 'li'}
    container_tags = {'ul', 'ol'}

    for tag in soup.find_all(True):
        if tag.name == 'a':
            href = tag.get('href')
            if not href:
                tag.unwrap()
                continue

            display_text = tag.get_text(separator='', strip=True)
            truncated_display = False
            if display_text:
                truncated_display = '...' in display_text or '\u2026' in display_text

            should_replace_with_href = truncated_display or not display_text
            if display_text and display_text.startswith('https://t.co/'):
                should_replace_with_href = True

            if should_replace_with_href:
                tag.clear()
                tag.append(NavigableString(href))

            tag.attrs = {
                'href': href,
                'style': 'color: #1da1f2; text-decoration: none;'
            }
        elif tag.name == 'br':
            continue
        elif tag.name in block_level_tags:
            tag.append(soup.new_tag('br'))
            tag.unwrap()
        elif tag.name in container_tags:
            tag.unwrap()
        else:
            tag.unwrap()

    sanitized_html = str(soup)
    sanitized_html = sanitized_html.replace('<br/>', '\n').replace('<br />', '\n').replace('<br>', '\n')

    return sanitized_html.strip()


def normalize_nitter_status_url(href: Optional[str], base_url: str) -> Optional[str]:
    """Return an absolute Nitter URL for a status link when possible."""
    if not href:
        return None

    trimmed_href = href.strip()
    if not trimmed_href:
        return None

    parsed = urlparse(trimmed_href)

    if parsed.scheme and parsed.netloc:
        normalized_netloc = parsed.netloc.lower()
        if normalized_netloc.endswith('twitter.com') or normalized_netloc.endswith('x.com'):
            if not base_url:
                return trimmed_href
            normalized_base = base_url if base_url.endswith('/') else f"{base_url}/"
            return urljoin(normalized_base, parsed.path.lstrip('/'))
        return trimmed_href

    if base_url:
        normalized_base = base_url if base_url.endswith('/') else f"{base_url}/"
        return urljoin(normalized_base, trimmed_href.lstrip('/'))

    return trimmed_href


def find_nested_quote_url(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """Locate the first nested quote URL within a tweet page."""
    selectors = (
        '.main-tweet .tweet-content a.quote-link',
        '.main-tweet .quote a.quote-link',
        '.main-tweet .quote a[href*="status/"]'
    )

    for selector in selectors:
        link = soup.select_one(selector)
        if not link:
            continue
        normalized = normalize_nitter_status_url(link.get('href'), base_url)
        if normalized:
            return normalized

    return None


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


def extract_quote_tweet_url_from_text(text: str) -> Optional[str]:
    """Extract quote tweet URL from plain text content"""
    if not text:
        return None
    # Look for status URLs in text (Nitter or X.com format)
    match = re.search(r'(https?://[^\s]*status/\d+)', text)
    return match.group(1) if match else None


def fetch_basic_quote_content(
        quote_url: str, config: Dict, logger=None) -> tuple[Optional[str], Optional[str], List[str], Optional[str], Optional[datetime]]:
    """Fetch quoted tweet content from Nitter page and return (author, text, image_urls, nested_quote_url, published)"""
    if not quote_url:
        return None, None, [], None, None

    try:
        # Use retry decorator for HTTP request with exponential backoff
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))
        )
        def fetch_quote_page():
            response = httpx.get(quote_url, timeout=15)
            response.raise_for_status()
            return response

        response = fetch_quote_page()
        soup = BeautifulSoup(response.content, 'html.parser')
        base_url = config.get('nitter', {}).get('base_url', '')

        # Extract quoted tweet author
        author = None
        author_elem = soup.select_one('.main-tweet .tweet-header .username')
        if author_elem:
            author = author_elem.get_text().strip()
            # Remove @ if present since we'll add it back in rendering
            if author.startswith('@'):
                author = author[1:]

        # Extract quoted tweet text
        text = None
        nested_quote_url = None
        text_elem = soup.select_one('.main-tweet .tweet-content')
        if text_elem:
            quote_links = text_elem.select('a.quote-link')
            for link in quote_links:
                if not nested_quote_url:
                    nested_quote_url = normalize_nitter_status_url(link.get('href'), base_url)
                link.decompose()
            text = text_elem.get_text().strip()

        if not nested_quote_url:
            nested_quote_url = find_nested_quote_url(soup, base_url)

        # Extract timestamp
        published = None
        timestamp_elem = soup.select_one('.main-tweet .tweet-header .tweet-date a')
        if timestamp_elem:
            timestamp_title = timestamp_elem.get('title')
            if timestamp_title:
                try:
                    # Nitter format: "Feb 15, 2025 · 3:45 PM UTC"
                    published = datetime.strptime(timestamp_title, '%b %d, %Y · %I:%M %p %Z')
                    published = published.replace(tzinfo=timezone.utc)
                except ValueError:
                    log_or_print(f"Failed to parse timestamp: {timestamp_title}", 'warning', logger)

        # Extract images from quoted tweet
        image_urls = []
        # Look for images in the attachments section of the main tweet
        img_elems = soup.select('.main-tweet .attachments .still-image img')
        for img in img_elems:
            src = img.get('src')
            if src:
                if src.startswith('/pic/') and base_url:
                    image_urls.append(f"{base_url}{src}")
                else:
                    image_urls.append(src)

        return author, text, image_urls, nested_quote_url, published

    except Exception as e:
        log_or_print(f"Failed to fetch quoted tweet content from {quote_url}: {e}", 'warning', logger)
        return None, None, [], None, None


def fetch_quoted_tweet_content_recursive(
        quote_url: str,
        config: Dict,
        max_depth: int = MAX_QUOTE_DEPTH,
        current_depth: int = 0,
        logger=None) -> Optional[Dict]:
    """Recursively fetch nested quote content with depth limit"""
    if current_depth >= max_depth or not quote_url:
        return None

    # Fetch basic quote content
    author, text, image_urls, nested_quote_url, published = fetch_basic_quote_content(quote_url, config, logger)

    if not author and not text:
        return None

    quote_data = {
        "url": quote_url,
        "author": author,
        "text": text,
        "image_urls": image_urls,
        "published": published.isoformat() if published else None
    }

    # Recursively check for nested quotes
    if text or nested_quote_url:
        nested_url = nested_quote_url or extract_quote_tweet_url_from_text(text)
        if nested_url:
            base_url = config.get('nitter', {}).get('base_url', '')
            nested_url = normalize_nitter_status_url(nested_url, base_url)
        if nested_url and nested_url != quote_url:
            nested_data = fetch_quoted_tweet_content_recursive(
                nested_url, config, max_depth, current_depth + 1, logger
            )
            if nested_data:
                quote_data["nested_quote"] = nested_data

    return quote_data


def get_profile_pic_url_from_nitter(handle: str, config: Dict, logger=None) -> Optional[str]:
    """Fetch profile picture URL from Nitter user page"""
    base_url = config.get('nitter', {}).get('base_url')
    if not base_url:
        return None
    
    try:
        user_url = f"{base_url}/{handle}"

        # Use retry decorator for HTTP request with exponential backoff
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))
        )
        def fetch_user_page():
            response = httpx.get(user_url, timeout=10)
            response.raise_for_status()
            return response

        response = fetch_user_page()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Check if the page contains actual profile content
        profile_card = soup.select_one('.profile-card')
        timeline = soup.select_one('.timeline')
        
        # If no profile content found, account might not exist or be suspended
        if not profile_card and not timeline:
            log_or_print(f"Profile not accessible for @{handle} (account may not exist or be suspended)", 'warning', logger)
            return None
        
        # Try multiple selectors for avatar image (profile picture, not banner)
        avatar_selectors = [
            '.profile-card .avatar img',
            '.avatar img',
            'img.avatar',
            'img[class*="avatar"]',
            'img[src*="/pic/"][src*="profile_images"]'  # More specific for profile images
        ]
        
        avatar_img = None
        for selector in avatar_selectors:
            avatar_img = soup.select_one(selector)
            if avatar_img:
                break
        
        if avatar_img:
            src = avatar_img.get('src')
            if src and src.startswith('/pic/'):
                return base_url + src
            elif src:
                return src
        
        return None
        
    except Exception as e:
        log_or_print(f"Failed to fetch profile pic URL for @{handle}: {e}", 'warning', logger)
        return None


def download_profile_pic(handle: str, profile_pic_url: str, run_timestamp: str, config: Dict, logger=None) -> tuple[Optional[str], Optional[str]]:
    """Download profile picture with unique naming and return (local_path, server_url)"""
    if not profile_pic_url:
        return None, None
    
    date_folder = datetime.now().strftime('%Y-%m-%d')
    images_dir = Path(f'images/{date_folder}')
    images_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Use retry decorator for HTTP request with exponential backoff
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))
        )
        def fetch_profile_image():
            response = httpx.get(profile_pic_url, timeout=10)
            response.raise_for_status()
            return response

        response = fetch_profile_image()
        ext = get_image_extension(profile_pic_url, response.headers)
        # Use run timestamp to ensure uniqueness across runs
        filename = f"{handle}_profile_{run_timestamp}{ext}"
        filepath = images_dir / filename
        
        filepath.write_bytes(response.content)
        
        # Upload to image server
        server_url = upload_to_image_server(str(filepath), config)
        
        log_or_print(f"Downloaded profile picture for @{handle}", 'info', logger)
        return str(filepath), server_url
        
    except Exception as e:
        log_or_print(f"Failed to download profile pic for @{handle}: {e}", 'warning', logger)
        return None, None


def extract_all_quote_authors(quote_data: Dict) -> set:
    """Recursively extract all authors from nested quote structure"""
    authors = set()
    if not quote_data:
        return authors

    # Add author from current level
    if quote_data.get("author"):
        authors.add(quote_data["author"])

    # Recursively extract from nested quotes
    if quote_data.get("nested_quote"):
        authors.update(extract_all_quote_authors(quote_data["nested_quote"]))

    return authors


def download_quote_images_recursive(post: Post, quote_data: Dict, handle: str, config: Dict, logger=None):
    """Recursively download images for nested quote structure"""
    def download_images_for_quote(quote_data_level: Dict, suffix: str):
        """Download images for a specific quote level"""
        if quote_data_level.get("image_urls"):
            tweet_id = post.id.split('/')[-1]
            paths, server_urls = download_images(
                tweet_id + suffix,
                handle,
                quote_data_level["image_urls"],
                config,
                logger
            )
            # Replace Nitter URLs with server URLs in the quote data
            quote_data_level["image_urls"] = server_urls

    # Download images for main quote
    download_images_for_quote(quote_data, "_quote")

    # Recursively download images for nested quotes
    current_quote = quote_data
    depth = 1
    while current_quote.get("nested_quote"):
        current_quote = current_quote["nested_quote"]
        download_images_for_quote(current_quote, f"_nested{depth}")
        depth += 1


def download_images(tweet_id: str, handle: str, image_urls: List[str], config: Dict, logger=None) -> tuple[List[str], List[str]]:
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
            # Use retry decorator for HTTP request with exponential backoff
            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))
            )
            def fetch_image():
                response = httpx.get(url, timeout=10)
                response.raise_for_status()
                return response

            response = fetch_image()
            ext = get_image_extension(url, response.headers)
            filename = f"{handle}_{tweet_id}_{i+1}{ext}"
            filepath = images_dir / filename

            filepath.write_bytes(response.content)
            local_paths.append(str(filepath))

            # Upload to image server
            server_url = upload_to_image_server(str(filepath), config)
            if server_url:
                server_urls.append(server_url)

        except Exception as e:
            log_or_print(f"Failed to download {url}: {e}", 'warning', logger)
    
    return local_paths, server_urls


def fetch_feed(handle: str, window_hours: int, config: Dict, max_posts: int = None, logger=None) -> List[Post]:
    """Fetch RSS feed for a handle with pagination until we find old non-retweet"""
    base_url = config.get('nitter', {}).get('base_url')
    if not base_url:
        raise ValueError("NITTER_BASE_URL not set in config")
    
    posts = []
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    cursor = None
    page_count = 0
    max_pages = 10  # Safety limit to prevent infinite loops
    profile_pic_url = None
    
    log_or_print(f"Fetching RSS pages until we find non-retweet older than {window_hours}h cutoff...", 'info', logger)
    
    while page_count < max_pages:
        # Build URL with cursor if we have one
        if cursor:
            feed_url = f"{base_url}/{handle}/rss?cursor={cursor}"
        else:
            feed_url = f"{base_url}/{handle}/rss"
        
        try:
            # Use retry decorator for HTTP request with exponential backoff
            @retry(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException))
            )
            def fetch_rss_page():
                response = httpx.get(feed_url, timeout=30)
                response.raise_for_status()
                return response

            response = fetch_rss_page()

            # Get cursor for next page from min-id header
            next_cursor = response.headers.get('min-id')

            feed = feedparser.parse(response.content)
            page_count += 1
            
            if feed.bozo:
                log_or_print(f"Feed parsing issues for {handle} on page {page_count}", 'warning', logger)
            
            # Extract profile picture from feed metadata (only from first page)
            if page_count == 1 and hasattr(feed.feed, 'image') and hasattr(feed.feed.image, 'url'):
                profile_pic_url = feed.feed.image.url
            
            # Track if we should continue paginating
            should_continue = False
            found_old_non_retweet = False
            hit_max_posts = False
            
            if not feed.entries:
                log_or_print(f"No more entries found on page {page_count}", 'info', logger)
                break
            
            for entry in feed.entries:
                # Parse published date
                published = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                
                if not published:
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
                post.set_x_url(config)
                
                # Handle retweets - get original author's info
                if post.is_retweet:
                    original_author = entry.get('author', '')
                    if original_author.startswith('@'):
                        original_author = original_author[1:]  # Remove @ symbol
                    post.retweet_author = original_author
                    # Note: Keep profile_pic_url as the retweeter's pic (from feed metadata)
                
                # Check stopping condition: non-retweet older than cutoff
                if not post.is_retweet and published < cutoff_time:
                    found_old_non_retweet = True
                    log_or_print(f"Found non-retweet from {published.strftime('%Y-%m-%d %H:%M')} (older than cutoff), stopping pagination", 'info', logger)
                    break
                
                # If tweet is within window, add it to results
                if published >= cutoff_time:
                    posts.append(post)
                    should_continue = True  # Continue looking for more recent tweets
                    
                    # Check if we've hit the max posts limit
                    if max_posts and len(posts) >= max_posts:
                        hit_max_posts = True
                        log_or_print(f"Hit max posts limit ({max_posts}), stopping pagination", 'info', logger)
                        break
                
            # Stop pagination if we found old non-retweet, hit max posts, or no cursor for next page
            if found_old_non_retweet or hit_max_posts or not next_cursor or next_cursor == cursor:
                break
            
            cursor = next_cursor
            log_or_print(f"Page {page_count}: found {len([p for p in posts if p.handle == handle])} tweets within window, continuing...", 'info', logger)
            
            # Be polite - small delay between pages
            time.sleep(0.1)
            
        except Exception as e:
            log_or_print(f"Error fetching page {page_count} for {handle}: {e}", 'error', logger)
            break
    
    # Filter posts to only this handle (in case of any issues)
    handle_posts = [post for post in posts if post.handle == handle]
    log_or_print(f"Completed after {page_count} page(s), found {len(handle_posts)} tweets within {window_hours}h window", 'info', logger)
    
    return handle_posts


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
                post.serialize_quote_data_for_db(),
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


def render_quote_html_recursive(quote_data: Dict, depth=0, author_pfps=None, timezone_str: str = "UTC") -> str:
    """Recursively render nested quote structure"""
    if not quote_data:
        return ""

    # Adjust styling based on nesting depth
    indent = depth * 16  # Increase indentation per level
    font_size = max(12, 14 - depth)  # Smaller font for deeper nesting

    # Get profile picture for quote author
    quote_author = quote_data.get("author", "unknown")
    profile_pic_html = ''
    if author_pfps and quote_author in author_pfps:
        author_pfp_info = author_pfps[quote_author]
        author_pfp_server_url = author_pfp_info[1] if author_pfp_info else None
        if author_pfp_server_url:
            pic_size = max(20, 24 - depth * 2)  # Smaller profile pics for deeper nesting
            profile_pic_html = f'<img src="{author_pfp_server_url}" style="width: {pic_size}px; height: {pic_size}px; border-radius: 50%; margin-right: 8px; vertical-align: middle;">'

    quote_text = quote_data.get("text") or ""

    # Format timestamp if present
    timestamp_html = ''
    published_iso = quote_data.get("published")
    if published_iso:
        try:
            published_dt = datetime.fromisoformat(published_iso)
            local_dt = convert_to_local_timezone(published_dt, timezone_str)
            time_str = local_dt.strftime('%I:%M %p · %b %d, %Y')
            timestamp_html = f'<div style="color: #657786; font-size: 11px; margin-bottom: 4px;">{time_str}</div>'
        except (ValueError, TypeError):
            pass

    quote_html = f'''
    <div style="border: 1px solid #e1e8ed; border-radius: 8px; padding: 8px;
                margin: 8px 0 8px {indent}px; background: #f7f9fa; font-size: {font_size}px;">
        <div style="font-weight: bold; margin-bottom: 4px; display: flex; align-items: center;">
            {profile_pic_html}💬 @{quote_author}
        </div>
        {timestamp_html}
        <div style="margin-bottom: 6px; white-space: pre-wrap;">{html.escape(quote_text)}</div>'''

    # Add images if present
    if quote_data.get("image_urls"):
        quote_html += '<div style="margin: 4px 0;">'
        for img_url in quote_data["image_urls"]:
            quote_html += f'<img src="{img_url}" style="max-width: 100%; height: auto; border-radius: 4px; margin: 2px 0; display: block;">'
        quote_html += '</div>'

    # Recursively render nested quote
    if quote_data.get("nested_quote"):
        quote_html += render_quote_html_recursive(quote_data["nested_quote"], depth + 1, author_pfps, timezone_str)

    quote_html += f'<div style="margin-top: 4px;"><a href="{quote_data.get("url", "")}" style="color: #1da1f2; font-size: 11px;">View original →</a></div>'
    quote_html += '</div>'

    return quote_html


def render_tweet_html(post: Post, author_pfps: Dict[str, tuple[Optional[str], Optional[str]]], timezone_str: str = "UTC") -> str:
    """Render a single tweet in Twitter-like HTML format"""
    
    # Determine which author's profile picture to show
    if post.is_retweet:
        # For retweets, show the original author's profile picture
        display_author = post.retweet_author or 'unknown'
        retweet_header = f'<div style="color: #657786; font-size: 13px; margin-bottom: 8px;">🔁 Retweeted by @{post.handle}</div>'
        display_handle = display_author
        tweet_body_html = format_tweet_body_html(post.raw_description or post.summary)
    else:
        # For regular tweets and quote tweets, show the main account's profile picture
        display_author = post.handle
        retweet_header = ''
        display_handle = post.handle
        tweet_body_html = format_tweet_body_html(post.summary or post.raw_description)

    if not tweet_body_html:
        tweet_body_html = ''
    
    # Get profile picture from author_pfps dictionary
    profile_pic_html = ''
    author_pfp_info = author_pfps.get(display_author, (None, None))
    author_pfp_server_url = author_pfp_info[1] if author_pfp_info else None
    
    if author_pfp_server_url:
        profile_pic_html = f'<img src="{author_pfp_server_url}" style="width: 48px; height: 48px; border-radius: 50%; margin-right: 12px;">'
    else:
        # Fallback placeholder
        profile_pic_html = '<div style="width: 48px; height: 48px; border-radius: 50%; background: #1da1f2; margin-right: 12px; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; font-size: 18px;">{}</div>'.format(display_author[0].upper())
    
    
    # Handle quote tweet with recursive rendering
    quote_tweet_html = ''
    if post.quote_data:
        quote_tweet_html = render_quote_html_recursive(post.quote_data, 0, author_pfps, timezone_str)
    elif post.quote_tweet_url:
        # Fallback for legacy data or failed quote fetching
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
    
    # Format timestamp with timezone conversion
    local_published = convert_to_local_timezone(post.published, timezone_str)
    time_str = local_published.strftime('%I:%M %p · %b %d, %Y')
    
    return f'''
    <div style="border: 1px solid #e1e8ed; border-radius: 12px; padding: 16px; margin: 12px 0; background: white;">
        {retweet_header}
        <div style="display: flex; align-items: flex-start;">
            {profile_pic_html}
            <div style="flex: 1;">
                <div style="font-weight: bold; color: #14171a;">@{display_handle}</div>
                <div style="color: #657786; font-size: 13px; margin-bottom: 8px;">{time_str}</div>
                <div style="color: #14171a; font-size: 15px; line-height: 1.4; white-space: pre-wrap;">{tweet_body_html}</div>
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


def render_email(posts: List[Post], account_list: AccountList, author_pfps: Dict[str, tuple[Optional[str], Optional[str]]], timezone_str: str = "UTC") -> tuple[str, str]:
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
        html_parts.append(render_tweet_html(post, author_pfps, timezone_str))
    
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


def main(dry_run: bool, window_hours: int = None, no_db: bool = False, recipient_email: str = None, config_path: str = None, secrets_path: str = None, account_lists_filter: List[str] = None, logger=None):
    """Main Twitter processing function"""
    import logging
    from common_utils import load_full_config, load_accounts_config, init_database

    if logger is None:
        logger = logging.getLogger('newsletter')
    
    # Load configuration
    full_config = load_full_config(config_path, secrets_path)
    accounts_config = load_accounts_config()
    
    # Parse account lists and settings
    account_lists = parse_account_lists(accounts_config)

    # Filter account lists if specified
    if account_lists_filter:
        filtered_lists = []
        adhoc_accounts = []

        for requested_name in account_lists_filter:
            # First, try to find existing account list by name
            found = False
            for account_list in account_lists:
                if account_list.name == requested_name:
                    filtered_lists.append(account_list)
                    found = True
                    break

            # If not found in existing lists, treat as adhoc account handle
            if not found:
                adhoc_accounts.append(requested_name)

        # Create adhoc account lists for handles not found in config
        for account_handle in adhoc_accounts:
            logger.info(f"Creating adhoc account list for @{account_handle}")
            adhoc_list = AccountList(
                name=account_handle,
                accounts=[account_handle]
            )
            filtered_lists.append(adhoc_list)

        # Log results
        found_names = [al.name for al in filtered_lists if al.name in [existing.name for existing in account_lists]]
        adhoc_names = [al.name for al in filtered_lists if al.name in adhoc_accounts]

        if found_names:
            logger.info(f"Found existing account lists: {', '.join(found_names)}")
        if adhoc_names:
            logger.info(f"Created adhoc account lists: {', '.join(adhoc_names)}")

        if not filtered_lists:
            logger.error(f"No accounts found or created for: {', '.join(account_lists_filter)}")
            return

        account_lists = filtered_lists
        logger.info(f"Processing {len(account_lists)} account list(s) total")
    window_hours = window_hours or full_config.get('newsletter', {}).get('window_hours', 24)
    max_per_account = full_config.get('newsletter', {}).get('max_per_account', 10)
    
    # Initialize database (unless in no-db mode)
    if not no_db:
        init_database()
    
    logger.info(f"Processing {len(account_lists)} Twitter account list(s)...")
    
    # Create unique run timestamp for profile picture naming
    run_timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Process each account list separately
    for account_list in account_lists:
        logger.info(f"Processing {account_list.name}")
        
        # Collect new posts for this account list
        list_new_posts = []
        for handle in account_list.accounts:
            logger.info(f"Fetching feed for @{handle}...")
            
            # Determine limit (use account list override or global setting)
            limit = account_list.max_posts or max_per_account
            
            posts = fetch_feed(handle, window_hours, full_config, limit, logger=logger)
            if no_db:
                # In no-db mode, treat all posts as new
                new_posts = posts
            else:
                new_posts = [post for post in posts if is_new_post(post.id)]
            
            # Limit should already be enforced during fetch, but double-check
            if len(new_posts) > limit:
                new_posts = new_posts[:limit]
                logger.info(f"Post-fetch limited to {limit} posts for @{handle}")
            
            # Download images for new posts
            for post in new_posts:
                
                # Download tweet images and upload to image server
                if post.image_urls:
                    post.image_paths, post.server_image_urls = download_images(
                        post.id.split('/')[-1],  # Use last part of ID as tweet ID
                        handle, 
                        post.image_urls,
                        full_config
                    )
                
                # Fetch quoted tweet content with nested quotes support
                if post.quote_tweet_url:
                    logger.info(f"Fetching quoted tweet content from {post.quote_tweet_url}")
                    quote_data = fetch_quoted_tweet_content_recursive(post.quote_tweet_url, full_config, max_depth=MAX_QUOTE_DEPTH, logger=logger)
                    post.set_quote_data(quote_data)

                    # Download images for all quotes in the nested structure
                    if quote_data:
                        download_quote_images_recursive(post, quote_data, handle, full_config, logger)
                    
                    # Small delay to be polite to Nitter instance
                    time.sleep(0.1)
            
            list_new_posts.extend(new_posts)
            logger.info(f"Found {len(new_posts)} new posts from @{handle}")
            
            # Be polite - sleep between feeds
            time.sleep(0.1)
        
        if not list_new_posts:
            logger.info(f"No new posts found for {account_list.name}")
            continue
        
        # Collect all unique authors and download their profile pictures
        unique_authors = set()
        author_pfps = {}  # author -> (local_path, server_url)
        
        for post in list_new_posts:
            # Add main account
            unique_authors.add(post.handle)
            
            # Add retweet author if it's a retweet
            if post.is_retweet and post.retweet_author:
                unique_authors.add(post.retweet_author)
            
            # Add all quote authors from nested structure
            if post.quote_data:
                quote_authors = extract_all_quote_authors(post.quote_data)
                unique_authors.update(quote_authors)
            elif post.quote_author:  # Fallback for legacy data
                unique_authors.add(post.quote_author)
        
        logger.info(f"Downloading profile pictures for {len(unique_authors)} unique authors...")
        
        # Download profile pictures for all unique authors
        for author in unique_authors:
            # For main accounts, we might have the profile pic URL from RSS
            known_url = None
            for post in list_new_posts:
                if post.handle == author and post.profile_pic_url:
                    known_url = post.profile_pic_url
                    break
            
            # Get profile pic URL (use known URL or fetch from Nitter)
            pic_url = known_url or get_profile_pic_url_from_nitter(author, full_config, logger)
            
            if pic_url:
                local_path, server_url = download_profile_pic(author, pic_url, run_timestamp, full_config, logger)
                if local_path and server_url:
                    author_pfps[author] = (local_path, server_url)
                    logger.info(f"Successfully downloaded and stored profile picture for @{author}")
                else:
                    logger.warning(f"Failed to download profile picture for @{author}")
            else:
                logger.warning(f"Could not get profile picture URL for @{author}")
            
            # Small delay to be polite to Nitter
            time.sleep(0.1)
        
        # Save posts to database (unless in no-db mode)
        if not no_db:
            save_posts(list_new_posts)
        
        # Render email for this account list
        timezone_str = full_config.get('newsletter', {}).get('timezone', 'UTC')
        text_content, html_content = render_email(list_new_posts, account_list, author_pfps, timezone_str)
        subject = account_list.get_email_subject()
        
        if dry_run:
            logger.info("=" * 60)
            logger.info(f"DRY RUN - {account_list.name} Newsletter")
            logger.info("=" * 60)
            logger.info(f"Subject: {subject}")
            logger.info(f"Posts: {len(list_new_posts)}")
            logger.info("-" * 30)
            logger.info(text_content[:500] + "..." if len(text_content) > 500 else text_content)
            logger.info("=" * 60)
            logger.info(f"Would email {len(list_new_posts)} posts to {recipient_email}")
        else:
            send_email(text_content, html_content, subject, recipient_email, full_config, logger=logger)
            logger.info(f"{account_list.name} newsletter sent with {len(list_new_posts)} posts!")
    
    logger.info("All Twitter newsletters processed!")
