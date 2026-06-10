"""
AGENT 7 — Strategist / PM Desk
Module B+C: Signal Fusion + Conviction  (Phase 2)
==================================================
Runs pre-market each weekday, AFTER Phase 1 (the regime classifier).
READ-ONLY + PAPER. It places NO trades, sizes NOTHING, and proposes NO
option legs (those are Phase 3+). It turns your validated Master Reference
edges into *scored candidate signals*, each conditioned on today's regime,
then fuses them into ONE primary view + an overall conviction, and writes a
PAPER-mode PM note to Supabase `agent7_signals`.

Pipeline:
  1. Get today's regime (read agent7_regime; if missing, generate via Phase 1).
  2. Build candidate signals from the Master Reference edge library
     (DOW bias, gap x day setups, spillover, streak/anti-persistence, trend).
  3. Score each signal: conviction = edge x sample-size factor x regime-agreement.
  4. Fuse: bull vs bear scores -> primary_view + overall_conviction + consensus,
     with conflict detection and macro-event / range guards -> action.
  5. Print a PM card, save to agent7_signals, optionally email.

Deliberately deterministic and inspectable (NOT an LLM black box). Every
number traces to a validated Master Reference statistic. An optional LLM
"second-pass" confirmation can be layered on in a later phase.

Run manually:   python3 -m agent7_strategist.signal_fusion
Scheduled:      see run_signals_weekday() + the master.py / launchd note in the README.

NOTE ON SECRETS: imports from shared.config only. No hardcoded keys here.
"""

import os
import sys
import json
import requests
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from shared.config import SUPABASE_URL, SUPABASE_KEY, NOTIFY_EMAIL

# Reuse Phase 1's data layer + helpers (single source of truth).
from agent7_strategist.regime_classifier import (
    ist_now, sb_headers, get_daily_bars, EVENT_DATES,
)
from agent7_strategist import regime_classifier

try:
    from shared.send_email import send_email
except Exception:
    send_email = None

# ── CONFIG ────────────────────────────────────────────────────────
EMAIL_REPORT = False                 # set True to also email the PM note
TIER_WEIGHT  = {3: 1.0, 2: 0.8, 1: 0.6}
DOW_NAME     = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ── HELPERS ───────────────────────────────────────────────────────

def _edge(name, direction, base_rate, n, tier, rationale, source, conditional=False):
    """One candidate signal. base_rate = probability IN ITS OWN direction (>=0.5)."""
    return {
        "name": name, "direction": direction, "base_rate": base_rate,
        "n": n, "tier": tier, "rationale": rationale, "source": source,
        "conditional": conditional,
    }


def _streak(daily):
    """Consecutive same-direction (close vs open) sessions, ending at the last row."""
    if daily is None or len(daily) == 0:
        return 0, None
    last_bull = bool(float(daily.iloc[-1]["close"]) > float(daily.iloc[-1]["open"]))
    streak = 1
    for i in range(len(daily) - 2, -1, -1):
        b = bool(float(daily.iloc[i]["close"]) > float(daily.iloc[i]["open"]))
        if b == last_bull:
            streak += 1
        else:
            break
    return streak, last_bull


def get_today_regime():
    """Read today's agent7_regime row; if absent, run Phase 1 to generate it."""
    today = ist_now().date().isoformat()
    try:
        url = f"{SUPABASE_URL}/rest/v1/agent7_regime?date=eq.{today}&limit=1"
        r = requests.get(url, headers=sb_headers(), timeout=15)
        rows = r.json()
        if isinstance(rows, list) and rows:
            print("  Using today's regime row from Supabase.")
            return rows[0]
    except Exception as e:
        print(f"  Regime read error: {e}")
    print("  No regime row yet — generating via Phase 1...")
    return regime_classifier.run("from_signal_fusion")


# ── MODULE B: BUILD CANDIDATE SIGNALS ─────────────────────────────

