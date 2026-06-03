"""
AGENT 2 — Sentiment Score Agent
=================================
Runs at 8:30 AM IST every weekday (before market open at 9:15 AM).

1. Reads your NIFTY analysis from the parallel project
2. Reads this morning's classified news from Agent 1
3. Generates a sentiment score from -50 to +50
4. Emails you a summary with an APPROVE or ADJUST button
5. When you click Approve → score goes live on the website
6. When you click Adjust → you reply with your own score
"""

import json
import anthropic
import smtplib
import threading
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import HTTPServer, BaseHTTPRequestHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

from shared.config import (
    ANTHROPIC_API_KEY, SMTP_HOST, SMTP_PORT,
    SMTP_USER, SMTP_PASSWORD, NOTIFY_EMAIL,
    APPROVAL_SERVER_PORT, GHOST_URL
)
from shared.ghost_api import update_site_metadata, get_all_posts


# ── STEP 1: GATHER INPUTS ─────────────────────────────────────────

def get_latest_nifty_analysis():
    """
    Read the most recent NIFTY analysis post from Ghost.
    This is where your parallel project's analysis lives.
    """
    posts = get_all_posts(limit=5)
    nifty_posts = [p for p in posts if "nifty" in p.get("title", "").lower()]
    if nifty_posts:
        # Return plain text of the most recent NIFTY post
        post = nifty_posts[0]
        return {
            "title": post["title"],
            "excerpt": post.get("custom_excerpt", ""),
            "content": post.get("plaintext", "")[:3000],  # first 3000 chars
        }
    return None

def get_mornings_news():
    import os
    try:
        local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shared', 'latest_news.json')
        with open(local_path, 'r') as f:
            data = json.load(f)
        return data.get("headlines", [])
    except Exception as e:
        print(f"  Could not read local news: {e}")
        return []


# ── STEP 2: GENERATE SCORE WITH CLAUDE ───────────────────────────

def generate_sentiment_score(analysis, headlines):
    """Ask Claude to synthesise everything into a score."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    positive_news = [h["title"] for h in headlines if h.get("sentiment") == "positive"]
    negative_news = [h["title"] for h in headlines if h.get("sentiment") == "negative"]
    neutral_news  = [h["title"] for h in headlines if h.get("sentiment") == "neutral"]

    analysis_text = ""
    if analysis:
        analysis_text = f"LATEST NIFTY ANALYSIS:\nTitle: {analysis['title']}\n{analysis['content'][:1500]}"

    prompt = f"""You are a quantitative market analyst for Indian Market Guru.

Produce a sentiment score for NIFTY 50 today from -50 to +50.

Scale:
+40 to +50 = Strongly bullish
+20 to +39 = Cautiously bullish
-19 to +19 = Neutral
-20 to -39 = Cautiously bearish
-40 to -50 = Strongly bearish

{analysis_text}

TODAY'S NEWS:
Positive ({len(positive_news)}): {', '.join(positive_news[:5])}
Negative ({len(negative_news)}): {', '.join(negative_news[:5])}
Neutral ({len(neutral_news)}): {', '.join(neutral_news[:3])}

Respond with ONLY a JSON object, no markdown, no explanation:
{{"score": 25, "label": "Cautiously bullish", "reasoning": "explanation here", "key_risks": ["risk1", "risk2", "risk3"], "key_positives": ["pos1", "pos2", "pos3"]}}"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    print(f"  Claude raw response: {raw[:150]}")
    start = raw.find('{')
    end = raw.rfind('}') + 1
    if start != -1:
        raw = raw[start:end]
    return json.loads(raw)

# ── STEP 3: SEND APPROVAL EMAIL ───────────────────────────────────

