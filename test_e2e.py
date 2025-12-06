#!/usr/bin/env python3
"""
End-to-end newsletter rendering tests using the private Nitter instance.
Fetches live statuses, renders HTML, and compares to a golden snapshot.
Use REGENERATE_GOLDEN=1 to refresh the snapshot after intentional changes.
"""

import os
import sys
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

sys.path.append('.')

from twitter import (
    Post,
    format_tweet_body_html,
    render_email,
    fetch_quoted_tweet_content_recursive,
)

SNAPSHOT_PATH = "fixtures/e2e_email_snapshot.html"
DEFAULT_BASE_URL = os.getenv("NITTER_BASE_URL", "http://10.8.0.1:8080")


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def pass_(self, name):
        self.passed += 1
        print(f"✅ PASS: {name}")

    def fail(self, name, detail=""):
        self.failed += 1
        msg = f"{name}{': ' + detail if detail else ''}"
        self.failures.append(msg)
        print(f"❌ FAIL: {msg}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\nTotal: {total}, Passed: {self.passed}, Failed: {self.failed}")
        if self.failed:
            print("Failures:")
            for f in self.failures:
                print(f"  • {f}")
        return self.failed == 0


def normalize_quote_media(quote_data: dict):
    if not quote_data:
        return
    if quote_data.get("video_attachments"):
        for va in quote_data["video_attachments"]:
            if "thumbnail_url" in va and "thumbnail_server_url" not in va:
                va["thumbnail_server_url"] = va["thumbnail_url"]
    if quote_data.get("nested_quote"):
        normalize_quote_media(quote_data["nested_quote"])


def fetch_status_minimal(status_url: str, base_url: str) -> Post:
    resp = httpx.get(status_url, timeout=20.0)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, 'html.parser')

    main = soup.select_one('.main-tweet')
    if not main:
        raise ValueError(f"No main tweet found at {status_url}")

    content_elem = main.select_one('.tweet-content')
    raw_html = str(content_elem) if content_elem else ""
    text = format_tweet_body_html(raw_html)

    image_urls = []
    for img in main.select('.attachments .still-image img'):
        src = img.get('src')
        if src:
            abs_src = src if src.startswith('http') else base_url.rstrip('/') + src
            image_urls.append(abs_src)

    video_attachments = []
    for img in main.select('.attachments .gallery-video img'):
        src = img.get('src')
        if not src:
            continue
        thumb = src if src.startswith('http') else base_url.rstrip('/') + src
        video_attachments.append({
            "thumbnail_url": thumb,
            "target_url": status_url
        })

    published = datetime.now(timezone.utc)
    ts = main.select_one('.tweet-header .tweet-date a')
    if ts and ts.get('title'):
        try:
            published = datetime.strptime(ts.get('title'), '%b %d, %Y · %I:%M %p %Z')
            published = published.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    try:
        handle = status_url.split('/')[3]
    except Exception:
        handle = 'unknown'

    post = Post(
        id=status_url,
        handle=handle,
        title=text[:40],
        summary=text,
        published=published,
        nitter_url=status_url,
        image_urls=image_urls,
        video_attachments=video_attachments,
        raw_description=raw_html,
    )
    post.server_image_urls = post.image_urls
    for va in post.video_attachments:
        va["thumbnail_server_url"] = va.get("thumbnail_url")

    post.set_x_url({"nitter": {"base_url": base_url}})

    quote_link = main.select_one('.quote a.quote-link, .quote a[href*="status/"]')
    if quote_link and quote_link.get('href'):
        quote_url = quote_link.get('href')
        if not quote_url.startswith('http'):
            quote_url = base_url.rstrip('/') + quote_url
        quote_data = fetch_quoted_tweet_content_recursive(
            quote_url,
            {"nitter": {"base_url": base_url}},
            max_depth=2,
            logger=None
        )
        post.set_quote_data(quote_data)
        normalize_quote_media(quote_data)

    return post


def run_e2e():
    result = TestResult()
    base_url = DEFAULT_BASE_URL
    targets = [
        # Standard tweet
        f"{base_url}/teortaxesTex/status/1997323428324327465#m",
        # Quote with video thumb
        f"{base_url}/teortaxesTex/status/1997321377951392074#m",
        # Mention without quote
        f"{base_url}/teortaxesTex/status/1997319384298091003#m",
    ]

    posts = []
    for url in targets:
        try:
            posts.append(fetch_status_minimal(url, base_url))
            result.pass_(f"Fetched {url}")
        except Exception as e:
            result.fail(f"Fetch failed for {url}", str(e))

    if not posts:
        return result.summary()

    account_list = type("DummyList", (), {"accounts": ["teortaxesTex"], "name": "teortaxesTex", "get_email_subject": lambda self=None: "Test"})()
    html_email = render_email(posts, account_list, author_pfps={}, timezone_str="UTC")[1]

    regen = os.getenv("REGENERATE_GOLDEN") == "1"
    if regen or not os.path.exists(SNAPSHOT_PATH):
        with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            f.write(html_email)
        result.pass_(f"Updated golden snapshot at {SNAPSHOT_PATH}")
    else:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            golden = f.read()
        if html_email == golden:
            result.pass_("Email rendering matches golden snapshot")
        else:
            result.fail("Email render does not match golden snapshot", "Run with REGENERATE_GOLDEN=1 after intentional changes")

    # Spot-check regressions
    outer_text = posts[1].summary if len(posts) > 1 else ""
    if "are you really comparing yourself" not in outer_text:
        result.pass_("Quote text not in outer body after sanitize")
    else:
        result.fail("Quote text leaked into outer body")

    html_lower = html_email.lower()
    quote_handle = ""
    if len(posts) > 1 and posts[1].quote_data and posts[1].quote_data.get("author"):
        quote_handle = f"@{posts[1].quote_data['author']}".lower()
    if quote_handle and quote_handle in html_lower:
        result.pass_("Quote author rendered as handle")
    elif quote_handle:
        result.fail("Quote author handle missing", quote_handle)

    if "▶ video" in html_lower:
        result.pass_("Video overlay present")
    else:
        result.fail("Video overlay missing")

    return result.summary()


if __name__ == "__main__":
    ok = run_e2e()
    sys.exit(0 if ok else 1)
