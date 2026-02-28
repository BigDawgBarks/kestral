#!/usr/bin/env python3
"""
Quick test script for nested quote tweet functionality.
Run with: python test_nested_quotes.py
"""

import sys
sys.path.append('.')

from twitter import Post, extract_quote_tweet_url_from_text, render_quote_html_recursive
from datetime import datetime, timezone


def test_url_extraction():
    """Test URL extraction from text"""
    print("Testing URL extraction from text...")

    text1 = "This is a tweet with a quote https://nitter.example.com/user/status/123456 in it"
    url1 = extract_quote_tweet_url_from_text(text1)
    assert url1 == "https://nitter.example.com/user/status/123456", f"Expected Nitter URL, got {url1!r}"

    text2 = "Another tweet with https://x.com/someone/status/789012 here"
    url2 = extract_quote_tweet_url_from_text(text2)
    assert url2 == "https://x.com/someone/status/789012", f"Expected X.com URL, got {url2!r}"

    text3 = "Just a regular tweet with no quotes"
    url3 = extract_quote_tweet_url_from_text(text3)
    assert url3 is None, f"Expected None, got {url3!r}"

    print("  All URL extraction assertions passed.")


def test_post_quote_data():
    """Test Post class quote data handling"""
    print("Testing Post class quote data methods...")

    post = Post(
        id="test_123",
        handle="testuser",
        title="Test Tweet",
        summary="This is a test tweet",
        published=datetime.now(timezone.utc),
        nitter_url="https://nitter.example.com/testuser/status/123"
    )

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

    assert post.quote_tweet_url == "https://nitter.example.com/quoted/status/456", \
        f"Unexpected quote_tweet_url: {post.quote_tweet_url!r}"
    assert post.quote_author == "quoteduser", f"Unexpected quote_author: {post.quote_author!r}"
    assert post.quote_text == "This is a quoted tweet", f"Unexpected quote_text: {post.quote_text!r}"
    assert post.quote_data == quote_data, "quote_data not preserved"

    serialized = post.serialize_quote_data_for_db()
    assert serialized.startswith('{'), f"Serialized data should be JSON, got {serialized[:50]!r}"

    deserialized = Post.deserialize_quote_data_from_db(serialized)
    assert deserialized == quote_data, "Deserialized data doesn't match original"

    print("  All Post quote data assertions passed.")


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

    rendered_html = render_quote_html_recursive(quote_data, 0)
    assert rendered_html, "Rendered HTML should not be empty"
    assert "@quoteduser" in rendered_html, "Main quote author missing from HTML"
    assert "@nesteduser" in rendered_html, "Nested quote author missing from HTML"
    assert "This is a quoted tweet" in rendered_html, "Main quote text missing from HTML"
    assert "This is a nested quote" in rendered_html, "Nested quote text missing from HTML"

    print("  All recursive rendering assertions passed.")


if __name__ == "__main__":
    test_url_extraction()
    test_post_quote_data()
    test_recursive_rendering()
    print("\nAll tests passed!")
