"""
AGENT 4 — Content Writer Agent  (v2 — data-grounded)
================================
Post schedule (IST):
  Mon-Fri  08:00  → Daily Market Brief
  Saturday 09:00  → Weekly Wrap
  Sunday   09:00  → Editorial (global trends + trading education)
Data sources:
  Nifty OHLCV + movers  → Supabase
  FII/DII flows         → NSE API
  India VIX             → NSE API (structured, reliable)   [v2]
  Global cues           → JSON-validated web search (omittable) [v2]
  News headlines        → Agent 1 GitHub Gist

v2 changes (content integrity):
  • India VIX now comes straight from NSE (level + % change), not a fuzzy
    web-search blob — fixes the inverted "VIX spiked" errors.
  • Global cues (US indices / crude / GIFT / USDINR) come from a web search
    that must return STRICT JSON; we parse + validate and pass only the fields
    that came back. Anything missing is omitted, never guessed.
  • Prompts rewritten: use ONLY the DATA BLOCK; never invent or recall a
    market figure; use the provided change exactly (no low-to-close "gains");
    explain causes ONLY from the provided news headlines; if a data point is
    absent, OMIT the section — never write "unavailable" / "N/A" / guess.
  • Removed the harmful fallback strings (incl. "use web search context").
"""
import json
import re
import requests
import anthropic
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.newsletter import send_newsletter
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone, timedelta
from shared.config import (
    ANTHROPIC_API_KEY, GHOST_URL,
    GIST_TICKER_URL, SUPABASE_URL, SUPABASE_KEY
)
from shared.ghost_api import ghost_headers

# NSE session headers (shared by FII/DII + VIX fetchers)
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

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
        close_url = (
            f"{SUPABASE_URL}/rest/v1/ohlcv_data"
            f"?datetime=gte.{date_str}T15:14:00+05:30"
            f"&datetime=lte.{date_str}T10:05:00%2B00:00"
            f"&symbol=neq.NIFTY 50"
            f"&order=symbol.asc"
        )
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
# ── STEP 2: NSE FII/DII + INDIA VIX ───────────────────────────────
def get_fii_dii_data():
    """Fetch FII/DII trade data from NSE (cookie two-step)."""
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        res = session.get(
            "https://www.nseindia.com/api/fiidiiTradeReact",
            headers=NSE_HEADERS, timeout=10
        )
        res.raise_for_status()
        data = res.json()
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
            print(f"  FII net: ₹{result.get('fii_net',0):,.2f} Cr | "
                  f"DII net: ₹{result.get('dii_net',0):,.2f} Cr")
            return result
        return None
    except Exception as e:
        print(f"  NSE FII/DII fetch failed: {e}")
        return None
def get_india_vix():
    """India VIX level + % change, straight from NSE (structured, reliable).
    At 8 AM pre-open NSE returns the previous session's close + its day change."""
    try:
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        r = s.get("https://www.nseindia.com/api/allIndices", headers=NSE_HEADERS, timeout=10)
        for idx in r.json().get("data", []):
            if idx.get("index", "").upper().replace(" ", "") == "INDIAVIX":
                last = float(idx.get("last"))
                pct  = idx.get("percentChange")
                pct  = round(float(pct), 2) if pct not in (None, "") else None
                print(f"  India VIX: {last} ({pct}%)")
                return {"last": round(last, 2), "pct": pct}
    except Exception as e:
        print(f"  VIX fetch error: {e}")
    return None
def format_fii_dii(data):
    """Format FII/DII for the data block. Returns '' if unavailable (so it's
    simply omitted — never a placeholder, never an instruction to fabricate)."""
    if not data:
        return ""
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
def get_global_cues_json():
    """US indices / Asia / crude / GIFT / USDINR via a JSON-ONLY web search.
    We parse + validate; only fields that come back are used, the rest omitted.
    This is more reliable than a free-text blob, but still web-sourced — for
    truly hard data we'd wire a market-data API (VIX is already NSE-direct)."""
    today = ist_now().strftime("%Y-%m-%d")
    query = (
        "Search the web for the most recent available values as of " + today + ". "
        "Return ONLY a single JSON object — no prose, no markdown fences. "
        "Use this exact schema and set any value you cannot verify to null:\n"
        '{"asof":"YYYY-MM-DD",'
        '"dow":{"level":number,"pct":number},'
        '"sp500":{"level":number,"pct":number},'
        '"nasdaq":{"level":number,"pct":number},'
        '"nikkei":{"level":number,"pct":number},'
        '"hangseng":{"level":number,"pct":number},'
        '"brent":number,"wti":number,"gift_nifty":number,"usdinr":number}\n'
        "These must be the latest ACTUAL last/closing values. Do not guess — use null."
    )
    raw = web_search(query)
    if not raw:
        return None
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        print("  Global cues: no JSON found in web result — omitting global section")
        return None
    try:
        return json.loads(m.group(0))
    except Exception as e:
        print(f"  Global cues parse error ({e}) — omitting global section")
        return None
