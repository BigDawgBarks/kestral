#!/usr/bin/env python3
"""
Test suite for the newsletter system.
Run with: python test_suite.py
"""

import sys
import json
import traceback
from datetime import datetime, timezone

sys.path.append('.')

from twitter import Post, extract_quote_tweet_url_from_text, render_quote_html_recursive, format_tweet_body_html

class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def assert_equal(self, actual, expected, test_name):
        """Assert that actual equals expected"""
        if actual == expected:
            self.passed += 1
            print(f"✅ PASS: {test_name}")
            return True
        else:
            self.failed += 1
            self.failures.append(f"{test_name}: expected {expected!r}, got {actual!r}")
            print(f"❌ FAIL: {test_name}")
            print(f"   Expected: {expected!r}")
            print(f"   Actual:   {actual!r}")
            return False

    def assert_not_none(self, value, test_name):
        """Assert that value is not None"""
        if value is not None:
            self.passed += 1
            print(f"✅ PASS: {test_name}")
            return True
        else:
            self.failed += 1
            self.failures.append(f"{test_name}: expected non-None value, got None")
            print(f"❌ FAIL: {test_name}")
            print(f"   Expected: non-None value")
            print(f"   Actual:   None")
            return False

    def assert_none(self, value, test_name):
        """Assert that value is None"""
        if value is None:
            self.passed += 1
            print(f"✅ PASS: {test_name}")
            return True
        else:
            self.failed += 1
            self.failures.append(f"{test_name}: expected None, got {value!r}")
            print(f"❌ FAIL: {test_name}")
            print(f"   Expected: None")
            print(f"   Actual:   {value!r}")
            return False

    def assert_contains(self, container, item, test_name):
        """Assert that container contains item"""
        if item in container:
            self.passed += 1
            print(f"✅ PASS: {test_name}")
            return True
        else:
            self.failed += 1
            self.failures.append(f"{test_name}: {item!r} not found in {container!r}")
            print(f"❌ FAIL: {test_name}")
            print(f"   Expected: {item!r} to be in container")
            print(f"   Container: {container!r}")
            return False

    def assert_true(self, condition, test_name):
        """Assert that condition is True"""
        if condition is True:
            self.passed += 1
            print(f"✅ PASS: {test_name}")
            return True
        else:
            self.failed += 1
            self.failures.append(f"{test_name}: expected True, got {condition!r}")
            print(f"❌ FAIL: {test_name}")
            print(f"   Expected: True")
            print(f"   Actual:   {condition!r}")
            return False

    def summary(self):
        """Print test summary"""
        total = self.passed + self.failed
        print(f"\n{'='*50}")
        print(f"TEST SUMMARY")
        print(f"{'='*50}")
        print(f"Total tests: {total}")
        print(f"Passed: {self.passed}")
        print(f"Failed: {self.failed}")

        if self.failed > 0:
            print(f"\nFAILED TESTS:")
            for failure in self.failures:
                print(f"  • {failure}")
            print(f"\n❌ OVERALL: FAILED ({self.failed}/{total} tests failed)")
            return False
        else:
            print(f"\n✅ OVERALL: ALL TESTS PASSED")
            return True