def build_candidate_signals(reg, daily):
    """Activate Master Reference edges that apply to today's regime."""
    sigs = []
    today     = ist_now().date()
    dow       = today.weekday()
    gap_state = reg.get("gap_state")
    trend     = reg.get("trend_state")

    # Yesterday (last completed session) for spillover + streak
    yest_ret = prev_dow = None
    streak, streak_bull = 0, None
    if daily is not None and len(daily) >= 1:
        yest = daily.iloc[-1]
        o, c = float(yest["open"]), float(yest["close"])
        yest_ret = (c - o) / o * 100 if o else None
        try:
            prev_dow = pd.Timestamp(yest["d"]).weekday()
        except Exception:
            prev_dow = None
        streak, streak_bull = _streak(daily)

    # A. Day-of-week bias (Master Reference §2) — fires every applicable day
    dow_map = {
        1: ("bear", 0.645, 121, 3, "Tuesday structurally bearish (35.5% bull, p=0.001)"),
        2: ("bull", 0.530, 115, 1, "Wednesday mildly bullish (53.0% bull)"),
        3: ("bear", 0.586, 116, 2, "Thursday bearish post-Sep-2025 (41.4% bull, -15.2 bps)"),
        4: ("bear", 0.548, 115, 1, "Friday mildly bearish (45.2% bull)"),
    }
    if dow in dow_map:
        d, br, n, t, r = dow_map[dow]
        sigs.append(_edge(f"DOW {DOW_NAME[dow]} bias", d, br, n, t, r, "DOW"))

    # B. Gap × Day top setups (Master Reference §5.3)
    if dow == 1 and gap_state == "Small UP":
        sigs.append(_edge("TUE + Small Gap UP", "bear", 0.79, 33, 3,
                          "Best validated daily setup: Tue small gap-up fades 79% bear (n=33)", "GapDay"))
    if dow == 4 and gap_state in ("Small UP", "Big UP"):
        sigs.append(_edge("FRI gap-up (sustained?)", "bull", 0.95, 20, 2,
                          "Fri sustained gap-up 95% bull (n=20) — CONDITIONAL on not filling by 10:30",
                          "GapDay", conditional=True))
    if dow == 0 and gap_state in ("Small DN", "Big DN"):
        sigs.append(_edge("MON med gap-down", "bull", 0.67, 12, 2,
                          "Monday medium gap-down 67% bull (weekend reversal, n=12; bucket approx)", "GapDay"))
    if dow == 2 and gap_state in ("Small DN", "Big DN"):
        sigs.append(_edge("WED med gap-down", "bull", 0.70, 12, 2,
                          "Wednesday medium gap-down 70% bull (n=12; bucket approx)", "GapDay"))

    # C. Spillover (Master Reference §7.1) — needs yesterday's direction
    if prev_dow is not None and yest_ret is not None:
        if prev_dow == 0 and yest_ret > 0.3 and dow == 1:
            sigs.append(_edge("Spillover Mon-up -> Tue-short", "bear", 0.667, 36, 2,
                              f"Monday closed +{yest_ret:.2f}% (>0.3%) -> Tuesday short 66.7% (n=36)",
                              "Spillover"))
            if gap_state == "Small UP":
                sigs.append(_edge("STACKED Mon-up + Tue small gap-up", "bear", 0.82, 11, 3,
                                  "Stacked: Mon-up AND Tue small gap-up -> 82% bear (n=11) — max conviction",
                                  "Spillover"))
        if prev_dow == 1 and yest_ret < -0.3 and dow == 2:
            sigs.append(_edge("Spillover Tue-dn -> Wed-long", "bull", 0.622, 45, 2,
                              f"Tuesday closed {yest_ret:.2f}% (<-0.3%) -> Wednesday long 62.2% (n=45)",
                              "Spillover"))
        if prev_dow == 4 and yest_ret < -0.5 and dow == 0:
            sigs.append(_edge("Spillover Fri-dn -> Mon-long", "bull", 0.645, 31, 2,
                              f"Friday closed {yest_ret:.2f}% (<-0.5%) -> Monday long 64.5% (n=31)",
                              "Spillover"))

    # D. Streak / anti-persistence (Master Reference §7.2)
    if streak == 1 and streak_bull is True:
        sigs.append(_edge("Anti-persist after 1 up day", "bear", 0.586, 145, 2,
                          "After 1 up day only 41.4% next-day bull (n=145) — mild fade", "Streak"))
    if streak >= 3 and streak_bull is False:
        sigs.append(_edge("Recovery after 3 down days", "bull", 0.511, 45, 1,
                          "After 3+ down days, mild bounce 51.1% (n=45)", "Streak"))

    # E. Trend regime context (Phase 1 trend_state) — directional prior, n=None
    if trend == "trend_up":
        sigs.append(_edge("Regime trend-up", "bull", 0.62, None, 2,
                          "Daily trend up (close>SMA20>SMA50, rising) — directional bull context", "Trend"))
    elif trend == "trend_down":
        sigs.append(_edge("Regime trend-down", "bear", 0.62, None, 2,
                          "Daily trend down (close<SMA20<SMA50, falling) — directional bear context", "Trend"))

    context = {"yest_ret": round(yest_ret, 2) if yest_ret is not None else None,
               "prev_dow": prev_dow, "streak": streak, "streak_bull": streak_bull}
    return sigs, context