def send_approval_email(score_data):
    """
    Email the score to Narendra with Approve / Adjust options.
    Approve link triggers the approval server to publish the score.
    """
    score      = score_data["score"]
    label      = score_data["label"]
    reasoning  = score_data["reasoning"]
    risks      = score_data.get("key_risks", [])
    positives  = score_data.get("key_positives", [])
    date_str   = datetime.now().strftime("%A, %d %B %Y")

    from shared.config import APPROVAL_SERVER_URL
    approve_url = f"{APPROVAL_SERVER_URL}/approve?score={score}&label={label.replace(' ', '+')}"
    today       = datetime.now().strftime("%Y%m%d")

    score_colour = "#22c55e" if score > 0 else "#ef4444" if score < 0 else "#94a3b8"
    needle_pct   = ((score + 50) / 100) * 100  # convert -50/+50 to 0-100%

    html = f"""
<div style="font-family:Georgia,serif;max-width:600px;margin:0 auto;color:#1a1a1a">
  <div style="background:#0a0a0a;padding:20px 24px;margin-bottom:0">
    <div style="font-family:'Courier New',monospace;font-size:10px;letter-spacing:.15em;
                color:rgba(255,255,255,.4);text-transform:uppercase;margin-bottom:6px">
      Indian Market Guru · Agent 2 · {date_str}
    </div>
    <div style="font-size:22px;font-weight:700;color:#fff;font-family:Georgia,serif">
      Morning Sentiment Report
    </div>
  </div>

  <div style="border:1px solid #e2e2e2;border-top:none;padding:28px 24px">

    <div style="text-align:center;margin-bottom:28px;padding:24px;background:#f8f8f6;border:1px solid #e2e2e2">
      <div style="font-family:'Courier New',monospace;font-size:10px;letter-spacing:.12em;
                  text-transform:uppercase;color:#999;margin-bottom:10px">AI-Generated Score</div>
      <div style="font-size:56px;font-weight:700;color:{score_colour};font-family:Georgia,serif;line-height:1">
        {'+' if score > 0 else ''}{score}
      </div>
      <div style="font-size:16px;color:#333;margin-top:6px;font-style:italic">{label}</div>
      
      <!-- Mini sentiment bar -->
      <div style="margin:16px auto 0;max-width:300px">
        <div style="height:8px;border-radius:2px;background:linear-gradient(to right,#7f1d1d,#ef4444,#fca5a5,#e8e4dc,#86efac,#22c55e,#14532d);position:relative">
          <div style="position:absolute;top:-4px;left:{needle_pct}%;width:3px;height:16px;
                      background:#0a0a0a;border-radius:1px;transform:translateX(-50%)"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-family:'Courier New',monospace;
                    font-size:9px;color:#999;margin-top:4px">
          <span>Strongly bearish</span><span>Strongly bullish</span>
        </div>
      </div>
    </div>

    <p style="font-size:14px;line-height:1.7;color:#333;margin-bottom:20px">
      <strong>Reasoning:</strong> {reasoning}
    </p>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:28px">
      <div style="border-left:3px solid #22c55e;padding-left:12px">
        <div style="font-family:'Courier New',monospace;font-size:9px;letter-spacing:.1em;
                    text-transform:uppercase;color:#999;margin-bottom:6px">Key positives</div>
        {''.join(f'<div style="font-size:12px;color:#333;margin-bottom:4px">+ {p}</div>' for p in positives)}
      </div>
      <div style="border-left:3px solid #ef4444;padding-left:12px">
        <div style="font-family:'Courier New',monospace;font-size:9px;letter-spacing:.1em;
                    text-transform:uppercase;color:#999;margin-bottom:6px">Key risks</div>
        {''.join(f'<div style="font-size:12px;color:#333;margin-bottom:4px">- {r}</div>' for r in risks)}
      </div>
    </div>

    <div style="background:#0a0a0a;padding:20px 24px;text-align:center">
      <div style="font-family:'Courier New',monospace;font-size:10px;color:rgba(255,255,255,.4);
                  margin-bottom:14px;letter-spacing:.06em">YOUR DECISION</div>
      <a href="{approve_url}" style="display:inline-block;background:#22c55e;color:#fff;
         padding:12px 32px;font-family:'Courier New',monospace;font-size:11px;
         letter-spacing:.08em;text-decoration:none;margin-right:10px">
        ✓ APPROVE &amp; PUBLISH SCORE
      </a>
      <a href="mailto:{NOTIFY_EMAIL}?subject=Score+override+{today}&body=My+score+for+today+is:+[enter -50 to +50]%0ALable: [your label]" 
         style="display:inline-block;background:transparent;color:#fff;
         padding:12px 32px;font-family:'Courier New',monospace;font-size:11px;
         letter-spacing:.08em;text-decoration:none;border:1px solid rgba(255,255,255,.3)">
        ✎ ADJUST SCORE
      </a>
    </div>

    <div style="margin-top:16px;font-family:'Courier New',monospace;font-size:10px;
                color:#bbb;text-align:center;line-height:1.6">
      Click Approve to publish this score to indianmarketguru.com<br>
      Click Adjust to send yourself an email and reply with your preferred score
    </div>

  </div>
</div>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[IMG] Morning Sentiment: {'+' if score > 0 else ''}{score} ({label}) — {date_str}"
    msg["From"]    = SMTP_USER
    msg["To"]      = NOTIFY_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL(SMTP_HOST, 465) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    print(f"  ✓ Approval email sent to {NOTIFY_EMAIL}")


# ── STEP 4: APPROVAL SERVER ───────────────────────────────────────

class ApprovalHandler(BaseHTTPRequestHandler):
    """
    Tiny local HTTP server that listens for your approval click.
    When you click 'Approve' in the email, this receives the request
    and publishes the score to Ghost.
    """
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/approve":
            params = parse_qs(parsed.query)
            score  = int(params.get("score", [0])[0])
            label  = params.get("label", ["Neutral"])[0].replace("+", " ")
            publish_score(score, label)

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(f"""
            <html><body style="font-family:Georgia;text-align:center;padding:60px;background:#f8f8f6">
            <h1 style="font-size:48px;color:#22c55e">✓</h1>
            <h2>Score published</h2>
            <p>Today's sentiment score of <strong>{'+' if score > 0 else ''}{score} ({label})</strong>
            is now live on indianmarketguru.com</p>
            </body></html>
            """.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress server logs


def start_approval_server():
    """Run the approval server in a background thread."""
    server = HTTPServer(("localhost", APPROVAL_SERVER_PORT), ApprovalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"  ✓ Approval server listening on port {APPROVAL_SERVER_PORT}")
    return server


def publish_score(score, label):
    from shared.ghost_api import update_gist
    from shared.config import GIST_SENTIMENT_ID
    import json

    needle_pct = ((score + 50) / 100) * 100
    label_colour = {
        "Strongly bullish":   "#15803d",
        "Cautiously bullish": "#16a34a",
        "Neutral":            "#94a3b8",
        "Cautiously bearish": "#dc2626",
        "Strongly bearish":   "#991b1b",
    }.get(label, "#94a3b8")

    data = {
        "score":        score,
        "label":        label,
        "needle_pct":   round(needle_pct, 1),
        "label_colour": label_colour,
        "updated_at":   datetime.now(timezone.utc).isoformat()
    }

    update_gist(GIST_SENTIMENT_ID, "sentiment.json", data)
    print(f"  Score published: {'+' if score > 0 else ''}{score} ({label})")

# ── MAIN ─────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*50}")
    print(f"AGENT 2 — Sentiment Score  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
    print(f"{'='*50}")

    print("\n[1/4] Starting approval server...")
    start_approval_server()

    print("\n[2/4] Gathering inputs...")
    analysis  = get_latest_nifty_analysis()
    headlines = get_mornings_news()
    print(f"  ✓ Analysis: {'found' if analysis else 'not found'}")
    print(f"  ✓ Headlines: {len(headlines)} from Agent 1")

    print("\n[3/4] Generating sentiment score with Claude...")
    score_data = generate_sentiment_score(analysis, headlines)
    score      = score_data['score']
    label      = score_data['label']
    print(f"  ✓ Score: {'+' if score > 0 else ''}{score} — {label}")
    print(f"  ✓ Reasoning: {score_data['reasoning'][:100]}...")

    print("\n[4/4] Sending approval email...")
    send_approval_email(score_data)

    print(f"\n✓ Agent 2 complete. Waiting for your approval email click.\n")

    # Keep approval server alive for 2 hours
    import time
    time.sleep(7200)


if __name__ == "__main__":
    run()
