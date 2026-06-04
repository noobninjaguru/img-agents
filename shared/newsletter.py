"""
shared/newsletter.py — Newsletter sender
"""
import requests
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.config import SUPABASE_URL, SUPABASE_KEY, RESEND_API_KEY

FROM_EMAIL = "Indian Market Guru <newsletter@indianmarketguru.com>"
SITE_URL   = "https://www.indianmarketguru.com"

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }

def get_subscribers():
    res = requests.get(
        f"{SUPABASE_URL}/rest/v1/subscribers?active=eq.true&select=email,name",
        headers=sb_headers(),
        timeout=10
    )
    if res.status_code == 200:
        return res.json()
    return []

def send_newsletter(title, html_content, post_url, tags):
    subscribers = get_subscribers()
    if not subscribers:
        print("  No subscribers yet — skipping newsletter")
        return 0

    tag = tags[0] if tags else "Daily Brief"
    tag_color = {"Daily Brief": "#0a0a0a", "Weekly Wrap": "#2563eb", "Editorial": "#dc2626"}.get(tag, "#0a0a0a")

    email_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f4f0;font-family:Georgia,serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f0;padding:32px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        <tr><td style="background:#0a0a0a;padding:24px 32px;border-radius:8px 8px 0 0;">
          <table width="100%" cellpadding="0" cellspacing="0"><tr>
            <td>
              <div style="font-family:Georgia,serif;font-size:11px;color:rgba(255,255,255,0.4);letter-spacing:3px;margin-bottom:4px;">INDIAN</div>
              <div style="font-family:Georgia,serif;font-size:22px;font-weight:700;color:#ffffff;">Market Guru</div>
            </td>
            <td align="right"><span style="background:{tag_color};color:#fff;font-family:'Courier New',monospace;font-size:9px;letter-spacing:1.5px;padding:4px 10px;border-radius:3px;">{tag.upper()}</span></td>
          </tr></table>
        </td></tr>
        <tr><td style="background:#ffffff;padding:32px;">
          <h1 style="font-family:Georgia,serif;font-size:24px;font-weight:700;color:#0a0a0a;line-height:1.3;margin:0 0 24px 0;">{title}</h1>
          <div style="font-family:Georgia,serif;font-size:15px;line-height:1.7;color:#333;">{html_content[:2000]}...</div>
          <div style="margin-top:28px;">
            <a href="{post_url}" style="background:#0a0a0a;color:#ffffff;font-family:'Courier New',monospace;font-size:11px;letter-spacing:1.5px;padding:12px 24px;text-decoration:none;border-radius:4px;display:inline-block;">READ FULL ANALYSIS →</a>
          </div>
        </td></tr>
        <tr><td style="background:#f8f8f6;padding:20px 32px;border-radius:0 0 8px 8px;border-top:1px solid #e8e8e4;">
          <p style="font-family:'Courier New',monospace;font-size:9px;color:#aaa;letter-spacing:1px;margin:0;">
            NIFTY ANALYSIS · DATA-DRIVEN SIGNALS · INDEPENDENT RESEARCH<br><br>
            You're receiving this because you subscribed at <a href="{SITE_URL}" style="color:#aaa;">{SITE_URL}</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    sent = 0
    failed = 0
    for sub in subscribers:
        try:
            res = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={"from": FROM_EMAIL, "to": [sub["email"]], "subject": title, "html": email_html},
                timeout=15
            )
            if res.status_code in [200, 201]:
                sent += 1
            else:
                failed += 1
                print(f"  ✗ Failed to send to {sub['email']}: {res.text[:100]}")
        except Exception as e:
            failed += 1
            print(f"  ✗ Error sending to {sub['email']}: {e}")

    print(f"  ✓ Newsletter sent to {sent} subscribers ({failed} failed)")
    return sent
