"""
AGENT 8 — Daily Data Updater  (Railway)
=======================================
Runs once after the NSE close — 4:00 PM IST (10:30 UTC), weekdays.
Downloads today's 15-minute bars for the Nifty 50 index + all 50
constituents from Zerodha and upserts them into Supabase `ohlcv_data`.

This is the cloud replacement for the Mac's daily_update.py. It does NOT
log in to Zerodha. It borrows the day's access token that the Mac's token
job already writes to Supabase at 9:15 (the same token Agent 6 uses for the
live price) — so no Zerodha password or TOTP ever lives on Railway.

Credentials — all already on Railway, nothing new to add:
  - ZERODHA_API_KEY   : env var (already used by Agent 6)
  - SUPABASE_URL/KEY  : shared.config (already used by every agent)
  - Zerodha token     : read from Supabase `settings` (written by the Mac)

Run manually:  python -m agent8_data.data_agent
Scheduled:     master.py -> run_data_weekday() at 10:30 UTC.
"""

import os
import sys
import json
import time
import requests
import pandas as pd
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone, timedelta
from shared.config import SUPABASE_URL, SUPABASE_KEY

# ── CONFIG ────────────────────────────────────────────────────────
API_KEY  = os.environ.get("ZERODHA_API_KEY")
INTERVAL = "15minute"
DELAY    = 0.3          # seconds between symbols (gentle on the API)
SB_BATCH = 500          # rows per Supabase write

NIFTY50_INDEX_TOKEN = 256265

STOCKS = [
    "HDFCBANK",  "RELIANCE",   "ICICIBANK",  "BHARTIARTL", "LT",
    "INFY",      "SBIN",       "AXISBANK",   "ITC",        "KOTAKBANK",
    "M&M",       "BAJFINANCE", "TCS",        "SUNPHARMA",  "HINDUNILVR",
    "NTPC",      "ETERNAL",    "TATASTEEL",  "MARUTI",     "TITAN",
    "HINDALCO",  "BEL",        "POWERGRID",  "ULTRACEMCO", "ADANIPORTS",
    "SHRIRAMFIN","HCLTECH",    "GRASIM",     "JSWSTEEL",   "BAJAJ-AUTO",
    "ASIANPAINT","ONGC",       "COALINDIA",  "NESTLEIND",  "BAJAJFINSV",
    "INDIGO",    "EICHERMOT",  "TRENT",      "TECHM",      "APOLLOHOSP",
    "SBILIFE",   "MAXHEALTH",  "DRREDDY",    "CIPLA",      "TATACONSUM",
    "ADANIENT",  "JIOFIN",     "TMPV",       "HDFCLIFE",   "WIPRO",
]

SYMBOL_OVERRIDES: dict = {}


# ── HELPERS ───────────────────────────────────────────────────────

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def ist_now():
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def get_token_from_supabase():
    """Read today's Zerodha access token from Supabase `settings`
    (written by the Mac's 9:15 job — exactly how Agent 6 reads it)."""
    try:
        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/settings?key=eq.zerodha_access_token",
            headers=sb_headers(),
            timeout=10,
        )
        rows = res.json()
        if not rows:
            print("  No token row in Supabase settings.")
            return None
        data = json.loads(rows[0]["value"])
        if data.get("date") != str(ist_now().date()):
            print(f"  Token is from {data.get('date')}, not today — "
                  f"is the Mac's 9:15 token job done?")
            return None
        return data.get("token")
    except Exception as e:
        print(f"  Token fetch error: {e}")
        return None


def get_kite():
    from kiteconnect import KiteConnect
    token = get_token_from_supabase()
    if not token:
        return None
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    return kite


def build_token_map(kite):
    """symbol -> instrument_token for NSE equities."""
    df = pd.DataFrame(kite.instruments("NSE"))
    eq = df[df["segment"] == "NSE"][["tradingsymbol", "instrument_token"]]
    return dict(zip(eq["tradingsymbol"], eq["instrument_token"]))


