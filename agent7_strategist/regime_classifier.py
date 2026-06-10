"""
AGENT 7 — Strategist / PM Desk
Module A: Regime Classifier  (Phase 1, v2)
=======================================
Runs pre-market each weekday. READ-ONLY. It classifies the day's market
regime and writes it to Supabase `agent7_regime`. It places NO trades and
sizes NOTHING — it only describes the state of the market so every later
Agent-7 module (signal fusion, conviction, risk, composer) can condition on it.

v2 changes (only):
  • Gap now comes from the EXACT 09:15 Zerodha open (token from Supabase ->
    kite.ohlc), with the Cloudflare worker as fallback — so the regime gap
    matches Agent 5's morning report instead of a drifted 9:25 live tick.
  • EMAIL_REPORT defaults ON and the email call is fixed to the real
    send_email(to=, subject=, html=) signature — you now get the card each AM.

What it outputs (one row per trading day):
  - trend_state      : trend_up | trend_down | range | unknown
  - vol_state        : expanding | contracting | stable | unknown
  - vol_level        : calm | normal | elevated | extreme | unknown   (India VIX band)
  - vol_percentile   : realized-vol percentile vs trailing window (0-100)
  - india_vix        : latest India VIX (NSE), if reachable
  - size_multiplier  : VIX-based GUIDANCE only (1.0 / 0.7 / 0.5) — not an order
  - gap_state        : Big/Small UP/DN, Flat, or pending
  - day_bias         : validated day-of-week bias (Master Reference)
  - dte / is_expiry  : days to weekly expiry (Tuesday, post Sep-2025) / today is expiry
  - event_blackout   : True near a known macro event or on expiry day
  - regime_label     : human-readable one-liner
  - playbook_hint    : which validated edges are favourable in this regime

Run manually:   python3 -m agent7_strategist.regime_classifier
Scheduled:      see run_regime_weekday() + the master.py line in the README.

NOTE ON SECRETS: imports from shared.config only. Do NOT add hardcoded keys
here — this module is part of the .env migration, not an exception to it.
"""

import os
import sys
import json
import requests
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from shared.config import SUPABASE_URL, SUPABASE_KEY, NOTIFY_EMAIL

# Email is optional — only used if EMAIL_REPORT is True.
try:
    from shared.send_email import send_email
except Exception:
    send_email = None

# ── CONFIG ────────────────────────────────────────────────────────
SYMBOL            = "NIFTY 50"
SYMBOL_ENC        = "NIFTY%2050"          # URL-encoded for Supabase REST
LOOKBACK_DAYS     = 90                     # calendar days of 15-min bars to pull
VOL_PCTL_WINDOW   = 60                     # trading days for realized-vol percentile
NIFTY_WORKER_URL  = "https://nifty-ticker.babi-naren.workers.dev"
API_KEY           = os.environ.get("ZERODHA_API_KEY")
EMAIL_REPORT      = True                   # v2: email the regime card each morning

# Validated day-of-week bias (Master Reference — % days closing green)
DOW_BIAS = {
    0: ("Monday",    51.7, "neutral"),
    1: ("Tuesday",   35.5, "strongly bearish (weekly expiry day)"),
    2: ("Wednesday", 53.0, "mildly bullish"),
    3: ("Thursday",  41.4, "mildly bearish"),
    4: ("Friday",    45.2, "mildly bearish"),
}

# Gap buckets (% of prev close), matching agent5_morning
GAP_CATEGORIES = [
    ("Big UP",    0.50,          float("inf")),
    ("Small UP",  0.10,          0.50),
    ("Flat",     -0.10,          0.10),
    ("Small DN", -0.50,         -0.10),
    ("Big DN",   float("-inf"), -0.50),
]

# Optional: known macro-event dates (ISO) that trigger an event blackout.
# Fill these in as RBI policy / Union Budget / major prints get scheduled.
EVENT_DATES = set([
    # "2026-06-06",
])


# ── HELPERS ───────────────────────────────────────────────────────

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def ist_now():
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def classify_gap(gap_pct):
    if gap_pct is None:
        return "pending"
    for name, lo, hi in GAP_CATEGORIES:
        if lo <= gap_pct < hi:
            return name
    return "Flat"


def vix_band(vix):
    """Master Reference position-sizing guidance. Returns (level, size_mult)."""
    if vix is None:
        return ("unknown", 1.0)
    if vix < 13:
        return ("calm", 1.0)
    if vix < 18:
        return ("normal", 1.0)
    if vix < 22:
        return ("elevated", 0.7)
    return ("extreme", 0.5)


# ── DATA: DAILY BARS FROM SUPABASE ────────────────────────────────

