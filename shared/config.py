import os


def _req(name):
    """Read a required secret from the environment.
    Warns (does not crash) if it's missing, so a misconfiguration is obvious
    in the logs instead of showing up later as a confusing 401."""
    val = os.environ.get(name)
    if not val:
        print(f"[config] WARNING: required secret '{name}' is not set in the environment")
    return val


# ── SECRETS — read from Railway environment variables ONLY (never hardcoded) ──
ANTHROPIC_API_KEY     = _req("ANTHROPIC_API_KEY")
GHOST_ADMIN_API_KEY   = _req("GHOST_ADMIN_API_KEY")
GHOST_CONTENT_API_KEY = _req("GHOST_CONTENT_API_KEY")   # used by Agent 3 (Editor)
SMTP_PASSWORD         = _req("SMTP_PASSWORD")
GITHUB_TOKEN          = _req("GITHUB_TOKEN")
SUPABASE_KEY          = _req("SUPABASE_KEY")
RESEND_API_KEY        = _req("RESEND_API_KEY")

# ── NON-SECRET CONFIG — safe to keep defaults in code ──
GHOST_URL            = os.environ.get("GHOST_URL", "https://indianmarketguru.com")
SUPABASE_URL         = os.environ.get("SUPABASE_URL", "https://bkdjdvzuwiwhcnzeorjx.supabase.co")
SMTP_HOST            = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT            = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER            = os.environ.get("SMTP_USER", "Babi.naren@gmail.com")
NOTIFY_EMAIL         = os.environ.get("NOTIFY_EMAIL", "babi.naren@gmail.com")
APPROVAL_SERVER_PORT = int(os.environ.get("APPROVAL_SERVER_PORT", "8765"))
APPROVAL_SERVER_URL  = os.environ.get("APPROVAL_SERVER_URL", "https://img-agents-production.up.railway.app")

# Gist identifiers are public (they appear in the raw URLs below) — not secrets
GIST_SENTIMENT_ID    = os.environ.get("GIST_SENTIMENT_ID", "949fbb07977169274d51f8b3cf3ff554")
GIST_TICKER_ID       = os.environ.get("GIST_TICKER_ID", "46b3dc4851c52f9c2c170cdab9bcb65b")
GIST_SENTIMENT_URL   = os.environ.get("GIST_SENTIMENT_URL", "https://gist.githubusercontent.com/noobninjaguru/949fbb07977169274d51f8b3cf3ff554/raw/sentiment.json")
GIST_TICKER_URL      = os.environ.get("GIST_TICKER_URL", "https://gist.githubusercontent.com/noobninjaguru/46b3dc4851c52f9c2c170cdab9bcb65b/raw/ticker.json")

NEWS_REFRESH_HOURS = 2
SENTIMENT_TIME     = "03:00"
EDITOR_TIME        = "14:30"

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
