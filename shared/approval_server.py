"""
Approval Server — Flask-based
==============================
Runs on PORT env var (Railway sets this automatically).
Handles /approve route when Narendra clicks the email button.
"""

import os
import json
import threading
from flask import Flask, request, jsonify
from datetime import datetime, timezone

app = Flask(__name__)

# Store pending score in memory
_pending = {}

def set_pending(score_data):
    """Called by Agent 2 to register the pending score."""
    global _pending
    _pending = score_data

@app.route("/approve")
def approve():
    from shared.ghost_api import update_gist
    from shared.config import GIST_SENTIMENT_ID

    score = int(request.args.get("score", 0))
    label = request.args.get("label", "Neutral").replace("+", " ")

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

    score_str = f"{'+' if score > 0 else ''}{score}"
    print(f"  ✓ Score published via approval: {score_str} ({label})")

    return f"""
<html>
<body style="font-family:Georgia;text-align:center;padding:60px;background:#f8f8f6">
  <h1 style="font-size:48px;color:#22c55e">✓</h1>
  <h2>Score published</h2>
  <p>Today's sentiment score of <strong>{score_str} ({label})</strong>
  is now live on indianmarketguru.com</p>
</body>
</html>
"""

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})

def start(threaded=True):
    """Start Flask server. Uses PORT env var (Railway) or 8080 locally."""
    port = int(os.environ.get("PORT", 8080))
    print(f"  ✓ Approval server running on port {port}")
    if threaded:
        t = threading.Thread(
            target=lambda: app.run(host="0.0.0.0", port=port, debug=False),
            daemon=True
        )
        t.start()
    else:
        app.run(host="0.0.0.0", port=port, debug=False)
