"""
AGENT 5 — Morning Analysis Agent
==================================
Runs at 9:20 AM IST (03:50 UTC) every weekday.

Replicates morning_analysis.py entirely on Railway:
- Pulls historical OHLCV from Supabase (no local CSV needed)
- Fetches today's opening price directly from Zerodha (worker as fallback)
- Calculates all signals (gap, DOW bias, spillover, ORB, streak)
- Sends formatted report email via Resend
- Saves report to Supabase daily_reports table
"""

import os
import sys
import json
import requests
import pandas as pd
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta, timezone
from shared.send_email import send_email
from shared.config import SUPABASE_URL, SUPABASE_KEY, NOTIFY_EMAIL

# ── CONFIG ────────────────────────────────────────────────────────
NIFTY_WORKER_URL = "https://nifty-ticker.babi-naren.workers.dev"
API_KEY          = os.environ.get("ZERODHA_API_KEY")

DOW_BIAS = {
    0: ("Monday",    51.7, "neutral"),
    1: ("Tuesday",   35.5, "strongly bearish"),
    2: ("Wednesday", 53.0, "mildly bullish"),
    3: ("Thursday",  41.4, "mildly bearish"),
    4: ("Friday",    45.2, "mildly bearish"),
}

GAP_CATEGORIES = [
    ("Big UP",    0.5,           float("inf")),
    ("Small UP",  0.1,           0.5),
    ("Flat",     -0.1,           0.1),
    ("Small DN", -0.5,          -0.1),
    ("Big DN",   float("-inf"), -0.5),
]

W = 63  # report width


# ── HELPERS ───────────────────────────────────────────────────────

def ist_now():
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }


def categorize_gap(pct):
    for label, lo, hi in GAP_CATEGORIES:
        if lo <= pct < hi:
            return label
    return "Flat"


def ruler(char="═"):
    return char * W


def section_header(title):
    pad = "━" * max(0, W - len(title) - 7)
    return f"\n━━━  {title}  {pad}"


def row(label, value, width=20):
    return f"  {label:<{width}} {value}"


# ── STEP 1: FETCH HISTORICAL DATA FROM SUPABASE ───────────────────

def fetch_historical_bars():
    """Fetch last 30 days of daily NIFTY 50 bars from Supabase."""
    try:
        n = ist_now()
        from_date = (n - timedelta(days=45)).strftime("%Y-%m-%d")

        url = (
            f"{SUPABASE_URL}/rest/v1/ohlcv_data"
            f"?symbol=eq.NIFTY 50"
            f"&datetime=gte.{from_date}T03:30:00%2B00:00"
            f"&order=datetime.asc"
            f"&limit=2000"
        )
        res = requests.get(url, headers=sb_headers())
        bars = res.json()

        if not isinstance(bars, list) or not bars:
            print(f"  No bars returned from Supabase")
            return None

        df = pd.DataFrame(bars)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df["datetime"] = df["datetime"].dt.tz_convert("Asia/Kolkata")
        df = df.sort_values("datetime").reset_index(drop=True)
        print(f"  Fetched {len(df)} 15-min bars from Supabase")
        return df

    except Exception as e:
        print(f"  Supabase fetch error: {e}")
        return None


def build_daily_bars(df):
    """Aggregate 15-min bars to daily OHLC with ORB levels."""
    df = df.copy()
    df["_date"] = df["datetime"].dt.date

    agg = df.groupby("_date").agg(
        open     =("open",  "first"),
        high     =("high",  "max"),
        low      =("low",   "min"),
        close    =("close", "last"),
        orb_high =("high",  "first"),
        orb_low  =("low",   "first"),
    ).reset_index()

    agg["date"] = pd.to_datetime(agg["_date"])
    agg["dow"]  = agg["date"].dt.dayofweek
    agg.drop(columns=["_date"], inplace=True)
    return agg.reset_index(drop=True)


