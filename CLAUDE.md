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
python main.py --no-db           # skip database (for testing email only)
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

## Current Progress (as of 2025-09-04)

### Phase 1 - MVP Implementation ✅ COMPLETED
**Status:** Fully functional MVP newsletter system

**Implemented Features:**
- ✅ RSS feed fetching from Nitter instance (http://10.8.0.1:8080)
- ✅ SQLite database with comprehensive tweet storage
- ✅ Image downloading and local storage (organized by date: `images/YYYY-MM-DD/`)
- ✅ Profile picture handling for authors and retweet authors
- ✅ Rich HTML email generation with Twitter-like formatting
- ✅ Retweet detection and proper attribution
- ✅ Quote tweet handling with content extraction
- ✅ Reply detection
- ✅ Base64 image embedding in emails
- ✅ CLI interface with `--dry-run` and `--send` options
- ✅ Configurable time windows and post limits
- ✅ SMTP email delivery via Gmail

**Current Configuration:**
- **Accounts:** teortaxestex, zephyr_z9, spandrell4
- **Window:** 72 hours (3 days)
- **Max per account:** 250 posts
- **Nitter instance:** Local (10.8.0.1:8080)
- **Email:** Gmail SMTP

**File Structure (Current):**
```
kestral/
├── .env                    # SMTP + Nitter config
├── accounts.yaml           # Account list + settings
├── main.py                 # Entry point + platform dispatch
├── common_utils.py         # Shared utilities (database, email, logging)
├── twitter.py              # Twitter/Nitter logic with nested quote support
├── newsletter.db           # SQLite database (functional)
├── images/                 # Downloaded images
│   └── 2025-09-03/        # Daily organization
├── test_suite.py           # Automated test suite
├── .venv/                  # Python virtual environment
└── CLAUDE.md              # This file
```

**Database Schema:** Full implementation with 19 columns including metadata, URLs, image paths, tweet type detection, and LLM preparation fields.

**Next Steps:**
- Phase 2: Add LLM filtering with prompts/
- Phase 3: Email template improvements
- Phase 4: Scheduling and operational tools

## Testing

### Test Suite

The project includes an automated test suite in `test_suite.py` with clear pass/fail results:

**Current Test Coverage:**
- URL extraction from plain text
- Post class quote data handling
- Nested quote processing logic
- HTML rendering for multi-level quotes
- JSON serialization/deserialization

**Running Tests:**
```bash
# Activate virtual environment
source .venv/bin/activate

# Run test suite
python test_suite.py
```

**Test Output Format:**
- ✅ PASS/❌ FAIL for each test with clear error messages
- Final summary with total passed/failed counts
- Exit code 0 for success, 1 for failure (good for CI/CD)

**Integration Testing:**
```bash
# Test with live Nitter feeds (dry run)
python main.py --platform=twitter --to=test@example.com --dry-run
```

**Status:** Basic test coverage for nested quote functionality. Tests will expand as new features are added.

## Discord Integration Plan

### Overview
Expand the newsletter system to include Discord server summaries using a hierarchical LLM processing approach. Keep the same simple, single-file philosophy while adding Discord as a parallel data source.

### Core Design Principles
- **Separate emails:** Discord gets its own dedicated email (like individual Twitter accounts)
- **Volume handling:** Use hierarchical summarization for ~1000 messages/day
- **Incremental phases:** Start with data collection, add email later
- **Intern-friendly:** Mirror existing Twitter patterns and structure

### Database Schema Extensions

#### Discord Messages Table
```sql
CREATE TABLE discord_messages (
  id TEXT PRIMARY KEY,
  channel_id TEXT,
  channel_name TEXT,
  author_name TEXT,
  author_id TEXT,
  content TEXT,
  timestamp TIMESTAMP,
  message_type TEXT,  -- 'normal', 'reply', 'thread_start'
  thread_id TEXT,     -- for threading conversations
  attachments TEXT,   -- JSON array of attachment URLs
  reactions TEXT,     -- JSON for reaction counts
  first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  included_in_newsletter BOOLEAN DEFAULT FALSE,
  llm_reason TEXT
);
```

#### Discord Summaries Table
```sql
CREATE TABLE discord_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date DATE,
  channel_name TEXT,
  summary_type TEXT,  -- 'play_by_play', 'channel_summary', 'conversation'
  summary_text TEXT,
  message_ids TEXT,   -- JSON array of contributing message IDs
  block_number INTEGER, -- for play-by-play tracking
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Hierarchical Summary Algorithm

**Step 1: Streaming Play-by-Play Generation**
```
For each channel:
  1. Fetch all messages from past 24h, ordered chronologically
  2. Process in blocks of 40 messages (with 5 message overlap)
  3. For each block:
     - Input: Current play-by-play summary + next 40 messages
     - Output: Updated play-by-play summary
     - Save to discord_summaries as type='play_by_play'
  4. Repeat until all messages processed
```

**Step 2: Channel Summary Generation**
```
For each channel:
  1. Take final play-by-play summary from Step 1
  2. LLM prompt: "Convert this play-by-play into a 5-minute summary"
  3. Save as type='channel_summary'
```

**Benefits of This Approach:**
- Handles arbitrarily large message volumes
- Maintains context across long conversations
- Preserves important details while reducing noise
- Scalable processing (can pause/resume)

### Configuration Additions

#### accounts.yaml Extension
```yaml
# Existing Twitter config unchanged...

discord:
  enabled: true
  server_id: "123456789"
  bot_token_env: "DISCORD_BOT_TOKEN"
  channels:
    - "general"
    - "development" 
    - "random"
    # Or use "all" to include all channels
  window_hours: 24
  processing:
    block_size: 40
    overlap_size: 5
    min_messages_for_summary: 10  # Skip quiet channels
```

#### Environment Variables
```bash
# Add to .env
DISCORD_BOT_TOKEN=your_bot_token_here
DISCORD_ENABLED=true
```

### Implementation Phases

#### Phase 1: Data Collection (No Email) 🎯 NEXT
**Goal:** Establish Discord message fetching and storage pipeline

**Tasks:**
- Add Discord bot setup with `discord.py`
- Implement message fetching across channels
- Store messages in `discord_messages` table
- Add `--discord-only` CLI flag for testing
- Test with `python main.py --discord-only --dry-run`

**Success Criteria:**
- Messages from past 24h stored in database
- Basic message metadata captured (author, channel, timestamps)
- No crashes on large message volumes

#### Phase 2: Hierarchical Summarization
**Goal:** Implement streaming play-by-play and channel summaries

**Tasks:**
- Add LLM integration for hierarchical processing
- Implement block-based processing with overlap
- Generate play-by-play summaries
- Convert play-by-play to final channel summaries
- Store all summaries in `discord_summaries` table

**Success Criteria:**
- Each active channel gets a coherent summary
- Processing handles 1000+ messages without memory issues
- Summaries capture key themes and decisions

#### Phase 3: Email Integration
**Goal:** Generate and send Discord newsletter

**Tasks:**
- Create Discord-specific email template
- Format channel summaries for email
- Add interesting individual messages section
- Implement separate Discord email sending
- Add Discord subject line generation

**Email Structure:**
```
📧 Discord Server Newsletter - 2025-09-07

Channel Summaries:
├── #general: [5-minute summary]
├── #development: [5-minute summary]
└── #random: [5-minute summary]

Highlighted Messages:
├── Message 1 with high engagement
├── Technical insight from #development
└── Important announcement

Generated by Newsletter System
```

#### Phase 4: Polish & Operations
**Goal:** Production readiness and operational improvements

**Tasks:**
- Add reaction/engagement metrics to message filtering
- Implement conversation thread detection
- Add Discord-specific prompts for different channel types
- Add rate limiting and error handling for Discord API
- Integration with existing cron scheduling

### Modular Architecture

**File Structure:**
```
kestral/
├── main.py              # Entry point + platform dispatch
├── common_utils.py      # Shared utilities (high bar for inclusion)
├── twitter.py           # All Twitter/Nitter logic
├── discord.py           # All Discord logic (future)
├── accounts.yaml        # Platform configurations
├── .env                 # Shared environment variables
├── newsletter.db        # Shared database (both platforms)
├── images/              # Downloaded images (Twitter + Discord)
├── .venv/               # Python virtual environment
└── CLAUDE.md           # This file
```

**Module Responsibilities:**

**main.py** (Entry point only):
- CLI argument parsing (`--platform=twitter|discord`)
- Platform dispatch (route to twitter.main() or discord.main())
- No business logic, just coordination

**common_utils.py** (Truly shared utilities only):
- Database initialization (shared schema)
- Email sending function (generic SMTP)
- Image utilities (download, base64, server upload)
- Configuration loading (.env, yaml)
- High bar: only add if genuinely platform-agnostic

**twitter.py** (All Twitter-specific logic):
- Post class and Twitter data models
- RSS feed fetching and parsing
- Tweet classification (retweets, quotes, replies)
- Profile picture and image handling
- Quote tweet content extraction
- Twitter email rendering
- Twitter database operations

**discord.py** (All Discord-specific logic - future):
- Discord bot connection and message fetching
- Hierarchical summarization pipeline
- Discord message models and processing
- Discord email rendering
- Discord database operations

### CLI Usage Examples
```bash
# Activate virtual environment first
source .venv/bin/activate

# Twitter newsletter (current functionality)
python main.py --platform=twitter --dry-run
python main.py --platform=twitter --send

# Discord newsletter (future)
python main.py --platform=discord --dry-run
python main.py --platform=discord --send

# Override window for any platform
python main.py --platform=twitter --send --window=48
```

### Cron Job Separation
```bash
# Twitter: Every day at 8:00 AM
0 8 * * * cd ~/kestral && source .venv/bin/activate && python main.py --platform=twitter --send

# Discord: Every day at 8:30 AM (fault isolation)
30 8 * * * cd ~/kestral && source .venv/bin/activate && python main.py --platform=discord --send
```

### Benefits of Modular Architecture
✅ **Fault isolation**: Nitter outage doesn't break Discord newsletter  
✅ **Independent scaling**: Different schedules per platform  
✅ **Clean separation**: Single responsibility per file  
✅ **Easy testing**: Test platforms independently  
✅ **Future-proof**: Easy to add new platforms  
✅ **No circular dependencies**: Clean import hierarchy

### Dependencies to Add
```bash
pip install discord.py>=2.0
# Existing: httpx feedparser jinja2 python-dotenv pydantic tenacity
```

### Success Metrics
- **Phase 1:** Successfully collect 1000+ messages/day
- **Phase 2:** Generate coherent summaries for 5+ channels
- **Phase 3:** Send readable Discord newsletter email
- **Phase 4:** Zero-maintenance daily operation

This approach maintains the project's core philosophy of simplicity while handling Discord's scale through smart hierarchical processing.