def build_global_block(vix, cues):
    """Clean, labelled global block from ONLY validated fields. Missing → omitted."""
    lines = []
    def idx_line(label, d):
        if isinstance(d, dict) and d.get("level") is not None:
            pct = d.get("pct")
            tail = f" ({pct:+.2f}%)" if isinstance(pct, (int, float)) else ""
            lines.append(f"{label}: {d['level']}{tail}")
    if isinstance(cues, dict):
        idx_line("Dow Jones",  cues.get("dow"))
        idx_line("S&P 500",    cues.get("sp500"))
        idx_line("NASDAQ",     cues.get("nasdaq"))
        idx_line("Nikkei 225", cues.get("nikkei"))
        idx_line("Hang Seng",  cues.get("hangseng"))
        for key, label in [("brent", "Brent crude (USD)"), ("wti", "WTI crude (USD)"),
                           ("gift_nifty", "GIFT Nifty"), ("usdinr", "USD/INR")]:
            v = cues.get(key)
            if isinstance(v, (int, float)):
                lines.append(f"{label}: {v}")
    if isinstance(vix, dict) and vix.get("last") is not None:
        pct = vix.get("pct")
        if isinstance(pct, (int, float)):
            direction = "FELL" if pct < 0 else ("ROSE" if pct > 0 else "flat")
            lines.append(f"India VIX: {vix['last']} ({pct:+.2f}%) — VIX {direction}")
        else:
            lines.append(f"India VIX: {vix['last']}")
    return "\n".join(lines)
def get_global_cues():
    """Assemble the global-cues block: NSE VIX (reliable) + validated web data."""
    print("  Fetching India VIX from NSE...")
    vix = get_india_vix()
    print("  Fetching global cues (JSON-validated web search)...")
    cues = get_global_cues_json()
    return build_global_block(vix, cues)
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
- Authoritative but accessible — a senior analyst explaining to a smart retail trader
- Specific and grounded — reference the actual numbers you are given
- Balanced — acknowledge both bull and bear cases honestly
- Actionable — end with clear takeaways retail traders can use today
- No fluff, no filler — every sentence must earn its place

DATA INTEGRITY (non-negotiable — violations make the post worthless):
- Use ONLY the numbers and facts in the DATA BLOCK of the prompt. Never recall,
  estimate, compute, or "remember" any market figure, index level, price, VIX
  value, or % change from general knowledge.
- Use the provided change figures EXACTLY. The day's change is previous-close-to-
  close. NEVER present an intraday low-to-close bounce as "the day's gain."
- Explain WHY the market moved ONLY using the provided news headlines. Do NOT
  assert any cause or event (strikes, ceasefire, war, policy decision, selloff,
  rally trigger) unless it appears in the provided headlines.
- If a data point or whole section is NOT in the DATA BLOCK, simply OMIT it.
  Never write "data unavailable," "not available," "N/A," "—", or a guess.
- The ONLY numbers you may derive yourself are support/resistance levels, which
  are your technical read of the PROVIDED OHLC — frame them clearly as "levels to
  watch," never as reported facts.

