"""
AGENT 1 — News Ticker Agent (fixed version)
"""

import json
import feedparser
import anthropic
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from shared.config import ANTHROPIC_API_KEY
from shared.ghost_api import update_site_metadata

RSS_FEEDS = [
    {"name": "Economic Times Markets",  "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
    {"name": "Economic Times Economy",  "url": "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms"},
    {"name": "Business Standard",       "url": "https://www.business-standard.com/rss/markets-106.rss"},
    {"name": "Mint Markets",            "url": "https://www.livemint.com/rss/markets"},
    {"name": "Hindu BusinessLine",      "url": "https://www.thehindubusinessline.com/markets/feeder/default.rss"},
    {"name": "CNBC World Markets",      "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
    {"name": "CNBC US Economy",         "url": "https://www.cnbc.com/id/20910289/device/rss/rss.html"},
    {"name": "Yahoo Finance",           "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "Reuters Business",        "url": "https://feeds.reuters.com/reuters/businessNews"},
]

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

def fetch_headlines():
    headlines = []
    for feed in RSS_FEEDS:
        try:
            feedparser.USER_AGENT = UA
            parsed = feedparser.parse(feed["url"], request_headers={"User-Agent": UA})
            count = 0
            for entry in parsed.entries[:6]:
                title = entry.get("title", "").strip()
                url   = entry.get("link", "")
                if title and url:
                    headlines.append({"source": feed["name"], "title": title, "url": url})
                    count += 1
            print(f"  {'OK' if count > 0 else '--'} {feed['name']}: {count}")
        except Exception as e:
            print(f"  ERR {feed['name']}: {e}")
    print(f"\n  Total: {len(headlines)} headlines")
    return headlines

def classify_headlines(headlines):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    headlines_text = "\n".join([f"{i+1}. [{h['source']}] {h['title']}" for i, h in enumerate(headlines)])
    prompt = f"""You are a senior NIFTY 50 analyst. Classify each headline's impact on NIFTY 50.

Headlines:
{headlines_text}

Respond ONLY with a JSON array, no markdown, no explanation.
Format: [{{"index": 1, "sentiment": "positive"}}, {{"index": 2, "sentiment": "negative"}}, ...]
Sentiment must be one of: positive, negative, neutral"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.strip().strip("```json").strip("```").strip()
    classifications = json.loads(raw)
    sentiment_map = {item["index"]: item["sentiment"] for item in classifications}
    for i, h in enumerate(headlines):
        h["sentiment"] = sentiment_map.get(i + 1, "neutral")
    pos = sum(1 for h in headlines if h["sentiment"] == "positive")
    neg = sum(1 for h in headlines if h["sentiment"] == "negative")
    neu = sum(1 for h in headlines if h["sentiment"] == "neutral")
    print(f"  Classified: {pos} positive, {neu} neutral, {neg} negative")
    return headlines

def build_ticker_html(headlines):
    colours = {"positive": "#4ade80", "neutral": "#e2e8f0", "negative": "#f87171"}
    sep = '<span style="color:rgba(255,255,255,0.2);margin:0 20px">·</span>'
    items = ""
    for h in headlines:
        c = colours.get(h["sentiment"], "#e2e8f0")
        items += f'<a href="{h["url"]}" target="_blank" rel="noopener" style="color:{c};text-decoration:none;white-space:nowrap;" title="{h["source"]}">{h["title"]}</a>{sep}'
    items = items * 2
    ts = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    return (f'<div id="img-ticker-container" style="background:#1a1a1a;padding:7px 0;overflow:hidden;'
            f'white-space:nowrap;font-family:monospace;font-size:10.5px;">'
            f'<span style="display:inline-block;animation:img-scroll-ticker 60s linear infinite;padding-left:100%;">'
            f'{items}</span></div>'
            f'<style>@keyframes img-scroll-ticker{{0%{{transform:translateX(0)}}100%{{transform:translateX(-50%)}}}}</style>'
            f'<!-- updated {ts} -->')

def push_to_ghost(headlines, ticker_html):
    import os, json as json_mod
    from shared.ghost_api import update_gist
    from shared.config import GIST_TICKER_ID

    data = {"updated_at": datetime.now(timezone.utc).isoformat(), "headlines": headlines}

    # Save locally for Agent 2
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared', 'latest_news.json')
    with open(local_path, 'w') as f:
        json_mod.dump(data, f)

    # Push to GitHub Gist — website reads from here
    update_gist(GIST_TICKER_ID, "ticker.json", data)
    print(f"  Pushed {len(headlines)} headlines to GitHub Gist")

def run():
    print(f"\n{'='*50}")
    print(f"AGENT 1 — News Ticker  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
    print(f"{'='*50}")
    print("\n[1/4] Fetching headlines...")
    headlines = fetch_headlines()
    if not headlines:
        print("No headlines fetched. Check internet connection.")
        return
    print(f"\n[2/4] Classifying {len(headlines)} headlines...")
    classified = classify_headlines(headlines)
    print("\n[3/4] Building ticker HTML...")
    ticker_html = build_ticker_html(classified)
    print("  Done")
    print("\n[4/4] Pushing to Ghost...")
    push_to_ghost(classified, ticker_html)
    print(f"\nAgent 1 complete.\n")

if __name__ == "__main__":
    run()