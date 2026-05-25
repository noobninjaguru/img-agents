"""
AGENT 1 — News Ticker Agent
============================
Runs every 2 hours. Fetches RSS feeds from global and Indian
financial news sources, classifies each headline by its likely
impact on NIFTY (positive / neutral / negative), and pushes
a colour-coded scrolling ticker to the website.

Colour coding:
  Green  = positive NIFTY impact
  White  = neutral
  Red    = negative NIFTY impact

Each headline links to the source article in a new tab.
"""

import json
import feedparser
import anthropic
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from shared.config import ANTHROPIC_API_KEY, RSS_FEEDS
from shared.ghost_api import update_site_metadata


# ── STEP 1: FETCH RSS HEADLINES ──────────────────────────────────

def fetch_headlines():
    """Pull latest headlines from all RSS feeds."""
    headlines = []
    for feed in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed["url"])
            for entry in parsed.entries[:5]:  # top 5 per source
                headlines.append({
                    "source": feed["name"],
                    "title": entry.get("title", "").strip(),
                    "url": entry.get("link", ""),
                    "published": entry.get("published", "")
                })
        except Exception as e:
            print(f"  ⚠ Could not fetch {feed['name']}: {e}")
    print(f"  ✓ Fetched {len(headlines)} headlines from {len(RSS_FEEDS)} sources")
    return headlines


# ── STEP 2: CLASSIFY WITH CLAUDE ─────────────────────────────────

def classify_headlines(headlines):
    """
    Send headlines to Claude API.
    Claude classifies each as positive, neutral, or negative
    for NIFTY and returns a clean JSON list.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    headlines_text = "\n".join([
        f"{i+1}. [{h['source']}] {h['title']}"
        for i, h in enumerate(headlines)
    ])

    prompt = f"""You are a senior Indian equity market analyst specialising in NIFTY 50.

Below are {len(headlines)} news headlines from global and Indian financial sources.

For each headline, classify its likely impact on NIFTY 50 as:
- "positive" — likely to push NIFTY higher (bullish for Indian markets)
- "negative" — likely to push NIFTY lower (bearish for Indian markets)  
- "neutral"  — minimal or unclear impact on NIFTY

Consider: FII flows, global risk appetite, RBI/Fed policy, crude oil, rupee, 
domestic growth, earnings, geopolitics, and sector-specific impacts on NIFTY constituents.

Headlines:
{headlines_text}

Respond ONLY with a valid JSON array. No explanation, no markdown, no preamble.
Format: [{{"index": 1, "sentiment": "positive"}}, {{"index": 2, "sentiment": "negative"}}, ...]
"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    classifications = json.loads(raw)

    # Merge sentiment back into headlines
    sentiment_map = {item["index"]: item["sentiment"] for item in classifications}
    for i, headline in enumerate(headlines):
        headline["sentiment"] = sentiment_map.get(i + 1, "neutral")

    positive = sum(1 for h in headlines if h["sentiment"] == "positive")
    negative = sum(1 for h in headlines if h["sentiment"] == "negative")
    neutral  = sum(1 for h in headlines if h["sentiment"] == "neutral")
    print(f"  ✓ Classified: {positive} positive · {neutral} neutral · {negative} negative")

    return headlines


# ── STEP 3: BUILD TICKER HTML ─────────────────────────────────────

def build_ticker_html(headlines):
    """
    Build the scrolling ticker HTML that Ghost will inject into the page.
    Each item is colour-coded and links to the source article.
    """
    colour_map = {
        "positive": "#4ade80",  # green
        "neutral":  "#e2e8f0",  # white
        "negative": "#f87171",  # red
    }

    items_html = ""
    separator = '<span style="color:rgba(255,255,255,0.2);margin:0 20px">·</span>'

    for h in headlines:
        colour = colour_map.get(h["sentiment"], "#e2e8f0")
        source = h["source"]
        title  = h["title"]
        url    = h["url"]

        items_html += f'''<a href="{url}" target="_blank" rel="noopener noreferrer" 
            style="color:{colour};text-decoration:none;white-space:nowrap;"
            title="{source}">{title}</a>{separator}'''

    # Double the items so the scroll loops seamlessly
    items_html = items_html * 2

    updated_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    html = f"""
<div id="img-ticker-container" style="
    background:#1a1a1a;
    padding:7px 0;
    overflow:hidden;
    white-space:nowrap;
    border-bottom:0.5px solid rgba(255,255,255,0.08);
    font-family:'DM Mono',monospace;
    font-size:10.5px;
    letter-spacing:.03em;
">
  <span id="img-ticker-inner" style="
    display:inline-block;
    animation:img-scroll-ticker 60s linear infinite;
    padding-left:100%;
  ">{items_html}</span>
</div>

<style>
@keyframes img-scroll-ticker {{
  0%   {{ transform: translateX(0); }}
  100% {{ transform: translateX(-50%); }}
}}
#img-ticker-inner a:hover {{
  text-decoration: underline !important;
  opacity: 0.85;
}}
</style>

<!-- Agent 1 metadata: last updated {updated_at} -->
"""
    return html


# ── STEP 4: PUSH TO GHOST ────────────────────────────────────────

def push_to_ghost(headlines, ticker_html):
    """
    Save the ticker HTML and raw data to Ghost.
    The website reads this via a small JS fetch.
    """
    # Save the full classified data as JSON for Agent 2 to use
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "headlines": headlines
    }
    update_site_metadata("news-ticker", json.dumps(data))

    # Save the rendered HTML ticker
    update_site_metadata("ticker-html", ticker_html)

    print(f"  ✓ Pushed ticker to Ghost ({len(headlines)} headlines)")


# ── MAIN ─────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*50}")
    print(f"AGENT 1 — News Ticker  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
    print(f"{'='*50}")

    print("\n[1/4] Fetching headlines from RSS feeds...")
    headlines = fetch_headlines()

    if not headlines:
        print("  ✗ No headlines fetched. Aborting.")
        return

    print(f"\n[2/4] Classifying {len(headlines)} headlines with Claude...")
    classified = classify_headlines(headlines)

    print("\n[3/4] Building ticker HTML...")
    ticker_html = build_ticker_html(classified)

    print("\n[4/4] Pushing to Ghost...")
    push_to_ghost(classified, ticker_html)

    print(f"\n✓ Agent 1 complete. Next run in 2 hours.\n")


if __name__ == "__main__":
    run()
