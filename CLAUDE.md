# LLM-Powered Newsletter Project

## Project Overview
Building a simple, intern-friendly LLM-powered newsletter system that pulls RSS from Nitter and sends curated email digests.

**Philosophy:** Keep everything modular, simple, and elegant. Start with MVP, layer in complexity gradually.

## What We're Building
A single Python script that:
1. Pulls RSS from a Nitter instance for a list of accounts
2. De-dupes and selects recent posts
3. (MVP) Emails them directly
4. (Upgrade) Uses LLM filtering with general + per-account prompts
5. Sends tidy HTML email

## Minimal File Structure (Simple by Design)
```
newsletter/
  .env                      # SMTP creds, NITTER_BASE_URL, etc.
  accounts.yaml             # list of @handles and per-account settings
  newsletter.db             # SQLite database (auto-created)
  images/                   # downloaded images (auto-created)
    2025-01-15/            # organized by date
      karpathy_123_1.jpg
      openai_456_1.png
  prompts/
    _general.md             # general system prompt
    <handle>.md             # per-account prompt (optional; else falls back)
  templates/
    email.html.j2           # (optional) nicer HTML; MVP can skip this
  main.py                   # single script; all logic lives here
```

Start with just `.env`, `accounts.yaml`, and `main.py`. The database and images folder are auto-created.

## Prerequisites
* Working **Nitter** instance (or stable public one)
* Python 3.10+ with: `pip install httpx feedparser jinja2 python-dotenv pydantic tenacity`
* SMTP credentials
* SQLite (built into Python, no extra install needed)

## Example Configuration Files

### .env
```
NITTER_BASE_URL=https://nitter.example.com
SMTP_HOST=smtp.fastmail.com
SMTP_PORT=587
SMTP_USER=me@example.com
SMTP_PASS=************************
MAIL_TO=me@example.com
MAIL_FROM=newsletter@example.com
TIMEZONE=America/Los_Angeles
```

### accounts.yaml
```yaml
accounts:
  - handle: karpathy
  - handle: openai
  - handle: soumith
window_hours: 24        # lookback window
max_per_account: 10     # MVP cap, optional
```

## Implementation Phases

### Phase 1 — MVP (No LLM)
**Goal:** Prove end-to-end fetching + email in simplest possible way.

**CLI Usage:**
```bash
python main.py --dry-run         # print to console only
python main.py --send            # actually email
python main.py --window 24       # override hours
```

### Phase 2 — Add LLM Filtering
Add `llm_filter()` function with:
- **System message** = contents of `_general.md`
- **User message** = per-account prompt + post content
- **Output contract** = JSON with `{id, include, reason}`
- **Batching** = up to 10 posts per account per call

**LLM Config:**
```
LLM_ENABLED=true
LLM_MODEL=gpt-4o-mini
LLM_MAX_BATCH=10
LLM_INCLUDE_THRESHOLD=0.5
```

### Phase 2.5 — Optional Vision Support
Only if needed:
- Fetch tweet's Nitter status page
- Parse image URLs
- Pass to vision-capable model
- Keep `VISION_ENABLED=false` by default

### Phase 3 — Email Polish
- Add `templates/email.html.j2` for prettier layout
- Sections: "Top Picks" + "By Account"
- Include Nitter + fallback x.com links
- Track basic metrics in `state.json`

### Phase 4 — Scheduling & Ops
- **Cron example:**
  ```
  # Weekdays at 8:10am PT
  10 8 * * 1-5 /usr/bin/env -S bash -lc 'cd ~/newsletter && . .venv/bin/activate && python main.py --send'
  ```
- **GitHub Actions** alternative for CI execution

## Sample Prompts

### prompts/_general.md (system)
```
You select tweets for my personal newsletter.
Keep only items that help me stay sharp on: applied ML for productivity, LLM agents/evals, devtools/observability, edge/on-device models.
Prefer practical insights, releases, benchmarks, deep dives.
Exclude memes, giveaways, drama, pure hype, job postings.
Decision rule: include only if useful to a busy senior engineer. Output only strict JSON as instructed.
```

### prompts/karpathy.md
```
I mainly want research insights, learning resources, and practical advice from Karpathy.
Prefer threads and posts with links to posts, videos, or code.
```

## Safety & Etiquette
- Sleep 300–800ms between feeds
- Exponential backoff on errors
- Respect <150 requests/day total
- Keep Nitter instance private if possible

## Data Storage Design

### SQLite Database Schema
```sql
CREATE TABLE tweets (
  id TEXT PRIMARY KEY,
  handle TEXT,
  title TEXT,
  summary TEXT,
  published TIMESTAMP,
  nitter_url TEXT,
  x_url TEXT,
  image_urls TEXT,          -- JSON: original Nitter URLs
  image_paths TEXT,         -- JSON: local file paths
  first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  included_in_newsletter BOOLEAN DEFAULT FALSE,
  llm_reason TEXT
);
```

### Image Storage
- Images downloaded to `images/{date}/{handle}_{tweet_id}_{index}.{ext}`
- Organized by date for easy cleanup
- Original URLs preserved in database
- Local paths stored for fast access
- Graceful failure if download fails

### Image Download Logic
```python
def download_images(tweet_id, handle, image_urls):
    date_folder = datetime.now().strftime('%Y-%m-%d')
    images_dir = Path(f'images/{date_folder}')
    images_dir.mkdir(parents=True, exist_ok=True)
    
    local_paths = []
    for i, url in enumerate(image_urls):
        try:
            response = httpx.get(url, timeout=10)
            ext = get_image_extension(url, response.headers)
            filename = f"{handle}_{tweet_id}_{i+1}{ext}"
            filepath = images_dir / filename
            filepath.write_bytes(response.content)
            local_paths.append(str(filepath))
        except Exception as e:
            print(f"Failed to download {url}: {e}")
    return local_paths
```

**Benefits:**
- Complete tweet history preserved
- Images stored locally (survive Nitter outages)
- Easy querying: "Show me all karpathy tweets from last month"
- Atomic transactions prevent corruption
- Can export to CSV for analysis

## Development Notes
- **Done is better than complex**
- Start with MVP for 2-3 days → assess usefulness
- Turn on LLM filtering with gentle prompts
- Log include/skip reasons for prompt refinement
- Cap daily total (10-20 items)
- Add image understanding only if truly needed