"""
AGENT 4 — Content Writer Agent
================================
Post schedule (IST):
  Mon-Fri  08:00  → Daily Market Brief
  Saturday 09:00  → Weekly Wrap
  Sunday   09:00  → Editorial (global trends + trading education)

Data sources:
  Nifty OHLCV + movers  → Supabase
  FII/DII flows         → NSE API
  Global cues           → Web search via Claude
  News headlines        → Agent 1 GitHub Gist
  Editorial research    → Web search via Claude
"""

import json
import re
import requests
import anthropic
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.newsletter import send_newsletter
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone, timedelta
from shared.config import (
    ANTHROPIC_API_KEY, GHOST_URL,
    GIST_TICKER_URL, SUPABASE_URL, SUPABASE_KEY
)
from shared.ghost_api import ghost_headers


# ── SUPABASE HELPERS ──────────────────────────────────────────────

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }


def ist_now():
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def last_trading_day(ref=None):
    """Always return the most recent completed trading day."""
    # Agent runs at 8AM IST — always use previous completed trading day
    d = (ref or ist_now()).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ── STEP 1: SUPABASE DATA ─────────────────────────────────────────

def get_day_data(date_str):
    """Fetch OHLCV summary for NIFTY 50 for a given date."""
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/ohlcv_data"
            f"?symbol=eq.NIFTY%2050"
            f"&datetime=gte.{date_str}T03:30:00%2B00:00"
            f"&datetime=lte.{date_str}T10:05:00%2B00:00"
            f"&order=datetime.asc"
        )
        resp = requests.get(url, headers=sb_headers())
        bars = resp.json()
        if not isinstance(bars, list) or not bars:
            print(f"  Supabase returned: {resp.text[:200]}")
            return None

        # Previous close
        prev_url = (
            f"{SUPABASE_URL}/rest/v1/ohlcv_data"
            f"?symbol=eq.NIFTY%2050"
            f"&datetime=lt.{date_str}T03:30:00%2B00:00"
            f"&order=datetime.desc&limit=1"
        )
        prev = requests.get(prev_url, headers=sb_headers()).json()
        prev_close = prev[0]["close"] if prev else bars[0]["open"]

        o = bars[0]["open"]
        h = max(b["high"]  for b in bars)
        l = min(b["low"]   for b in bars)
        c = bars[-1]["close"]
        chg_pts = round(c - prev_close, 2)
        chg_pct = round((chg_pts / prev_close) * 100, 2)
        rng     = round(h - l, 2)
        close_loc = round(((c - l) / (h - l)) * 100, 1) if h != l else 50

        return {
            "date": date_str, "open": o, "high": h, "low": l,
            "close": c, "prev_close": prev_close,
            "change_pts": chg_pts, "change_pct": chg_pct,
            "range": rng, "close_location_pct": close_loc,
            "direction": "BULL" if c > o else "BEAR"
        }
    except Exception as e:
        print(f"  Day data error: {e}")
        return None


def get_week_data():
    """Fetch weekly summary for current week."""
    try:
        n = ist_now()
        # Monday of this week
        monday = (n - timedelta(days=n.weekday())).date()
        friday = monday + timedelta(days=4)

        url = (
            f"{SUPABASE_URL}/rest/v1/ohlcv_data"
            f"?symbol=eq.NIFTY%2050"
            f"&datetime=gte.{monday}T03:30:00%2B00:00"
            f"&datetime=lte.{friday}T10:05:00%2B00:00"
            f"&order=datetime.asc"
        )
        resp = requests.get(url, headers=sb_headers())
        bars = resp.json()
        if not isinstance(bars, list) or not bars:
            print(f"  Supabase returned: {resp.text[:200]}")
            return None

        # Previous week close
        prev_url = (
            f"{SUPABASE_URL}/rest/v1/ohlcv_data"
            f"?symbol=eq.NIFTY%2050"
            f"&datetime=lt.{monday}T03:30:00%2B00:00"
            f"&order=datetime.desc&limit=1"
        )
        prev = requests.get(prev_url, headers=sb_headers()).json()
        prev_close = prev[0]["close"] if prev else bars[0]["open"]

        o = bars[0]["open"]
        h = max(b["high"]  for b in bars)
        l = min(b["low"]   for b in bars)
        c = bars[-1]["close"]
        chg_pct = round(((c - prev_close) / prev_close) * 100, 2)

        return {
            "week_start": str(monday), "week_end": str(friday),
            "open": o, "high": h, "low": l, "close": c,
            "prev_close": prev_close, "change_pct": chg_pct
        }
    except Exception as e:
        print(f"  Week data error: {e}")
        return None


