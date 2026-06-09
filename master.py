"""
MASTER SCHEDULER — Indian Market Guru Agent System
====================================================
Manages all six agents:

  Agent 1 — News Ticker:       every 2 hours, all day
  Agent 2 — Sentiment Score:   8:30 AM IST, weekdays only
  Agent 3 — Editor-in-Chief:   8:00 PM IST daily
  Agent 4 — Content Writer:    8:00 AM IST weekdays, 9:00 AM weekends
  Agent 5 — Morning Analysis:  9:20 AM IST weekdays
  Agent 6 — Nifty Live Price:  continuous background thread

Each scheduled agent runs in its OWN thread (via run_threaded), so a
long-running or blocking agent — e.g. Agent 2's approval wait — can never
stall the main scheduler loop or hold up the agents queued behind it.
(That blocking is what was delaying Agent 5, the 9:20 morning report, by
up to two hours.)

Start with:  python master.py
Stop with:   Ctrl+C
"""

import schedule
import time
import threading
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from shared.approval_server import start as start_flask

from agent1_news.news_ticker_agent    import run as run_news
from agent2_sentiment.sentiment_agent import run as run_sentiment
from agent3_editor.editor_agent       import run as run_editor
from agent4_content.content_agent     import run as run_content
from agent5_morning.morning_agent     import run as run_morning
from agent6_price.price_agent         import run as run_price
from agent8_data.data_agent           import run_data_weekday


def safe_run(agent_name, func, *args):
    try:
        print(f"\n▶ Starting {agent_name}...")
        func(*args)
    except Exception as e:
        print(f"\n✗ {agent_name} failed: {e}")
        import traceback
        traceback.print_exc()


def run_threaded(job_func, *args):
    """Launch a scheduled job in its own daemon thread.

    The main loop calls this, it returns instantly after spawning the
    thread, and the actual work happens off the main loop. This is what
    stops one blocking agent (Agent 2's 2-hour approval wait) from
    freezing the scheduler and delaying everything behind it.
    """
    threading.Thread(target=job_func, args=args, daemon=True).start()


def is_weekday():
    return datetime.now().weekday() < 5


# ── SCHEDULES ────────────────────────────────────────────────────
# Every job is dispatched through run_threaded(...) so each agent runs
# in its own thread. The main loop only spawns threads; it never blocks.

# Agent 1: every 2 hours
schedule.every(2).hours.do(run_threaded, safe_run, "Agent 1 — News Ticker", run_news)

# Agent 2: 8:30 AM IST weekdays (03:00 UTC)
def run_sentiment_weekday():
    if is_weekday():
        safe_run("Agent 2 — Sentiment", run_sentiment)
schedule.every().day.at("03:00").do(run_threaded, run_sentiment_weekday)

# Agent 3: 8:00 PM IST daily (14:30 UTC)
schedule.every().day.at("14:30").do(
    run_threaded, safe_run, "Agent 3 — Editor-in-Chief", run_editor, "scheduled")

# Agent 4: 8:00 AM IST weekdays (02:30 UTC), 9:00 AM IST weekends (03:30 UTC)
def run_content_weekday():
    if datetime.now().weekday() < 5:
        safe_run("Agent 4 — Content Writer", run_content, "scheduled")

def run_content_weekend():
    if datetime.now().weekday() >= 5:
        safe_run("Agent 4 — Content Writer", run_content, "scheduled")

schedule.every().day.at("02:30").do(run_threaded, run_content_weekday)
schedule.every().day.at("03:30").do(run_threaded, run_content_weekend)

# Agent 5: 9:20 AM IST weekdays (03:50 UTC)
def run_morning_weekday():
    if is_weekday():
        safe_run("Agent 5 — Morning Analysis", run_morning, "scheduled")
schedule.every().day.at("03:50").do(run_threaded, run_morning_weekday)

# Agent 8: 4:00 PM IST weekdays (10:30 UTC) — download today's bars to Supabase
schedule.every().day.at("10:30").do(run_threaded, run_data_weekday)


# ── STARTUP ──────────────────────────────────────────────────────

def startup():
    # Flask approval server
    start_flask()

    print("\n" + "="*55)
    print("  INDIAN MARKET GURU — Agent System Starting")
    print("="*55)
    print(f"  Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Agent 1 (News Ticker):      every 2 hours")
    print(f"  Agent 2 (Sentiment Score):  08:30 IST weekdays")
    print(f"  Agent 3 (Editor-in-Chief):  20:00 IST daily")
    print(f"  Agent 4 (Content Writer):   08:00 IST weekdays / 09:00 IST weekends")
    print(f"  Agent 5 (Morning Analysis): 09:20 IST weekdays")
    print(f"  Agent 6 (Nifty Price):      continuous background thread")
    print(f"  Agent 8 (Daily Data):       16:00 IST weekdays")
    print("="*55)

    # Agent 6: start live price pusher in background thread
    price_thread = threading.Thread(target=run_price, daemon=True)
    price_thread.start()
    print("  Agent 6 — Nifty Live Price: started in background")

    # Agent 1: run immediately on startup
    print("\n  Running Agent 1 immediately on startup...")
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
        time.sleep(30)