def compute_gap_day_stats(daily):
    """Compute historical bull% for each gap×day combination."""
    d = daily.copy()
    d["prev_close"] = d["close"].shift(1)
    d = d.dropna(subset=["prev_close"])
    d["gap_pct"]  = (d["open"] - d["prev_close"]) / d["prev_close"] * 100
    d["gap_cat"]  = d["gap_pct"].apply(categorize_gap)
    d["is_bull"]  = (d["close"] > d["open"]).astype(int)
    d["dow_name"] = d["date"].dt.day_name()

    stats = (
        d.groupby(["dow_name", "dow", "gap_cat"])
        .agg(count=("is_bull", "count"), bull_count=("is_bull", "sum"))
        .reset_index()
    )
    stats["bull_pct"] = (stats["bull_count"] / stats["count"] * 100).round(1)
    return stats


def compute_streak(past):
    """Count consecutive same-direction days."""
    if past.empty:
        return 0, True
    last_bull = bool(past.iloc[-1]["close"] > past.iloc[-1]["open"])
    streak = 1
    for i in range(len(past) - 2, -1, -1):
        row_bull = bool(past.iloc[i]["close"] > past.iloc[i]["open"])
        if row_bull == last_bull:
            streak += 1
        else:
            break
    return streak, last_bull


def round_levels_near(price, step=100, count=7):
    import math
    base = math.floor(price / step) * step
    half = count // 2
    return [base + step * (i - half) for i in range(count)]


# ── STEP 2: FETCH TODAY'S OPENING PRICE ───────────────────────────

def get_token_from_supabase():
    """Read today's Zerodha access token from Supabase settings
    (written by the Mac's 9:15 job — exactly how Agent 6 reads it)."""
    try:
        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/settings?key=eq.zerodha_access_token",
            headers=sb_headers(),
            timeout=10,
        )
        rows = res.json()
        if not rows:
            return None
        data = json.loads(rows[0]["value"])
        if data.get("date") != str(ist_now().date()):
            print(f"  Token in Supabase is from {data.get('date')}, not today")
            return None
        return data.get("token")
    except Exception as e:
        print(f"  Token fetch error: {e}")
        return None


def get_open_from_worker():
    """Fallback: read the live price from the Cloudflare worker."""
    try:
        res = requests.get(NIFTY_WORKER_URL, timeout=10)
        data = res.json()
        if data.get("market_open") and data.get("price"):
            price = float(data["price"])
            print(f"  Open via live worker (fallback): {price:,.2f}")
            return price
        print("  Worker has no live price yet")
    except Exception as e:
        print(f"  Worker fetch failed: {e}")
    return None