def get_top_movers(date_str):
    """Top 5 gainers and losers from Supabase for a given date."""
    try:
        # Closing bars
        close_url = (
            f"{SUPABASE_URL}/rest/v1/ohlcv_data"
            f"?datetime=gte.{date_str}T15:14:00+05:30"
            f"&datetime=lte.{date_str}T10:05:00%2B00:00"
            f"&symbol=neq.NIFTY 50"
            f"&order=symbol.asc"
        )
        # Opening bars
        open_url = (
            f"{SUPABASE_URL}/rest/v1/ohlcv_data"
            f"?datetime=gte.{date_str}T09:14:00+05:30"
            f"&datetime=lte.{date_str}T04:05:00%2B00:00"
            f"&symbol=neq.NIFTY 50"
            f"&order=symbol.asc"
        )
        close_resp = requests.get(close_url, headers=sb_headers()).json()
        open_resp  = requests.get(open_url,  headers=sb_headers()).json()
        close_bars = close_resp if isinstance(close_resp, list) else []
        open_bars  = open_resp  if isinstance(open_resp,  list) else []

        open_map = {b["symbol"]: b["open"] for b in open_bars}
        movers = []
        for b in close_bars:
            sym = b["symbol"]
            op  = open_map.get(sym)
            if op and op > 0:
                chg = round(((b["close"] - op) / op) * 100, 2)
                movers.append({"symbol": sym, "change_pct": chg, "close": b["close"]})

        movers.sort(key=lambda x: x["change_pct"], reverse=True)
        return movers[:5], movers[-5:][::-1]
    except Exception as e:
        print(f"  Movers error: {e}")
        return [], []


# ── STEP 2: NSE FII/DII API ───────────────────────────────────────

def get_fii_dii_data():
    """
    Fetch FII/DII trade data from NSE.
    NSE requires session cookies — we do a two-step request.
    """
    try:
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
        }
        # Step 1: get cookies
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        # Step 2: fetch FII/DII data
        res = session.get(
            "https://www.nseindia.com/api/fiidiiTradeReact",
            headers=headers,
            timeout=10
        )
        res.raise_for_status()
        data = res.json()

        # Parse the response — NSE returns array of objects
        result = {}
        for item in data:
            cat = item.get("category", "").strip()
            if "FII" in cat or "FPI" in cat:
                result["fii_net"]  = float(str(item.get("netValue",  0)).replace(",","") or 0)
                result["fii_buy"]  = float(str(item.get("buyValue",  0)).replace(",","") or 0)
                result["fii_sell"] = float(str(item.get("sellValue", 0)).replace(",","") or 0)
            elif "DII" in cat:
                result["dii_net"]  = float(str(item.get("netValue",  0)).replace(",","") or 0)
                result["dii_buy"]  = float(str(item.get("buyValue",  0)).replace(",","") or 0)
                result["dii_sell"] = float(str(item.get("sellValue", 0)).replace(",","") or 0)

        if result:
            fii_net = result.get("fii_net", 0)
            dii_net = result.get("dii_net", 0)
            print(f"  FII net: ₹{fii_net:,.2f} Cr | DII net: ₹{dii_net:,.2f} Cr")
            return result
        return None

    except Exception as e:
        print(f"  NSE FII/DII fetch failed: {e}")
        return None


def format_fii_dii(data):
    """Format FII/DII data for prompt."""
    if not data:
        return "FII/DII data unavailable — use web search context"
    fii_net = data.get("fii_net", 0)
    dii_net = data.get("dii_net", 0)
    fii_dir = "NET BUYERS" if fii_net > 0 else "NET SELLERS"
    dii_dir = "NET BUYERS" if dii_net > 0 else "NET SELLERS"
    return (
        f"FII/FPI: {fii_dir} ₹{abs(fii_net):,.2f} Cr "
        f"(Buy: ₹{data.get('fii_buy',0):,.2f} Cr | Sell: ₹{data.get('fii_sell',0):,.2f} Cr)\n"
        f"DII: {dii_dir} ₹{abs(dii_net):,.2f} Cr "
        f"(Buy: ₹{data.get('dii_buy',0):,.2f} Cr | Sell: ₹{data.get('dii_sell',0):,.2f} Cr)"
    )