def get_daily_bars(lookback_days=LOOKBACK_DAYS):
    """Pull 15-min NIFTY 50 bars and aggregate into daily OHLC (IST dates)."""
    try:
        start = (ist_now().date() - timedelta(days=lookback_days)).isoformat()
        url = (
            f"{SUPABASE_URL}/rest/v1/ohlcv_data"
            f"?symbol=eq.{SYMBOL_ENC}"
            f"&datetime=gte.{start}T00:00:00%2B00:00"
            f"&order=datetime.asc"
            f"&limit=10000"
        )
        r = requests.get(url, headers=sb_headers(), timeout=30)
        bars = r.json()
        if not isinstance(bars, list) or not bars:
            print(f"  Supabase returned: {str(bars)[:200]}")
            return None

        df = pd.DataFrame(bars)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df["d"] = df["datetime"].dt.tz_convert("Asia/Kolkata").dt.date
        for col in ("open", "high", "low", "close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        daily = (
            df.groupby("d")
              .agg(open=("open", "first"),
                   high=("high", "max"),
                   low=("low", "min"),
                   close=("close", "last"))
              .reset_index()
              .sort_values("d")
              .reset_index(drop=True)
        )
        return daily if len(daily) >= 25 else daily  # caller checks length
    except Exception as e:
        print(f"  Daily bars error: {e}")
        return None