def fetch_today_bars(kite, instrument_token, symbol, day):
    """Today's 15-min bars (09:15–15:30) for one instrument."""
    from_dt = datetime(day.year, day.month, day.day, 9, 15)
    to_dt   = datetime(day.year, day.month, day.day, 15, 30)
    rows = kite.historical_data(
        instrument_token, from_date=from_dt, to_date=to_dt,
        interval=INTERVAL, continuous=False, oi=False,
    )
    if not rows:
        return pd.DataFrame(
            columns=["datetime", "open", "high", "low", "close", "volume", "symbol"]
        )
    df = pd.DataFrame(rows)
    df.rename(columns={"date": "datetime"}, inplace=True)
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize(None)
    df["symbol"] = symbol
    df = df[["datetime", "open", "high", "low", "close", "volume", "symbol"]]
    df.sort_values("datetime", inplace=True)
    df.drop_duplicates("datetime", inplace=True)
    return df


def push_to_supabase(df, label):
    """Upsert OHLCV rows into Supabase ohlcv_data (idempotent on symbol,datetime)."""
    if df is None or df.empty:
        return 0
    dt = pd.to_datetime(df["datetime"])
    rows = [
        {
            "symbol":   str(r["symbol"]),
            "exchange": "NSE",
            "datetime": dt.iloc[i].strftime("%Y-%m-%dT%H:%M:%S+05:30"),
            "open":     float(r["open"]),
            "high":     float(r["high"]),
            "low":      float(r["low"]),
            "close":    float(r["close"]),
            "volume":   int(r["volume"]),
        }
        for i, (_, r) in enumerate(df.iterrows())
    ]
    h = sb_headers()
    h["Prefer"] = "resolution=merge-duplicates,return=minimal"
    pushed = 0
    for start in range(0, len(rows), SB_BATCH):
        chunk = rows[start:start + SB_BATCH]
        res = requests.post(
            f"{SUPABASE_URL}/rest/v1/ohlcv_data?on_conflict=symbol,datetime",
            headers=h, json=chunk, timeout=30,
        )
        if res.status_code in (200, 201, 204):
            pushed += len(chunk)
        else:
            print(f"  Supabase write {res.status_code} for {label}: {res.text[:150]}")
    return pushed


# ── MAIN ──────────────────────────────────────────────────────────

def run(trigger="manual"):
    n = ist_now()
    today = n.date()
    print(f"\n{'='*55}")
    print(f"AGENT 8 — Daily Data Updater  [{n.strftime('%Y-%m-%d %H:%M IST')}]  ({trigger})")
    print(f"{'='*55}")

    if today.weekday() >= 5:
        print("  Weekend — NSE closed. Nothing to update.")
        return

    kite = get_kite()
    if kite is None:
        print("  ✗ No valid token in Supabase — aborting (nothing written).")
        return

    total = 0

    # ── Nifty 50 index ──
    try:
        idx = fetch_today_bars(kite, NIFTY50_INDEX_TOKEN, "NIFTY 50", today)
        p = push_to_supabase(idx, "NIFTY 50")
        total += p
        print(f"  NIFTY 50 index : {p} bars")
    except Exception as e:
        print(f"  NIFTY 50 index failed: {e}")

    time.sleep(DELAY)

    # ── 50 constituents ──
    try:
        token_map = build_token_map(kite)
    except Exception as e:
        print(f"  ✗ Could not load instrument list: {e}")
        return

    ok, failed = 0, []
    for i, symbol in enumerate(STOCKS, 1):
        lookup = SYMBOL_OVERRIDES.get(symbol, symbol)
        tok = token_map.get(lookup)
        if tok is None:
            print(f"  [{i:02d}/50] {symbol:<12} ✗ instrument token not found")
            failed.append(symbol)
            continue
        try:
            df = fetch_today_bars(kite, tok, symbol, today)
            p = push_to_supabase(df, symbol)
            total += p
            ok += 1
        except Exception as e:
            print(f"  [{i:02d}/50] {symbol:<12} ✗ {e}")
            failed.append(symbol)
        time.sleep(DELAY)

    print(f"\n  Done — {ok}/{len(STOCKS)} stocks OK, {total} rows upserted to Supabase.")
    if failed:
        print(f"  Failed: {', '.join(failed)}")
    print(f"{'='*55}\n")


def run_data_weekday():
    """master.py wrapper — skip weekends."""
    if ist_now().weekday() <= 4:
        return run("scheduled")
    print("  Weekend — data updater skipped.")
    return None


if __name__ == "__main__":
    run("manual")