# ── STEP 3: NEWS FROM AGENT 1 GIST ───────────────────────────────

def get_latest_news():
    try:
        res = requests.get(GIST_TICKER_URL, timeout=10)
        data = res.json()
        headlines = data.get("headlines", [])
        print(f"  Got {len(headlines)} headlines from Agent 1")
        return headlines
    except Exception as e:
        print(f"  News Gist error: {e}")
        return []


# ── STEP 4: WEB SEARCH VIA CLAUDE ────────────────────────────────

def web_search(query):
    """Use Claude's web search tool to fetch current data."""
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": query}]
        )
        return " ".join(
            block.text for block in response.content
            if hasattr(block, "text") and block.text
        ).strip()
    except Exception as e:
        print(f"  Web search error: {e}")
        return ""


def get_global_cues():
    print("  Searching global cues...")
    return web_search(
        "Latest Dow Jones S&P 500 NASDAQ closing prices today. "
        "Asian markets Nikkei Hang Seng. GIFT Nifty SGX Nifty. "
        "Crude oil WTI Brent price. USD INR rupee rate. India VIX. "
        "Give me specific numbers."
    )


def get_editorial_research():
    print("  Searching editorial topics...")
    topic = web_search(
        "Most important development in global trading markets this week 2026. "
        "New trends retail traders should know. Key lesson from recent market events."
    )
    education = web_search(
        "Best risk management trading psychology lessons for retail traders India 2026"
    )
    return {"topic": topic, "education": education}


# ── STYLE GUIDE ───────────────────────────────────────────────────

STYLE_GUIDE = """
You are the lead analyst and writer for Indian Market Guru (indianmarketguru.com).

VOICE & TONE:
- Authoritative but accessible — like a senior analyst explaining to a smart retail trader
- Data-driven — always cite actual numbers, never be vague
- Balanced — acknowledge both bull and bear cases honestly
- Actionable — end with clear takeaways retail traders can use today
- No fluff, no filler — every sentence must earn its place

AUDIENCE:
- Indian retail traders/investors, age 25-45
- Trades Nifty 50 options or invests in index funds
- Reads ET/Mint, understands basic Greeks, skeptical of vague advice
- Will stop reading if content feels generic or numbers are missing

FORMAT RULES:
- First line = Title (no # prefix, no quotes)
- Use ## for section headings
- Use --- as section dividers
- **Bold** key numbers, levels, and critical phrases
- Write in flowing paragraphs — not bullet points
- End every post with ## Actionable Takeaways section with 5 specific points
- Never make up numbers — use only data provided
- Reading time: ~5 min daily, ~8 min weekly wrap, ~6 min editorial
"""


# ── STEP 5: GENERATE POSTS ────────────────────────────────────────

def generate_daily_brief(day_data, gainers, losers, fii_dii_str, global_cues, headlines):
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    n       = ist_now()
    today   = n.strftime("%A, %d %B %Y")

    pos_news = "; ".join(h["title"] for h in headlines if h.get("sentiment") == "positive")[:500]
    neg_news = "; ".join(h["title"] for h in headlines if h.get("sentiment") == "negative")[:500]
    gainers_str = ", ".join(f"{g['symbol']} ({g['change_pct']:+.2f}%)" for g in gainers) or "unavailable"
    losers_str  = ", ".join(f"{l['symbol']} ({l['change_pct']:+.2f}%)" for l in losers)  or "unavailable"

    prompt = f"""Write a daily pre-market analysis post for {today}.

YESTERDAY'S NIFTY 50 SESSION ({day_data['date']}):
Direction: {day_data['direction']} | Change: {day_data['change_pts']:+.2f} pts ({day_data['change_pct']:+.2f}%)
Open: {day_data['open']} | High: {day_data['high']} | Low: {day_data['low']} | Close: {day_data['close']}
Previous Close: {day_data['prev_close']} | Day Range: {day_data['range']} pts
Close Location: {day_data['close_location_pct']}% of day range (100% = closed at high)

TOP MOVERS YESTERDAY:
Gainers: {gainers_str}
Losers: {losers_str}

FII/DII FLOWS (latest):
{fii_dii_str}

GLOBAL CUES:
{global_cues}

NEWS FLOW:
Positive: {pos_news}
Negative: {neg_news}

Write ~800 words covering:
1. Yesterday's session recap — what happened, key levels tested, what the close tells us
2. Overnight global cues — specific index levels and what they signal for today
3. FII/DII activity — who's buying, who's selling, what it means
4. Key levels for today — specific support and resistance with reasoning
5. Today's bias — bull/bear/neutral and what would change it
6. Actionable Takeaways — 5 specific, numbered points for retail traders today

Title (first line, no prefix): Nifty 50 Pre-Market: [compelling hook] — {today}
"""
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        system=STYLE_GUIDE,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


