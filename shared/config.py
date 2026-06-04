import os

ANTHROPIC_API_KEY    = os.environ.get("ANTHROPIC_API_KEY", "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")
GHOST_URL            = os.environ.get("GHOST_URL", "https://www.indianmarketguru.com")
GHOST_ADMIN_API_KEY  = os.environ.get("GHOST_ADMIN_API_KEY", "6a2127f3701a1c00015c3c84:3170761d2bfb80ad23f6ce7bcb3c9b5803ea2b9ce5ca75eb412fc5d677ed2135")
SMTP_HOST            = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT            = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER            = os.environ.get("SMTP_USER", "Babi.naren@gmail.com")
SMTP_PASSWORD        = os.environ.get("SMTP_PASSWORD", "shshktglfsewwxxl")
NOTIFY_EMAIL         = os.environ.get("NOTIFY_EMAIL", "babi.naren@gmail.com")
APPROVAL_SERVER_PORT = int(os.environ.get("APPROVAL_SERVER_PORT", "8765"))
APPROVAL_SERVER_URL  = os.environ.get("APPROVAL_SERVER_URL", "https://img-agents-production.up.railway.app")
GITHUB_TOKEN         = os.environ.get("GITHUB_TOKEN", "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")
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
SUPABASE_URL         = os.environ.get("SUPABASE_URL", "https://bkdjdvzuwiwhcnzeorjx.supabase.co")
SUPABASE_KEY         = os.environ.get("SUPABASE_KEY", "sb_secret_akIwQp4Ynze6tVinoEFgOg_qGQ08xxQ")
RESEND_API_KEY       = os.environ.get("RESEND_API_KEY", "re_3oVSkESd_2JbgtWjxjiE5Ys62C6wyLfsb")
