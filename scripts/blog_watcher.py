#!/usr/bin/env python3
"""
Mike Dietrich Blog RSS Watcher — no_agent cron script.
Fetches blog RSS, detects new posts via cache file, outputs formatted summary.

Silent if no new articles. Outputs article summaries only when new content found.
"""
import feedparser
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CACHE_FILE = Path("/opt/hermes/.hermes/cron/9992054ce7dd/seen.json")
CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

# Load seen IDs
seen = set()
if CACHE_FILE.exists():
    try:
        data = json.loads(CACHE_FILE.read_text())
        seen = set(data.get("seen", []))
    except (json.JSONDecodeError, ValueError):
        seen = set()

feed = feedparser.parse("https://mikedietrichde.com/feed/")
new_articles = []

for entry in feed.entries[:10]:
    article_id = entry.get("id", entry.get("link", ""))
    if article_id in seen:
        continue
    new_articles.append(entry)
    seen.add(article_id)

# Save updated seen set
with open(CACHE_FILE, "w") as f:
    json.dump({"seen": list(seen), "updated": datetime.now(timezone.utc).isoformat()}, f)

if not new_articles:
    # Silent — no new articles
    sys.exit(0)

# Format output
lines = [f"📰 Mike Dietrich Blog — {len(new_articles)} new article(s)"]
lines.append("")

for article in new_articles[:5]:  # Max 5 per cron cycle
    title = article.get("title", "Untitled")
    link = article.get("link", "")
    published = article.get("published", "")
    summary = article.get("summary", "")
    
    # Clean summary (strip HTML tags for readability)
    import re
    summary_clean = re.sub(r"<[^>]+>", "", summary)[:300]
    
    lines.append(f"**{title}**")
    if published:
        lines.append(f"🕐 {published}")
    lines.append(f"{link}")
    if summary_clean:
        lines.append(f"> {summary_clean}")
    lines.append("")

if len(new_articles) > 5:
    lines.append(f"...and {len(new_articles) - 5} more")

lines.append("via Mike Dietrich Blog RSS")
print("\n".join(lines))
sys.exit(0)