def get_today_open():
    """Today's Nifty open — pulled straight from Zerodha (the exact 09:15
    open), independent of when the live ticker warms up. Falls back to the
    Cloudflare worker only if the direct fetch is unavailable."""
    token = get_token_from_supabase()
    if token:
        try:
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=API_KEY)
            kite.set_access_token(token)
            ohlc = kite.ohlc(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["ohlc"]
            open_price = float(ohlc["open"])
            if open_price > 0:
                print(f"  Today's open (Zerodha): {open_price:,.2f}")
                return open_price
            print("  Zerodha returned open=0 — trying worker...")
        except Exception as e:
            print(f"  Zerodha open fetch failed ({e}) — trying worker...")
    else:
        print("  No valid token in Supabase — trying worker...")
    return get_open_from_worker()


# ── STEP 3: BUILD REPORT ──────────────────────────────────────────

def build_report(daily, gap_stats, today_open, today):
    today_dow = today.weekday()
    dow_name, dow_bull_pct, dow_bias_str = DOW_BIAS.get(today_dow, ("Unknown", 50.0, "unknown"))

    past = daily[daily["date"].dt.date < today]
    if past.empty:
        return "No historical data available."

    yest       = past.iloc[-1]
    yest_bull  = bool(yest["close"] > yest["open"])
    yest_ret   = (yest["close"] - yest["open"]) / yest["open"] * 100
    yest_range = yest["high"] - yest["low"]
    close_pct  = ((yest["close"] - yest["low"]) / yest_range * 100) if yest_range else 50
    close_loc  = "TOP" if close_pct > 66 else ("BOTTOM" if close_pct < 33 else "MIDDLE")
    streak, streak_bull = compute_streak(past)
    streak_dir = "UP" if streak_bull else "DOWN"

    # Gap
    if today_open is not None:
        gap_pts = today_open - yest["close"]
        gap_pct = gap_pts / yest["close"] * 100
        gap_cat = categorize_gap(gap_pct)
    else:
        gap_pts = gap_pct = None
        gap_cat = None

    # Gap × Day stats
    if gap_cat:
        gd = gap_stats[
            (gap_stats["dow_name"] == dow_name) &
            (gap_stats["gap_cat"]  == gap_cat)
        ]
        if not gd.empty:
            gd_bull_pct = gd.iloc[0]["bull_pct"]
            gd_count    = int(gd.iloc[0]["count"])
            if gd_bull_pct > 65:
                gd_signal = f"HIGH-CONVICTION BULL  ({gd_bull_pct}%)"
            elif gd_bull_pct < 35:
                gd_signal = f"HIGH-CONVICTION BEAR  ({gd_bull_pct}%)"
            else:
                gd_signal = f"No strong edge  ({gd_bull_pct}%)"
        else:
            gd_bull_pct, gd_count, gd_signal = None, 0, "Insufficient historical data"
    else:
        gd_bull_pct = gd_count = None
        gd_signal = "—  (no opening price yet)"

    # Spillover
    spillover = []
    prev_dow = int(yest["dow"])
    if prev_dow == 0 and yest_ret > 0.3:
        spillover.append("TUESDAY SHORT BIAS  (Monday closed up >0.3%)")
    if prev_dow == 1 and yest_ret < -0.3:
        spillover.append("WEDNESDAY LONG BIAS  (Tuesday closed down >0.3%)")
    if prev_dow == 4 and yest_ret < -0.5:
        spillover.append("MONDAY LONG BIAS  (Friday closed down >0.5%)")
    if streak >= 3 and not streak_bull:
        spillover.append("MILD RECOVERY BIAS  (3+ consecutive bear days)")

    # Key levels
    five_days     = past.tail(5)
    five_day_high = five_days["high"].max()
    five_day_low  = five_days["low"].min()
    ref_price     = today_open if today_open else float(yest["close"])
    round_lvls    = round_levels_near(ref_price, step=100, count=7)
    round_str     = "  /  ".join(f"{l:,.0f}" for l in round_lvls)

    # ORB
    orb_high  = float(yest["orb_high"])
    orb_low   = float(yest["orb_low"])
    orb_range = orb_high - orb_low
    orb_size  = "TIGHT" if orb_range < 50 else ("WIDE" if orb_range > 100 else "NORMAL")

    # Assemble report
    L = []
    L.append(ruler("═"))
    L.append(f"   NIFTY 50 MORNING ANALYSIS  —  {today.strftime('%A, %d %B %Y')}  09:20")
    L.append(ruler("═"))

    arrow  = "▲ BULL" if yest_bull else "▼ BEAR"
    sign_r = "+" if yest_ret >= 0 else ""
    L.append(section_header(f"1.  YESTERDAY'S SESSION  ({yest['date'].strftime('%A, %d %b')})"))
    L.append(row("Direction",      f"{arrow}  ({sign_r}{yest_ret:.2f}%)"))
    L.append(row("Close location", f"{close_loc}  ({close_pct:.0f}% of day range)"))
    L.append(row("Streak",         f"{streak} consecutive {streak_dir} days"))

    L.append(section_header("2.  TODAY'S GAP"))
    if today_open is not None:
        sign_g = "+" if gap_pts >= 0 else ""
        L.append(row("Today open",      f"{today_open:,.2f}"))
        L.append(row("Yesterday close", f"{yest['close']:,.2f}"))
        L.append(row("Gap",             f"{sign_g}{gap_pts:,.1f} pts  ({sign_g}{gap_pct:.2f}%)  →  {gap_cat}"))
    else:
        L.append(row("Today open",      "not yet available"))
        L.append(row("Gap",             "—"))

    L.append(section_header("3.  DAY-OF-WEEK BIAS"))
    L.append(row(dow_name, f"{dow_bull_pct}% bull days  —  {dow_bias_str}"))

    L.append(section_header("4.  GAP × DAY SIGNAL"))
    if gap_cat:
        L.append(row(f"{gap_cat} on {dow_name}", f"{gd_signal}  ({gd_count} occurrences)"))
    else:
        L.append(row("Gap × Day", gd_signal))

    L.append(section_header("5.  SPILLOVER EFFECTS"))
    if spillover:
        for sig in spillover:
            L.append(f"  ⚑  {sig}")
    else:
        L.append("  ✓  None triggered")

    L.append(section_header("6.  KEY LEVELS"))
    L.append(row("Yesterday High",  f"{yest['high']:,.2f}"))
    L.append(row("Yesterday Low",   f"{yest['low']:,.2f}"))
    L.append(row("Yesterday Close", f"{yest['close']:,.2f}"))
    L.append(row("5-Day High",      f"{five_day_high:,.2f}"))
    L.append(row("5-Day Low",       f"{five_day_low:,.2f}"))
    L.append(row("Round numbers",   round_str))

    L.append(section_header("7.  ORB LEVELS  (Yesterday's 9:15 bar)"))
    L.append(row("ORB High",  f"{orb_high:,.2f}"))
    L.append(row("ORB Low",   f"{orb_low:,.2f}"))
    L.append(row("ORB Range", f"{orb_range:.1f} pts  —  {orb_size}"))

    L.append(section_header("8.  FAILED-ORB TRAP"))
    L.append("  Watch for bars that poke outside the 9:15 range —")
    L.append("  if the NEXT bar closes back inside, enter in the")
    L.append("  OPPOSITE direction.  Historical win rate: 80–89%.")

    L.append("")
    L.append(ruler("═"))
    L.append("")

    return "\n".join(L)


# ── STEP 4: SAVE TO SUPABASE ──────────────────────────────────────

def save_to_supabase(report, report_date):
    try:
        url = f"{SUPABASE_URL}/rest/v1/daily_reports?on_conflict=report_date,report_type"
        res = requests.post(
            url,
            headers={**sb_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "report_date": str(report_date),
                "report_type": "premarket",
                "content":     report,
            }
        )
        if res.status_code in [200, 201, 204]:
            print(f"  Saved to Supabase daily_reports")
        else:
            print(f"  Supabase save: {res.status_code} {res.text[:100]}")
    except Exception as e:
        print(f"  Supabase save error: {e}")


# ── STEP 5: SEND EMAIL ────────────────────────────────────────────

def send_report_email(report, report_date):
    subject = f"NIFTY 50 Morning Analysis — {report_date.strftime('%d %B %Y')}"
    html = f"""
<div style="font-family:'Courier New',monospace;font-size:12px;line-height:1.6;
            background:#0a0a0a;color:#e2e8f0;padding:24px;max-width:700px;">
  <pre style="font-family:'Courier New',monospace;font-size:12px;
              white-space:pre-wrap;color:#e2e8f0;">{report}</pre>
</div>
"""
    send_email(to=NOTIFY_EMAIL, subject=subject, html=html)
    print(f"  Email sent to {NOTIFY_EMAIL}")


# ── MAIN ──────────────────────────────────────────────────────────

def run(trigger="scheduled"):
    n   = ist_now()
    dow = n.weekday()

    print(f"\n{'='*55}")
    print(f"AGENT 5 — Morning Analysis  [{n.strftime('%Y-%m-%d %H:%M')} IST]")
    print(f"Trigger: {trigger}")
    print(f"{'='*55}")

    if dow >= 5:
        print("  Weekend — no analysis today.")
        return

    today = n.date()

    print("\n[1/4] Fetching historical bars from Supabase...")
    df = fetch_historical_bars()
    if df is None:
        print("  ✗ Could not fetch data. Aborting.")
        return

    daily     = build_daily_bars(df)
    gap_stats = compute_gap_day_stats(daily)
    print(f"  Built {len(daily)} daily bars")

    print("\n[2/4] Fetching today's opening price...")
    today_open = get_today_open()

    print("\n[3/4] Building report...")
    report = build_report(daily, gap_stats, today_open, today)
    print(report)

    print("\n[4/4] Saving and sending...")
    save_to_supabase(report, today)
    send_report_email(report, today)

    print(f"\n✓ Agent 5 complete.\n")


if __name__ == "__main__":
    trigger = sys.argv[1] if len(sys.argv) > 1 else "manual"
    run(trigger)