# ── MODULE C: SCORE + FUSE ────────────────────────────────────────

def score_signals(sigs, reg):
    """Conviction = edge x sample-size factor x regime-agreement, with a
    discount for signals that still need an intraday confirmation."""
    trend = reg.get("trend_state")
    trend_dir = {"trend_up": "bull", "trend_down": "bear"}.get(trend)

    for s in sigs:
        raw_edge = max(0.0, (s["base_rate"] - 0.5) / 0.5)           # 0..1
        n = s["n"]
        n_factor = 0.7 if n is None else min(1.0, n / 30.0)         # cap small samples
        if trend_dir is None:
            agree = 1.0
        elif s["direction"] == trend_dir:
            agree = 1.1
        else:
            agree = 0.9
        conv = raw_edge * n_factor * agree
        if s.get("conditional"):
            conv *= 0.6                                             # pre-confirm discount
        conv = max(0.0, min(1.0, conv))
        s["conviction"]   = round(conv, 3)
        s["weight"]       = TIER_WEIGHT.get(s["tier"], 0.8)
        s["contribution"] = round((1 if s["direction"] == "bull" else -1) * conv * s["weight"], 3)
    # rank by absolute contribution
    sigs.sort(key=lambda x: abs(x["contribution"]), reverse=True)
    return sigs


def fuse(sigs, reg, macro_event):
    bull = sum(s["conviction"] * s["weight"] for s in sigs if s["direction"] == "bull")
    bear = sum(s["conviction"] * s["weight"] for s in sigs if s["direction"] == "bear")
    net, total = bull - bear, bull + bear
    consensus  = (net / total) if total > 0 else 0.0
    overall    = min(1.0, abs(net))

    top_bull = max([s["conviction"] for s in sigs if s["direction"] == "bull"], default=0.0)
    top_bear = max([s["conviction"] for s in sigs if s["direction"] == "bear"], default=0.0)
    conflict = (top_bull > 0.5 and top_bear > 0.5)
    if conflict:
        overall *= 0.6

    if total == 0 or abs(consensus) < 0.25:
        view = "NEUTRAL"
    elif net > 0:
        view = "BULLISH"
    else:
        view = "BEARISH"

    # Action ladder — Phase 2 is PAPER-only (no LIVE/SHADOW yet).
    if macro_event:
        action, reason = "FLAT", "Macro-event blackout / extreme VIX — stand aside"
    elif view == "NEUTRAL":
        action, reason = "PAPER", "No clean directional edge — credit spreads / stand aside (paper)"
    else:
        action, reason = "PAPER", "Directional lean — PAPER only (validate before any live size)"

    return {
        "primary_view": view,
        "overall_conviction": round(overall, 3),
        "consensus": round(consensus, 3),
        "bull_score": round(bull, 3),
        "bear_score": round(bear, 3),
        "conflict": conflict,
        "action": action,
        "reason": reason,
    }


def tactical_rules(reg):
    """Intraday if-then triggers (NOT scored pre-market) — carried verbatim
    from the Master Reference so the morning note still flags them."""
    rules = []
    dow = ist_now().date().weekday()
    if dow == 1:
        rules.append("ORB: Tue Confirmed-DOWN = 92% bear (MAX size). NEVER trade Tue Confirmed-UP (41%).")
    else:
        rules.append("ORB (9:45 confirm): Confirmed-DOWN ~77% bear / Confirmed-UP ~67% bull.")
    rules.append("Failed-ORB-Trap: fade pokes outside the 9:15 range that close back inside next bar (~80%).")
    rules.append("VWAP @10:30: GapDN+above VWAP = 76% bull; Flat+below VWAP = 21% bull (bear).")
    if reg.get("is_expiry"):
        rules.append("Weekly expiry (Tue): elevated late-day gamma — flatten before 15:00.")
    rules.append("Exit discipline: trim 50% ~14:30, flat by 15:00 (Friday: flat by 14:30).")
    return rules