def generate_weekly_wrap(week_data, gainers, losers, fii_dii_str, global_cues, headlines):
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    n       = ist_now()
    today   = n.strftime("%d %B %Y")

    all_news = "; ".join(h["title"] for h in headlines[:12])[:800]
    gainers_str = ", ".join(f"{g['symbol']} ({g['change_pct']:+.2f}%)" for g in gainers) or "unavailable"
    losers_str  = ", ".join(f"{l['symbol']} ({l['change_pct']:+.2f}%)" for l in losers)  or "unavailable"

    prompt = f"""Write a weekly market wrap post for week ending {today}.

THIS WEEK'S NIFTY 50:
Week: {week_data['week_start']} to {week_data['week_end']}
Open: {week_data['open']} | High: {week_data['high']} | Low: {week_data['low']} | Close: {week_data['close']}
Previous Week Close: {week_data['prev_close']}
Weekly Change: {week_data['change_pct']:+.2f}%

TOP MOVERS THIS WEEK:
Gainers: {gainers_str}
Losers: {losers_str}

FII/DII FLOWS THIS WEEK:
{fii_dii_str}

GLOBAL MACRO:
{global_cues}

NEWS FLOW THIS WEEK:
{all_news}

Write ~1200 words covering:
1. The week in numbers — open, high, low, close, weekly change with context
2. Day-by-day narrative — key turning points, what drove each session
3. Sectoral performance — winners and laggards with specific stock moves
4. FII vs DII — the institutional battle this week and who won
5. Global macro — how world markets influenced India this week
6. News flow that mattered — which headlines moved the market
7. What to watch next week — key levels, events, macro triggers
8. Actionable Takeaways — 5 specific points for next week

Title (first line): Nifty 50 Weekly Wrap: [compelling hook] | Week of {week_data['week_start']} to {week_data['week_end']}
"""
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=3000,
        system=STYLE_GUIDE,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


def generate_editorial(research):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    n      = ist_now()
    today  = n.strftime("%d %B %Y")

    prompt = f"""Write a weekly editorial post for Indian Market Guru dated {today}.

RESEARCH:
Global Market Trends: {research['topic']}
Trading Education: {research['education']}

Write ~1000 words on ONE focused topic. Choose the most compelling angle from:
- A major shift happening in global markets and what it means for Indian retail traders
- A trading concept that separates consistently profitable traders from losing ones
- How institutional traders think vs retail — the edge you can learn
- A risk management lesson from a recent market event
- The psychology behind a specific trading mistake most retail traders make

Structure:
1. Hook — open with a story, stat, or provocative question (2-3 sentences)
2. The Core Insight — what most traders get wrong about this topic
3. Evidence — specific examples, data, real market situations
4. The Indian Context — how this applies specifically to Nifty/Indian retail traders
5. The Framework — a simple mental model or rule to apply
6. Actionable Takeaways — 5 specific things to do differently

Tone: NYT op-ed meets experienced market professional. Confident, specific, no fluff.

Title (first line): The Trader's Edge: [compelling title] | {today}
"""
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2500,
        system=STYLE_GUIDE,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


# ── STEP 6: PUBLISH TO GHOST ──────────────────────────────────────