def compute_features(daily):
    d = daily.copy()
    d["ret"]    = d["close"].pct_change()
    d["sma20"]  = d["close"].rolling(20).mean()
    d["sma50"]  = d["close"].rolling(50).mean()

    prev_close = d["close"].shift(1)
    tr = pd.concat([
        (d["high"] - d["low"]),
        (d["high"] - prev_close).abs(),
        (d["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    d["atr14"]  = tr.rolling(14).mean()
    d["rvol5"]  = d["ret"].rolling(5).std()  * (252 ** 0.5) * 100
    d["rvol20"] = d["ret"].rolling(20).std() * (252 ** 0.5) * 100
    return d


# ── CLASSIFIERS ───────────────────────────────────────────────────

def classify_trend(d):
    last = d.iloc[-1]
    sma20, sma50, close = last["sma20"], last["sma50"], last["close"]
    if pd.isna(sma20) or pd.isna(sma50) or len(d) < 26:
        return "unknown"
    slope20 = d["sma20"].iloc[-1] - d["sma20"].iloc[-6]   # ~1 week slope
    if close > sma20 and sma20 > sma50 and slope20 > 0:
        return "trend_up"
    if close < sma20 and sma20 < sma50 and slope20 < 0:
        return "trend_down"
    return "range"


def classify_vol_state(d):
    last = d.iloc[-1]
    s, l = last["rvol5"], last["rvol20"]
    if pd.isna(s) or pd.isna(l):
        return "unknown"
    if s > l * 1.15:
        return "expanding"
    if s < l * 0.85:
        return "contracting"
    return "stable"


def vol_percentile(d, window=VOL_PCTL_WINDOW):
    series = d["rvol20"].dropna().tail(window)
    if len(series) < 10:
        return None
    today = series.iloc[-1]
    return round(float((series < today).mean()) * 100, 1)


# ── LIVE INPUTS: TODAY OPEN (ZERODHA) + INDIA VIX ─────────────────

def get_token_from_supabase():
    """Read today's Zerodha access token from Supabase settings
    (written by the Mac's 9:15 job — exactly how Agent 6 / Agent 5 read it)."""
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


def get_live_price():
    """Fallback: read agent-6's live Nifty price (Cloudflare KV via worker)."""
    try:
        r = requests.get(NIFTY_WORKER_URL, timeout=10)
        data = r.json()
        if isinstance(data, (int, float)):
            return float(data)
        if isinstance(data, dict):
            for k in ("price", "ltp", "last_price", "last", "value", "nifty_price"):
                if k in data and data[k] is not None:
                    return float(data[k])
    except Exception as e:
        print(f"  Worker price error: {e}")
    return None


def get_today_open():
    """Today's Nifty open — pulled straight from Zerodha (the exact 09:15 open),
    independent of when the live ticker warms up. Falls back to the Cloudflare
    worker only if the direct fetch is unavailable."""
    token = get_token_from_supabase()
    if token:
        try:
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=API_KEY)
            kite.set_access_token(token)
            ohlc = kite.ohlc(["NSE:NIFTY 50"])["NSE:NIFTY 50"]["ohlc"]
            op = float(ohlc["open"])
            if op > 0:
                print(f"  Today's open (Zerodha): {op:,.2f}")
                return op
            print("  Zerodha returned open=0 — trying worker...")
        except Exception as e:
            print(f"  Zerodha open fetch failed ({e}) — trying worker...")
    else:
        print("  No valid token in Supabase — trying worker...")
    return get_live_price()


def get_india_vix():
    """Fetch India VIX from NSE (cookie two-step, like agent-4's FII/DII)."""
    try:
        s = requests.Session()
        h = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
        }
        s.get("https://www.nseindia.com", headers=h, timeout=10)
        r = s.get("https://www.nseindia.com/api/allIndices", headers=h, timeout=10)
        for idx in r.json().get("data", []):
            if idx.get("index", "").upper().replace(" ", "") == "INDIAVIX":
                return float(idx.get("last"))
    except Exception as e:
        print(f"  VIX fetch error: {e}")
    return None


# ── EXPIRY / EVENT PROXIMITY ──────────────────────────────────────

def expiry_info(today):
    """Weekly expiry = Tuesday (post Sep-2025 NSE change)."""
    wd = today.weekday()                 # 0=Mon … 6=Sun
    dte = (1 - wd) % 7                    # days to next Tuesday
    is_expiry = (wd == 1)
    return dte, is_expiry


# ── PLAYBOOK HINTS (keyed to validated Master Reference edges) ─────

def build_playbook(trend_state, vol_state, vol_level, vol_pctl,
                   gap_state, dow, is_expiry):
    hints = []

    # Day-of-week + gap (strongest validated daily setup)
    if dow == 1 and gap_state == "Small UP":
        hints.append("TUE + Small Gap UP = 79% bear (best daily setup): "
                     "favour ATM PE / bear call spread, exit by 15:00.")
    elif dow == 1:
        hints.append("Tuesday structurally bearish (35.5% bull): "
                     "lean short; NEVER trade Tue ORB Confirmed-UP (41% acc).")
    elif dow == 2:
        hints.append("Wednesday best-balanced day: both ORB directions reliable.")

    # Trend vs range
    if trend_state in ("trend_up", "trend_down"):
        hints.append(f"{trend_state.replace('_',' ').title()}: directional day likely "
                     "— trade in trend direction; ORB-confirmed breaks favoured.")
    elif trend_state == "range":
        hints.append("Range regime: credit spreads / iron condor; fade extremes; "
                     "expect 11AM-1PM dead zone.")

    # Volatility level → sizing + structure
    if vol_level == "extreme":
        hints.append("VIX EXTREME: halve size; defined-risk spreads only.")
    elif vol_level == "elevated":
        hints.append("VIX elevated: reduce size ~30%.")

    # Realized-vol percentile → buy vs sell premium
    if vol_pctl is not None:
        if vol_pctl >= 70:
            hints.append("Vol percentile high: premium rich — favour SELLING / spreads "
                         "over naked option buys (IV-crush risk on buys).")
        elif vol_pctl <= 30:
            hints.append("Vol percentile low: premium cheap — debit/long structures "
                         "relatively more attractive.")

    if vol_state == "expanding":
        hints.append("Vol expanding: widen stops; momentum-ignition fades less reliable.")

    if is_expiry:
        hints.append("Weekly expiry (Tue): elevated gamma late-day; flatten before 15:00.")

    # Always-on intraday edge
    hints.append("Failed-ORB-Trap valid across regimes (~80% reversal): "
                 "fade ORB pokes that close back inside within one bar.")
    return hints


# ── ASSEMBLE + PERSIST ────────────────────────────────────────────

def save_regime(reg):
    """Upsert one row per date into agent7_regime (date is primary key)."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/agent7_regime?on_conflict=date"
        h = sb_headers()
        h["Prefer"] = "resolution=merge-duplicates,return=minimal"
        r = requests.post(url, headers=h, json=reg, timeout=20)
        if r.status_code in (200, 201, 204):
            print("  ✓ Saved regime to Supabase.")
        else:
            print(f"  Supabase write {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  Save error: {e}")


def render_card(reg, hints):
    """Build the regime card as a text block (used for both terminal + email)."""
    L = []
    L.append("=" * 58)
    L.append(f"AGENT 7 · REGIME  [{reg['date']}]")
    L.append("=" * 58)
    L.append(f"  Trend       : {reg['trend_state']}")
    L.append(f"  Volatility  : {reg['vol_state']} | level {reg['vol_level']} "
             f"(VIX {reg['india_vix']}) | pctl {reg['vol_percentile']}")
    L.append(f"  Size mult   : {reg['size_multiplier']}x  (guidance only)")
    L.append(f"  Gap         : {reg['gap_state']} ({reg['gap_pct']}%)")
    L.append(f"  Day bias    : {reg['day_bias']}")
    L.append(f"  Expiry      : DTE {reg['dte']} | is_expiry {reg['is_expiry']}")
    L.append(f"  Blackout    : {reg['event_blackout']}")
    L.append(f"  Label       : {reg['regime_label']}")
    L.append("")
    L.append("  PLAYBOOK:")
    for h in hints:
        L.append(f"    • {h}")
    L.append("=" * 58)
    return "\n".join(L)


def print_card(reg, hints):
    print("\n" + render_card(reg, hints) + "\n")


def email_card(reg, hints, today):
    """v2: email the regime card via Resend, using the real send_email signature."""
    if not (EMAIL_REPORT and send_email):
        return
    try:
        card = render_card(reg, hints)
        html = (
            "<div style=\"font-family:'Courier New',monospace;font-size:12px;"
            "line-height:1.6;background:#0a0a0a;color:#e2e8f0;padding:24px;max-width:700px;\">"
            "<pre style=\"font-family:'Courier New',monospace;font-size:12px;"
            f"white-space:pre-wrap;color:#e2e8f0;\">{card}</pre></div>"
        )
        send_email(
            to=NOTIFY_EMAIL,
            subject=f"Agent 7 Regime — {today.strftime('%d %b %Y')}",
            html=html,
        )
        print(f"  ✓ Regime card emailed to {NOTIFY_EMAIL}")
    except Exception as e:
        print(f"  Email error: {e}")


def run(trigger="manual"):
    n = ist_now()
    today = n.date()
    dow = today.weekday()

    print(f"\nAGENT 7 — Regime Classifier  [{n.strftime('%Y-%m-%d %H:%M')} IST] "
          f"(trigger: {trigger})")

    daily = get_daily_bars()
    if daily is None or len(daily) < 26:
        print("  ✗ Not enough daily history to classify. Aborting (no row written).")
        return None

    d = compute_features(daily)

    trend_state = classify_trend(d)
    vol_state   = classify_vol_state(d)
    vol_pctl    = vol_percentile(d)

    # India VIX (level + size guidance)
    vix = get_india_vix()
    vol_level, size_mult = vix_band(vix)

    # Gap: prefer today's open if today's row exists, else the EXACT Zerodha open
    last_date  = daily["d"].iloc[-1]
    prev_close = float(daily["close"].iloc[-1])
    today_open = None
    if last_date == today:
        today_open = float(daily["open"].iloc[-1])
        prev_close = float(daily["close"].iloc[-2]) if len(daily) >= 2 else prev_close
    else:
        today_open = get_today_open()      # v2: Zerodha 09:15 open, worker fallback

    gap_pct = None
    if today_open and prev_close:
        gap_pct = round((today_open - prev_close) / prev_close * 100, 2)
    gap_state = classify_gap(gap_pct)

    day_name, day_bull, day_desc = DOW_BIAS.get(dow, ("Weekend", None, "no session"))
    day_bias = f"{day_name}: {day_desc}" + (f" ({day_bull}% bull)" if day_bull else "")

    dte, is_expiry = expiry_info(today)
    event_blackout = bool(is_expiry or today.isoformat() in EVENT_DATES
                          or vol_level == "extreme")

    label = (f"{trend_state} / vol {vol_state}-{vol_level} / "
             f"{gap_state} gap / {day_name}")

    hints = build_playbook(trend_state, vol_state, vol_level, vol_pctl,
                           gap_state, dow, is_expiry)

    reg = {
        "date":            today.isoformat(),
        "generated_at":    n.isoformat(),
        "trend_state":     trend_state,
        "vol_state":       vol_state,
        "vol_level":       vol_level,
        "vol_percentile":  vol_pctl,
        "india_vix":       vix,
        "size_multiplier": size_mult,
        "gap_state":       gap_state,
        "gap_pct":         gap_pct,
        "day_bias":        day_bias,
        "dte":             dte,
        "is_expiry":       is_expiry,
        "event_blackout":  event_blackout,
        "regime_label":    label,
        "playbook_hint":   hints,
        "features": {
            "close":  round(float(d["close"].iloc[-1]), 2),
            "sma20":  round(float(d["sma20"].iloc[-1]), 2) if not pd.isna(d["sma20"].iloc[-1]) else None,
            "sma50":  round(float(d["sma50"].iloc[-1]), 2) if not pd.isna(d["sma50"].iloc[-1]) else None,
            "atr14":  round(float(d["atr14"].iloc[-1]), 2) if not pd.isna(d["atr14"].iloc[-1]) else None,
            "rvol5":  round(float(d["rvol5"].iloc[-1]), 2) if not pd.isna(d["rvol5"].iloc[-1]) else None,
            "rvol20": round(float(d["rvol20"].iloc[-1]), 2) if not pd.isna(d["rvol20"].iloc[-1]) else None,
        },
    }

    print_card(reg, hints)
    save_regime(reg)
    email_card(reg, hints, today)

    print("✓ Agent 7 regime complete.\n")
    return reg


def run_regime_weekday():
    """master.py wrapper — skip weekends."""
    if ist_now().weekday() <= 4:
        return run("scheduled")
    print("  Weekend — regime classifier skipped.")
    return None


if __name__ == "__main__":
    trig = sys.argv[1] if len(sys.argv) > 1 else "manual"
    run(trig)
