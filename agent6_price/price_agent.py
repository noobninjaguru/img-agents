"""
AGENT 6 — Nifty Live Price Pusher
====================================
Runs continuously on Railway during market hours.
Reads Zerodha token from Supabase every 60 seconds.
Fetches live Nifty price and pushes to Cloudflare KV.
Completely replaces nifty_server.py on Mac.
"""

import os
import sys
import json
import time
import requests
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timezone, timedelta
from shared.config import SUPABASE_URL, SUPABASE_KEY

# ── CONFIG ────────────────────────────────────────────────────────
API_KEY         = os.environ.get("ZERODHA_API_KEY")
API_SECRET      = os.environ.get("ZERODHA_API_SECRET")
CF_ACCOUNT_ID   = os.environ.get("CF_ACCOUNT_ID")
CF_NAMESPACE_ID = os.environ.get("CF_NAMESPACE_ID")
CF_API_TOKEN    = os.environ.get("CF_API_TOKEN")
KV_KEY          = "nifty_price"
POLL_INTERVAL   = 60


def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }


def ist_now():
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def is_market_open():
    n = ist_now()
    if n.weekday() >= 5:
        return False
    h, m = n.hour, n.minute
    return (h * 60 + m) >= (9 * 60 + 15) and (h * 60 + m) <= (15 * 60 + 30)


def get_token_from_supabase():
    """Read today's Zerodha token from Supabase settings table."""
    try:
        res = requests.get(
            f"{SUPABASE_URL}/rest/v1/settings?key=eq.zerodha_access_token",
            headers=sb_headers(),
            timeout=10
        )
        rows = res.json()
        if not rows:
            return None
        data = json.loads(rows[0]["value"])
        if data.get("date") != str(date.today()):
            print(f"  Token is from {data.get('date')} — waiting for today's token")
            return None
        return data.get("token")
    except Exception as e:
        print(f"  Token fetch error: {e}")
        return None


def fetch_nifty_price(token):
    """Fetch live Nifty price from Zerodha using token."""
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    resp = kite.ohlc(["NSE:NIFTY 50"])
    d = resp["NSE:NIFTY 50"]
    price      = float(d["last_price"])
    prev_close = float(d["ohlc"]["close"])
    change     = round(price - prev_close, 2)
    change_pct = round(change / prev_close * 100, 2) if prev_close else 0.0
    return price, change, change_pct


def push_to_cloudflare(price, change, change_pct):
    """Push price to Cloudflare KV."""
    payload = {
        "price":       price,
        "change":      change,
        "change_pct":  change_pct,
        "market_open": is_market_open(),
        "time":        ist_now().strftime("%H:%M IST"),
    }
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
        f"/storage/kv/namespaces/{CF_NAMESPACE_ID}/values/{KV_KEY}"
    )
    res = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {CF_API_TOKEN}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=10
    )
    res.raise_for_status()
    return payload


def run():
    print(f"\n{'='*55}")
    print(f"AGENT 6 — Nifty Live Price Pusher")
    print(f"Started: {ist_now().strftime('%Y-%m-%d %H:%M IST')}")
    print(f"{'='*55}\n")

    token = None
    consecutive_errors = 0

    while True:
        try:
            n = ist_now()

            # Outside market hours — push closed status every 5 min
            if not is_market_open():
                payload = {
                    "price":       0,
                    "change":      0,
                    "change_pct":  0,
                    "market_open": False,
                    "time":        n.strftime("%H:%M IST"),
                }
                try:
                    push_to_cloudflare(0, 0, 0)
                except Exception:
                    pass
                time.sleep(300)  # check every 5 min outside market hours
                continue

            # Get today's token from Supabase
            if token is None:
                token = get_token_from_supabase()
                if token is None:
                    print(f"  [{n.strftime('%H:%M:%S')}] Waiting for token upload from Mac...")
                    time.sleep(30)
                    continue
                print(f"  [{n.strftime('%H:%M:%S')}] Token loaded from Supabase ✓")

            # Fetch and push price
            price, change, change_pct = fetch_nifty_price(token)
            push_to_cloudflare(price, change, change_pct)

            sign = "+" if change >= 0 else ""
            print(f"  [{n.strftime('%H:%M:%S')}]  {price:>10,.2f}  {sign}{change:.2f} ({sign}{change_pct:.2f}%)")

            consecutive_errors = 0
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n  Stopped.")
            break
        except Exception as e:
            consecutive_errors += 1
            ts = ist_now().strftime("%H:%M:%S")
            print(f"  [{ts}] Error: {e} — retrying in 30s")
            # Reset token on auth errors
            if "access_token" in str(e).lower() or "api_key" in str(e).lower():
                token = None
                print(f"  Token invalidated — will fetch fresh from Supabase")
            time.sleep(30)


if __name__ == "__main__":
    run()