AUDIENCE:
- Indian retail traders/investors, age 25-45, trade Nifty 50 options or index funds
- Read ET/Mint, understand basic Greeks, skeptical of vague or invented numbers
FORMAT RULES:
- First line = Title (no # prefix, no quotes)
- Use ## for section headings, --- as section dividers
- **Bold** key numbers, levels, and critical phrases
- Flowing paragraphs, not bullet points
- End with a ## Actionable Takeaways section of 5 specific points
- Reading time: ~5 min daily, ~8 min weekly wrap, ~6 min editorial
"""
# ── STEP 5: GENERATE POSTS ────────────────────────────────────────
def generate_daily_brief(day_data, gainers, losers, fii_dii_str, global_block, headlines):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    n      = ist_now()
    today  = n.strftime("%A, %d %B %Y")
    pos_news = "; ".join(h["title"] for h in headlines if h.get("sentiment") == "positive")[:600]
    neg_news = "; ".join(h["title"] for h in headlines if h.get("sentiment") == "negative")[:600]

    # Build the DATA BLOCK from ONLY what we actually have.
    parts = [
        f"YESTERDAY'S NIFTY 50 SESSION ({day_data['date']}):\n"
        f"Direction: {day_data['direction']} | "
        f"Change (previous close to close): {day_data['change_pts']:+.2f} pts "
        f"({day_data['change_pct']:+.2f}%)\n"
        f"Open: {day_data['open']} | High: {day_data['high']} | "
        f"Low: {day_data['low']} | Close: {day_data['close']}\n"
        f"Previous Close: {day_data['prev_close']} | Day Range: {day_data['range']} pts\n"
        f"Close Location: {day_data['close_location_pct']}% of day range (100% = at high)"
    ]
    if gainers or losers:
        g = ", ".join(f"{x['symbol']} ({x['change_pct']:+.2f}%)" for x in gainers)
        l = ", ".join(f"{x['symbol']} ({x['change_pct']:+.2f}%)" for x in losers)
        parts.append(f"TOP MOVERS YESTERDAY:\nGainers: {g}\nLosers: {l}")
    if fii_dii_str:
        parts.append(f"FII/DII FLOWS (latest available):\n{fii_dii_str}")
    if global_block:
        parts.append(f"GLOBAL CUES (latest available):\n{global_block}")
    news_bits = []
    if pos_news:
        news_bits.append(f"Positive: {pos_news}")
    if neg_news:
        news_bits.append(f"Negative: {neg_news}")
    if news_bits:
        parts.append("NEWS HEADLINES (use ONLY these for narrative/causes):\n"
                     + "\n".join(news_bits))
    data_block = "\n\n".join(parts)

    prompt = f"""Write a daily pre-market analysis post for {today}.

=== DATA BLOCK — the ONLY facts you may state ===
{data_block}
=== END DATA BLOCK ===

Hard rules:
- Use ONLY numbers/facts from the DATA BLOCK. Do not add, recall, or estimate any
  market figure not shown above.
- State yesterday's change exactly as: {day_data['change_pts']:+.2f} pts ({day_data['change_pct']:+.2f}%).
  Do NOT recompute it or call the low-to-close move "the day's gain."
- Explain market causes ONLY from the NEWS HEADLINES above. If the headlines don't
  explain a move, describe the price action without inventing a reason.
- If a section's data is absent from the DATA BLOCK (global cues, movers, FII/DII),
  SKIP that section entirely. Never write "unavailable," "N/A," or guess.

Write ~700-800 words, including ONLY the sections you have data for:
1. Yesterday's session recap — what happened, key levels tested, what the close tells us
2. Global cues — ONLY if provided; cite the given levels and what they signal
3. FII/DII activity — ONLY if provided; who bought/sold and what it implies
4. Key levels for today — support/resistance you derive from the provided OHLC,
   framed as levels to watch
5. Today's bias — bull/bear/neutral grounded in the data + headlines, and what flips it
6. Actionable Takeaways — 5 specific numbered points built on the levels above

Title (first line, no prefix): Nifty 50 Pre-Market: [hook grounded in the actual data] — {today}
"""
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        system=STYLE_GUIDE,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()
def generate_weekly_wrap(week_data, gainers, losers, fii_dii_str, global_block, headlines):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    n      = ist_now()
    today  = n.strftime("%d %B %Y")
    all_news = "; ".join(h["title"] for h in headlines[:12])[:900]

    parts = [
        f"THIS WEEK'S NIFTY 50:\n"
        f"Week: {week_data['week_start']} to {week_data['week_end']}\n"
        f"Open: {week_data['open']} | High: {week_data['high']} | "
        f"Low: {week_data['low']} | Close: {week_data['close']}\n"
        f"Previous Week Close: {week_data['prev_close']} | "
        f"Weekly Change: {week_data['change_pct']:+.2f}%"
    ]
    if gainers or losers:
        g = ", ".join(f"{x['symbol']} ({x['change_pct']:+.2f}%)" for x in gainers)
        l = ", ".join(f"{x['symbol']} ({x['change_pct']:+.2f}%)" for x in losers)
        parts.append(f"TOP MOVERS THIS WEEK:\nGainers: {g}\nLosers: {l}")
    if fii_dii_str:
        parts.append(f"FII/DII FLOWS (latest available):\n{fii_dii_str}")
    if global_block:
        parts.append(f"GLOBAL MACRO (latest available):\n{global_block}")
    if all_news:
        parts.append(f"NEWS HEADLINES THIS WEEK (use ONLY these for causes):\n{all_news}")
    data_block = "\n\n".join(parts)

    prompt = f"""Write a weekly market wrap post for week ending {today}.

=== DATA BLOCK — the ONLY facts you may state ===
{data_block}
=== END DATA BLOCK ===

Hard rules:
- Use ONLY numbers/facts from the DATA BLOCK. Never recall or invent a market figure.
- Use the weekly change exactly as given ({week_data['change_pct']:+.2f}%).
- Explain causes ONLY from the provided headlines. If absent, describe price action only.
- Omit any section whose data isn't in the DATA BLOCK. Never write "unavailable"/"N/A".

Write ~1200 words, including ONLY the sections you have data for:
1. The week in numbers — open/high/low/close + weekly change with context
2. Day-by-day narrative — turning points the data and headlines support
3. Sectoral / stock performance — using the provided movers
4. FII vs DII — the institutional battle this week (only if provided)
5. Global macro — how world markets influenced India (only if provided)
6. News flow that mattered — which provided headlines moved the market
7. What to watch next week — key levels off the provided prices, events from headlines
8. Actionable Takeaways — 5 specific points for next week

Title (first line): Nifty 50 Weekly Wrap: [hook grounded in the data] | Week of {week_data['week_start']} to {week_data['week_end']}
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
RESEARCH (use only what is substantiated here; do not invent statistics):
Global Market Trends: {research['topic']}
Trading Education: {research['education']}
Write ~1000 words on ONE focused topic. Choose the most compelling angle from:
- A major shift in global markets and what it means for Indian retail traders
- A trading concept that separates consistently profitable traders from losing ones
- How institutional traders think vs retail — the edge you can learn
- A risk management lesson from a recent market event
- The psychology behind a specific trading mistake most retail traders make
Structure:
1. Hook — a story, stat, or provocative question (2-3 sentences)
2. The Core Insight — what most traders get wrong
3. Evidence — specific examples grounded in the research (no invented figures)
4. The Indian Context — how it applies to Nifty/Indian retail traders
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
def html_to_lexical(html_content):
    """Convert HTML to Ghost Lexical JSON format."""
    children = []
    for line in html_content.split('\n'):
        s = line.strip()
        if not s:
            continue
        if s.startswith('<h2>') and s.endswith('</h2>'):
            text = re.sub(r'<[^>]+>', '', s)
            children.append({
                "children": [{"detail": 0, "format": 0, "mode": "normal",
                               "style": "", "text": text, "type": "text", "version": 1}],
                "direction": "ltr", "format": "", "indent": 0,
                "type": "extended-heading", "version": 1, "tag": "h2"
            })
        elif s == '<hr>':
            children.append({"type": "horizontalrule", "version": 1})
        elif s.startswith('<p>') and s.endswith('</p>'):
            inner = s[3:-4]
            parts = re.split(r'(<strong>.*?</strong>)', inner)
            text_children = []
            for part in parts:
                if part.startswith('<strong>') and part.endswith('</strong>'):
                    text_children.append({
                        "detail": 0, "format": 1, "mode": "normal",
                        "style": "", "text": part[8:-9], "type": "text", "version": 1
                    })
                elif part:
                    text_children.append({
                        "detail": 0, "format": 0, "mode": "normal",
                        "style": "", "text": part, "type": "text", "version": 1
                    })
            if text_children:
                children.append({
                    "children": text_children,
                    "direction": "ltr", "format": "", "indent": 0,
                    "type": "paragraph", "version": 1
                })
    return json.dumps({
        "root": {
            "children": children,
            "direction": "ltr", "format": "", "indent": 0,
            "type": "root", "version": 1
        }
    })
def publish_to_ghost(title, html_content, tags):
    lexical = html_to_lexical(html_content)
    payload = {
        "posts": [{
            "title":        title,
            "lexical":      lexical,
            "status":       "published",
            "tags":         [{"name": t} for t in tags],
            "published_at": ist_now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }]
    }
    url = f"{GHOST_URL}/ghost/api/admin/posts/"
    res = requests.post(url, json=payload, headers=ghost_headers())
    res.raise_for_status()
    post = res.json()["posts"][0]
    print(f"  Published: \"{post['title']}\"")
    post_url = f"{GHOST_URL}/{post.get('slug', '')}"
    send_newsletter(post['title'], html_content, post_url, tags)
    return post
# ── MAIN ──────────────────────────────────────────────────────────
def run(trigger="scheduled"):
    n           = ist_now()
    dow         = n.weekday()
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
        global_block = get_global_cues()
        print("\n[5/5] Fetching news + generating post...")
        headlines = get_latest_news()
        content   = generate_weekly_wrap(week_data, gainers, losers, fii_dii_str, global_block, headlines)
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
        global_block = get_global_cues()
        print("\n[5/5] Fetching news + generating post...")
        headlines = get_latest_news()
        content   = generate_daily_brief(day_data, gainers, losers, fii_dii_str, global_block, headlines)
        title, html = content_to_html(content)
        publish_to_ghost(title, html, ["Daily Brief", "Market Analysis", "Nifty 50"])
    print(f"\n✓ Agent 4 complete.\n")
if __name__ == "__main__":
    trigger = sys.argv[1] if len(sys.argv) > 1 else "manual"
    run(trigger)