# ── PERSIST + PRINT ───────────────────────────────────────────────

def save_signals(payload):
    try:
        url = f"{SUPABASE_URL}/rest/v1/agent7_signals?on_conflict=date"
        h = sb_headers()
        h["Prefer"] = "resolution=merge-duplicates,return=minimal"
        r = requests.post(url, headers=h, json=payload, timeout=20)
        if r.status_code in (200, 201, 204):
            print("  ✓ Saved PM note to agent7_signals.")
        else:
            print(f"  Supabase write {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"  Save error: {e}")


def print_card(payload, sigs, rules, reg):
    arrow = {"BULLISH": "▲", "BEARISH": "▼", "NEUTRAL": "■"}.get(payload["primary_view"], "■")
    print(f"\n{'='*60}")
    print(f"AGENT 7 · PM NOTE (PAPER)   [{payload['date']}]")
    print(f"{'='*60}")
    print(f"  Regime      : {reg.get('regime_label', '—')}")
    print(f"  Primary view: {arrow} {payload['primary_view']}   "
          f"conviction {payload['overall_conviction']}  (consensus {payload['consensus']})")
    print(f"  Scores      : bull {payload['bull_score']}  |  bear {payload['bear_score']}"
          f"{'   ⚠ CONFLICT' if payload['conflict'] else ''}")
    print(f"  Action      : {payload['action']} — {payload['reason']}")
    print(f"\n  SCORED SIGNALS ({len(sigs)}):")
    if sigs:
        for s in sigs:
            d = "BULL" if s["direction"] == "bull" else "BEAR"
            print(f"    [{d}] {s['name']:<34} conv {s['conviction']:.2f} "
                  f"(base {s['base_rate']:.0%}, n={s['n']}, ★{s['tier']})")
            print(f"          {s['rationale']}")
    else:
        print("    (none triggered today)")
    print(f"\n  TACTICAL RULES (intraday, not scored):")
    for r in rules:
        print(f"    • {r}")
    print(f"{'='*60}\n")


# ── MAIN ──────────────────────────────────────────────────────────

def run(trigger="manual"):
    n = ist_now()
    today = n.date()
    dow = today.weekday()

    print(f"\nAGENT 7 — Signal Fusion  [{n.strftime('%Y-%m-%d %H:%M')} IST] (trigger: {trigger})")

    if dow >= 5:
        print("  Weekend — no signals today.")
        return None

    reg = get_today_regime()
    if reg is None:
        print("  ✗ No regime available — cannot fuse signals. Aborting.")
        return None

    daily = get_daily_bars()

    sigs, context = build_candidate_signals(reg, daily)
    sigs = score_signals(sigs, reg)

    macro_event = bool(reg.get("vol_level") == "extreme" or today.isoformat() in EVENT_DATES)
    verdict = fuse(sigs, reg, macro_event)
    rules = tactical_rules(reg)

    payload = {
        "date":               today.isoformat(),
        "generated_at":       n.isoformat(),
        "primary_view":       verdict["primary_view"],
        "overall_conviction": verdict["overall_conviction"],
        "consensus":          verdict["consensus"],
        "bull_score":         verdict["bull_score"],
        "bear_score":         verdict["bear_score"],
        "conflict":           verdict["conflict"],
        "action":             verdict["action"],
        "reason":             verdict["reason"],
        "signals":            sigs,
        "tactical_rules":     rules,
        "context":            context,
    }

    print_card(payload, sigs, rules, reg)
    save_signals(payload)

    if EMAIL_REPORT and send_email:
        try:
            body = "AGENT 7 PM NOTE (PAPER)\n\n" + json.dumps(payload, indent=2)
            send_email(f"Agent 7 PM Note — {today.isoformat()}", body, NOTIFY_EMAIL)
        except Exception as e:
            print(f"  Email error: {e}")

    print("✓ Agent 7 signal fusion complete.\n")
    return payload


def run_signals_weekday():
    """master.py / launchd wrapper — skip weekends."""
    if ist_now().weekday() <= 4:
        return run("scheduled")
    print("  Weekend — signal fusion skipped.")
    return None


if __name__ == "__main__":
    trig = sys.argv[1] if len(sys.argv) > 1 else "manual"
    run(trig)