def content_to_html(content):
    """Convert markdown-style content to HTML for Ghost."""
    lines  = content.strip().split('\n')
    title  = lines[0].strip().strip('"')
    body   = '\n'.join(lines[1:]).strip()

    html_parts = []
    for line in body.split('\n'):
        s = line.strip()
        if not s:
            continue
        elif s.startswith('## '):
            html_parts.append(f"<h2>{s[3:]}</h2>")
        elif s == '---':
            html_parts.append("<hr>")
        else:
            s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
            html_parts.append(f"<p>{s}</p>")

    return title, '\n'.join(html_parts)


def publish_to_ghost(title, html_content, tags):
    payload = {
        "posts": [{
            "title":        title,
            "html":         html_content,
            "status":       "published",
            "tags":         [{"name": t} for t in tags],
            "published_at": ist_now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }]
    }
    url = f"{GHOST_URL}/ghost/api/admin/posts/?source=html"
    res = requests.post(url, json=payload, headers=ghost_headers())
    res.raise_for_status()
    post = res.json()["posts"][0]
    print(f"  ✓ Published: \"{post['title']}\"")
    post_url = f"{GHOST_URL}/{post.get('slug', '')}"
    send_newsletter(post['title'], html_content, post_url, tags)
    return post


# ── MAIN ──────────────────────────────────────────────────────────

def run(trigger="scheduled"):
    n           = ist_now()
    dow         = n.weekday()   # 0=Mon … 4=Fri, 5=Sat, 6=Sun
    day_name    = n.strftime("%A")

    print(f"\n{'='*55}")
    print(f"AGENT 4 — Content Writer  [{n.strftime('%Y-%m-%d %H:%M')} IST]")
    print(f"Day: {day_name} | Trigger: {trigger}")
    print(f"{'='*55}")

    # ── SATURDAY: Weekly Wrap ─────────────────────────────────────
    if dow == 5:
        print("\nSaturday → Weekly Wrap")

        print("\n[1/5] Fetching week data from Supabase...")
        week_data = get_week_data()
        if not week_data:
            print("  ✗ No week data. Aborting.")
            return

        print("\n[2/5] Fetching top movers...")
        friday = (n - timedelta(days=1)).date()
        gainers, losers = get_top_movers(str(friday))

        print("\n[3/5] Fetching FII/DII from NSE...")
        fii_dii_str = format_fii_dii(get_fii_dii_data())

        print("\n[4/5] Fetching global cues...")
        global_cues = get_global_cues()

        print("\n[5/5] Fetching news + generating post...")
        headlines = get_latest_news()
        content   = generate_weekly_wrap(week_data, gainers, losers, fii_dii_str, global_cues, headlines)
        title, html = content_to_html(content)
        publish_to_ghost(title, html, ["Weekly Wrap", "Market Analysis", "Nifty 50"])

    # ── SUNDAY: Editorial ─────────────────────────────────────────
    elif dow == 6:
        print("\nSunday → Editorial")

        print("\n[1/2] Researching editorial topic...")
        research = get_editorial_research()

        print("\n[2/2] Generating and publishing editorial...")
        content  = generate_editorial(research)
        title, html = content_to_html(content)
        publish_to_ghost(title, html, ["Editorial", "Trading Education", "Market Analysis"])

    # ── MON-FRI: Daily Brief ──────────────────────────────────────
    elif dow <= 4:
        print(f"\n{day_name} → Daily Market Brief")

        print("\n[1/5] Fetching yesterday's data from Supabase...")
        yesterday_date = str(last_trading_day())
        day_data = get_day_data(yesterday_date)
        if not day_data:
            print("  ✗ No data found. Aborting.")
            return

        print("\n[2/5] Fetching top movers...")
        gainers, losers = get_top_movers(yesterday_date)

        print("\n[3/5] Fetching FII/DII from NSE...")
        fii_dii_str = format_fii_dii(get_fii_dii_data())

        print("\n[4/5] Fetching global cues...")
        global_cues = get_global_cues()

        print("\n[5/5] Fetching news + generating post...")
        headlines = get_latest_news()
        content   = generate_daily_brief(day_data, gainers, losers, fii_dii_str, global_cues, headlines)
        title, html = content_to_html(content)
        publish_to_ghost(title, html, ["Daily Brief", "Market Analysis", "Nifty 50"])

    print(f"\n✓ Agent 4 complete.\n")


if __name__ == "__main__":
    trigger = sys.argv[1] if len(sys.argv) > 1 else "manual"
    run(trigger)