def test_nested_quote_functionality():
    """Test nested quote tweet functionality"""
    print("="*50)
    print("TESTING: Nested Quote Tweet Functionality")
    print("="*50)

    result = TestResult()

    # Test 1: URL extraction from text
    print("\n--- URL Extraction Tests ---")

    # Test with Nitter URL
    text1 = "This is a tweet with a quote https://nitter.example.com/user/status/123456 in it"
    url1 = extract_quote_tweet_url_from_text(text1)
    result.assert_equal(url1, "https://nitter.example.com/user/status/123456", "Extract Nitter URL from text")

    # Test with X.com URL
    text2 = "Another tweet with https://x.com/someone/status/789012 here"
    url2 = extract_quote_tweet_url_from_text(text2)
    result.assert_equal(url2, "https://x.com/someone/status/789012", "Extract X.com URL from text")

    # Test with no URL
    text3 = "Just a regular tweet with no quotes"
    url3 = extract_quote_tweet_url_from_text(text3)
    result.assert_none(url3, "No URL extraction from plain text")

    # Test with multiple URLs (should get the first one)
    text4 = "Tweet with https://x.com/first/status/111 and https://x.com/second/status/222"
    url4 = extract_quote_tweet_url_from_text(text4)
    result.assert_equal(url4, "https://x.com/first/status/111", "Extract first URL when multiple present")

    # Test 2: Post class quote data handling
    print("\n--- Post Class Quote Data Tests ---")

    post = Post(
        id="test_123",
        handle="testuser",
        title="Test Tweet",
        summary="This is a test tweet",
        published=datetime.now(timezone.utc),
        nitter_url="https://nitter.example.com/testuser/status/123"
    )

    # Test initial state
    result.assert_none(post.quote_data, "Initial quote_data is None")

    # Test setting nested quote data
    quote_data = {
        "url": "https://nitter.example.com/quoted/status/456",
        "author": "quoteduser",
        "text": "This is a quoted tweet",
        "image_urls": ["https://example.com/image1.jpg"],
        "nested_quote": {
            "url": "https://nitter.example.com/nested/status/789",
            "author": "nesteduser",
            "text": "This is a nested quote",
            "image_urls": ["https://example.com/nested_image.jpg"]
        }
    }

    post.set_quote_data(quote_data)

    # Test legacy fields are populated correctly
    result.assert_equal(post.quote_tweet_url, "https://nitter.example.com/quoted/status/456", "Quote URL populated from quote_data")
    result.assert_equal(post.quote_author, "quoteduser", "Quote author populated from quote_data")
    result.assert_equal(post.quote_text, "This is a quoted tweet", "Quote text populated from quote_data")
    result.assert_equal(post.quote_image_urls, ["https://example.com/image1.jpg"], "Quote image URLs populated from quote_data")

    # Test quote_data is preserved
    result.assert_equal(post.quote_data, quote_data, "Quote data is preserved intact")

    # Test 3: Serialization and deserialization
    print("\n--- Serialization Tests ---")

    # Test serialization
    serialized = post.serialize_quote_data_for_db()
    result.assert_true(serialized.startswith('{'), "Serialized data starts with JSON object marker")

    # Test deserialization
    deserialized = Post.deserialize_quote_data_from_db(serialized)
    result.assert_equal(deserialized, quote_data, "Deserialized data matches original")

    # Test legacy text fallback
    post_legacy = Post(
        id="legacy_123",
        handle="legacy_user",
        title="Legacy Tweet",
        summary="Legacy summary",
        published=datetime.now(timezone.utc),
        nitter_url="https://nitter.example.com/legacy/status/999"
    )
    post_legacy.quote_text = "Plain text quote"
    legacy_serialized = post_legacy.serialize_quote_data_for_db()
    result.assert_equal(legacy_serialized, "Plain text quote", "Legacy text serialization")

    # Test legacy deserialization (should return None for non-JSON)
    legacy_deserialized = Post.deserialize_quote_data_from_db("Plain text quote")
    result.assert_none(legacy_deserialized, "Legacy text deserialization returns None")

    # Test 4: HTML rendering
    print("\n--- HTML Rendering Tests ---")

    # Test recursive rendering produces valid HTML
    html = render_quote_html_recursive(quote_data, 0)
    result.assert_not_none(html, "HTML rendering produces output")
    result.assert_contains(html, "💬 @quoteduser", "Main quote author in HTML")
    result.assert_contains(html, "This is a quoted tweet", "Main quote text in HTML")
    result.assert_contains(html, "💬 @nesteduser", "Nested quote author in HTML")
    result.assert_contains(html, "This is a nested quote", "Nested quote text in HTML")
    result.assert_contains(html, "margin: 8px 0 8px 16px", "Nested quote has increased indentation")

    # Test depth styling
    result.assert_contains(html, "font-size: 14px", "Main quote has larger font size")
    result.assert_contains(html, "font-size: 13px", "Nested quote has smaller font size")

    # Test empty quote data
    empty_html = render_quote_html_recursive(None, 0)
    result.assert_equal(empty_html, "", "Empty quote data produces empty HTML")

    return result


def test_tweet_link_rendering():
    """Test that tweet bodies keep full URLs in rendered HTML."""
    print("="*50)
    print("TESTING: Tweet Link Rendering")
    print("="*50)

    result = TestResult()

    print("\n--- Link Expansion Tests ---")
    truncated_html = '<p>Check this <a href="https://www.example.com/foo/bar/baz">https://www.example.com/foo...</a></p>'
    formatted = format_tweet_body_html(truncated_html)
    result.assert_contains(formatted, 'href="https://www.example.com/foo/bar/baz"', "Full URL preserved in href")
    result.assert_contains(formatted, '>https://www.example.com/foo/bar/baz<', "Full URL shown in link text")
    result.assert_true('foo...' not in formatted and 'foo\u2026' not in formatted, "Ellipsis removed from link text")

    short_link_html = '<a href="https://www.example.com/full">https://t.co/xyz</a>'
    short_formatted = format_tweet_body_html(short_link_html)
    result.assert_contains(short_formatted, '>https://www.example.com/full<', "Short links expanded to destination URL")

    simple_text = 'Plain text without links'
    simple_formatted = format_tweet_body_html(simple_text)
    result.assert_equal(simple_formatted, simple_text, "Plain text remains unchanged")

    return result


def run_all_tests():
    """Run all test suites"""
    print("🧪 NEWSLETTER SYSTEM TEST SUITE")
    print("🧪 " + "="*47)

    all_results = []

    try:
        # Run nested quote functionality tests
        nested_result = test_nested_quote_functionality()
        all_results.append(nested_result)

        link_result = test_tweet_link_rendering()
        all_results.append(link_result)

    except Exception as e:
        print(f"\n❌ CRITICAL ERROR: Test suite crashed")
        print(f"Error: {e}")
        traceback.print_exc()
        return False

    # Overall summary
    total_passed = sum(r.passed for r in all_results)
    total_failed = sum(r.failed for r in all_results)
    total_tests = total_passed + total_failed

    print(f"\n🏁 FINAL SUMMARY")
    print(f"🏁 {'='*47}")
    print(f"Total test suites: {len(all_results)}")
    print(f"Total tests run: {total_tests}")
    print(f"Total passed: {total_passed}")
    print(f"Total failed: {total_failed}")

    if total_failed == 0:
        print(f"\n🎉 ALL TESTS PASSED! Newsletter system is working correctly.")
        return True
    else:
        print(f"\n💥 TESTS FAILED! {total_failed}/{total_tests} tests failed.")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
