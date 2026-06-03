"""
MASTER SCHEDULER — Indian Market Guru Agent System
====================================================
Run this once and leave it running. It manages all three agents:

  Agent 1 — News Ticker:     every 2 hours, all day
  Agent 2 — Sentiment Score: 8:30 AM IST, weekdays only
  Agent 3 — Editor-in-Chief: 8:00 PM IST daily

Start with:  python master.py
Stop with:   Ctrl+C
"""

import schedule
import time
import threading
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

# Import all three agents
from agent1_news.news_ticker_agent    import run as run_news
from agent2_sentiment.sentiment_agent import run as run_sentiment
from agent3_editor.editor_agent       import run as run_editor


def safe_run(agent_name, func, *args):
    """Wrap agent runs so one failure doesn't kill the scheduler."""
    try:
        print(f"\n▶ Starting {agent_name}...")
        func(*args)
    except Exception as e:
        print(f"\n✗ {agent_name} failed: {e}")
        import traceback
        traceback.print_exc()


def is_weekday():
    return datetime.now().weekday() < 5  # Mon-Fri


# ── SCHEDULE ALL AGENTS ──────────────────────────────────────────

# Agent 1: every 2 hours
schedule.every(2).hours.do(safe_run, "Agent 1 — News Ticker", run_news)

# Agent 2: 8:30 AM IST, weekdays only
def run_sentiment_weekday():
    if is_weekday():
        safe_run("Agent 2 — Sentiment", run_sentiment)

schedule.every().day.at("03:00").do(run_sentiment_weekday)  # 03:00 UTC = 08:30 IST

# Agent 3: 8:00 PM IST daily
schedule.every().day.at("14:30").do(  # 14:30 UTC = 20:00 IST
    safe_run, "Agent 3 — Editor-in-Chief", run_editor, "scheduled"
)


# ── STARTUP: RUN AGENT 1 IMMEDIATELY ────────────────────────────

def startup():
    print("\n" + "="*55)
    print("  INDIAN MARKET GURU — Agent System Starting")
    print("="*55)
    print(f"  Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Agent 1 (News Ticker):     every 2 hours")
    print(f"  Agent 2 (Sentiment Score): 08:30 IST weekdays")
    print(f"  Agent 3 (Editor-in-Chief): 20:00 IST daily")
    print("="*55)
    print("\n  Running Agent 1 immediately on startup...")

    # Run news ticker immediately so site has fresh content right away
    thread = threading.Thread(
        target=safe_run,
        args=("Agent 1 — News Ticker (startup)", run_news),
        daemon=True
    )
    thread.start()


# ── MAIN LOOP ────────────────────────────────────────────────────

if __name__ == "__main__":
    startup()

    print("\n  Scheduler running. Press Ctrl+C to stop.\n")

    while True:
        schedule.run_pending()
        time.sleep(30)  # check every 30 seconds
