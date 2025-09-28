#!/usr/bin/env python3
"""
Quick test script for nested quote tweet functionality
"""

import sys
sys.path.append('.')

from twitter import Post, extract_quote_tweet_url_from_text, render_quote_html_recursive
from datetime import datetime, timezone

def test_url_extraction():
    """Test URL extraction from text"""
    print("Testing URL extraction from text...")

    # Test with Nitter URL
    text1 = "This is a tweet with a quote https://nitter.example.com/user/status/123456 in it"
    url1 = extract_quote_tweet_url_from_text(text1)
    print(f"Text: {text1}")
    print(f"Extracted URL: {url1}")
    print()

    # Test with X.com URL
    text2 = "Another tweet with https://x.com/someone/status/789012 here"
    url2 = extract_quote_tweet_url_from_text(text2)
    print(f"Text: {text2}")
    print(f"Extracted URL: {url2}")
    print()

    # Test with no URL
    text3 = "Just a regular tweet with no quotes"
    url3 = extract_quote_tweet_url_from_text(text3)
    print(f"Text: {text3}")
    print(f"Extracted URL: {url3}")
    print()

def test_post_quote_data():
    """Test Post class quote data handling"""
    print("Testing Post class quote data methods...")

    # Create a test post
    post = Post(
        id="test_123",
        handle="testuser",
        title="Test Tweet",
        summary="This is a test tweet",
        published=datetime.now(timezone.utc),
        nitter_url="https://nitter.example.com/testuser/status/123"
    )

    # Test nested quote data
    quote_data = {
        "url": "https://nitter.example.com/quoted/status/456",
        "author": "quoteduser",
        "text": "This is a quoted tweet",
        "image_urls": [],
        "nested_quote": {
            "url": "https://nitter.example.com/nested/status/789",
            "author": "nesteduser",
            "text": "This is a nested quote",
            "image_urls": ["https://example.com/image1.jpg"]
        }
    }

    post.set_quote_data(quote_data)

    print(f"Quote tweet URL: {post.quote_tweet_url}")
    print(f"Quote author: {post.quote_author}")
    print(f"Quote text: {post.quote_text}")
    print(f"Quote data: {post.quote_data}")
    print()

    # Test serialization
    serialized = post.serialize_quote_data_for_db()
    print(f"Serialized for DB: {serialized[:100]}..." if len(serialized) > 100 else serialized)
    print()

    # Test deserialization
    deserialized = Post.deserialize_quote_data_from_db(serialized)
    print(f"Deserialized: {deserialized}")
    print()

def test_recursive_rendering():
    """Test recursive HTML rendering"""
    print("Testing recursive HTML rendering...")

    quote_data = {
        "url": "https://nitter.example.com/quoted/status/456",
        "author": "quoteduser",
        "text": "This is a quoted tweet with some content",
        "image_urls": [],
        "nested_quote": {
            "url": "https://nitter.example.com/nested/status/789",
            "author": "nesteduser",
            "text": "This is a nested quote within the quote",
            "image_urls": []
        }
    }

    html = render_quote_html_recursive(quote_data, 0)
    print("Generated HTML:")
    print(html[:500] + "..." if len(html) > 500 else html)
    print()

if __name__ == "__main__":
    test_url_extraction()
    test_post_quote_data()
    test_recursive_rendering()
    print("✅ All tests completed successfully!")