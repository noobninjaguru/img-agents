"""
AGENT 3 — Editor-in-Chief Agent
=================================
Runs daily at 8 PM IST, and also whenever a new post is published
(via Ghost webhook).

Acts as a critical reader from your target audience — an educated
Indian retail investor or active NIFTY trader, 28-45 years old,
financially literate, skeptical of vague claims.

Audits the entire site holistically:
- Content quality and depth of each post
- Consistency of tone and editorial voice
- Whether the signals section and blog are cohesive
- Factual gaps, unsubstantiated claims, missing context
- What a paying subscriber would love vs. what would annoy them

Sends a private report to your email — never publishes anything.
"""

import json
import anthropic
import smtplib
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from shared.config import (
    ANTHROPIC_API_KEY, SMTP_HOST, SMTP_PORT,
    SMTP_USER, SMTP_PASSWORD, NOTIFY_EMAIL, GHOST_URL
)
from shared.ghost_api import get_all_posts


# ── STEP 1: GATHER SITE CONTENT ──────────────────────────────────

def gather_site_content():
    """Fetch recent posts using the public Content API — no auth needed."""
    import requests
    from shared.config import GHOST_URL
    
    CONTENT_KEY = "aef48258333e3c052c6a02ae54"
    url = f"{GHOST_URL}/ghost/api/content/posts/?key={CONTENT_KEY}&limit=10&include=tags"
    res = requests.get(url)
    res.raise_for_status()
    posts = res.json().get("posts", [])
    return posts


# ── STEP 2: RUN THE AUDIT ─────────────────────────────────────────

def run_audit(posts, trigger="scheduled"):
    """
    Send all content to Claude with the Editor-in-Chief persona.
    Returns a structured audit report.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    posts_text = ""
    for i, post in enumerate(posts):
        posts_text += f"""
POST {i+1}: {post['title']}
Published: {post.get('published_at', 'unknown')} | Tags: {', '.join([t['name'] for t in post.get('tags', [])])}
Excerpt: {post.get('excerpt') or post.get('custom_excerpt') or 'No excerpt'}
Content preview:
{post.get('excerpt', post.get('custom_excerpt', 'No content available'))[:1000]}
{'—'*40}
"""

    prompt = f"""You are the Editor-in-Chief of Indian Market Guru, a premium NIFTY analysis 
and trading signals website targeting educated Indian retail investors and active NIFTY traders.

Your typical reader:
- Age 28-45, financially literate, trades NIFTY options or invests in index funds
- Has tried multiple advisory services and is skeptical of vague claims
- Pays ₹499-3,999/month — they expect institutional-quality analysis
- Reads Economic Times, follows FII data, understands basic options greeks
- Will cancel immediately if content feels generic, lazy, or unsubstantiated

Your job: conduct a rigorous editorial audit of the site's recent content.
Be honest, specific, and constructively critical. Do not be diplomatic about problems.

SITE CONTENT TO AUDIT:
{posts_text}

SITE CONTEXT:
- Homepage shows live NIFTY price, scrolling news ticker, and sentiment bar
- Signals section sells three tiers: Signal I (₹799), Signal II (₹1,999), Signal III (₹3,999)
- Pro subscribers get a nightly post-market PDF report
- Brand positioning: NYT-style editorial authority, data-driven, India-first

Audit trigger: {trigger}

Respond with a JSON object. No markdown, no preamble, valid JSON only.

{{
  "overall_score": <1-10, where 10 is exceptional>,
  "overall_verdict": "<one sentence summary of the site's current editorial state>",
  
  "strengths": [
    "<specific strength 1 with example from content>",
    "<specific strength 2>",
    "<specific strength 3>"
  ],
  
  "critical_issues": [
    {{
      "issue": "<specific problem>",
      "post": "<which post or section>",
      "impact": "<why this hurts the reader experience>",
      "fix": "<specific actionable fix>"
    }}
  ],
  
  "post_by_post": [
    {{
      "title": "<post title>",
      "score": <1-10>,
      "best_part": "<what works>",
      "weakest_part": "<what doesn't>",
      "reader_reaction": "<how a paying subscriber would feel reading this>"
    }}
  ],
  
  "coherence_check": {{
    "signals_blog_alignment": "<are the blog posts building credibility for the signals product?>",
    "tone_consistency": "<is the voice consistent across posts?>",
    "content_gaps": ["<missing content type 1>", "<missing content type 2>"]
  }},
  
  "top_3_priorities": [
    "<highest priority fix — do this first>",
    "<second priority>",
    "<third priority>"
  ],
  
  "what_a_subscriber_would_say": "<honest 2-3 sentence reaction from a paying ₹1,999/month subscriber>"
}}
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1:
        raw = raw[start:end]
    return json.loads(raw)

# ── STEP 3: FORMAT AND SEND EMAIL REPORT ─────────────────────────

def send_editor_report(audit, posts):
    """Send the audit as a well-formatted email to Narendra."""

    score         = audit.get("overall_score", "—")
    verdict       = audit.get("overall_verdict", "")
    strengths     = audit.get("strengths", [])
    issues        = audit.get("critical_issues", [])
    post_reviews  = audit.get("post_by_post", [])
    coherence     = audit.get("coherence_check", {})
    priorities    = audit.get("top_3_priorities", [])
    subscriber    = audit.get("what_a_subscriber_would_say", "")
    date_str      = datetime.now().strftime("%A, %d %B %Y")

    score_colour  = "#22c55e" if score >= 7 else "#f59e0b" if score >= 5 else "#ef4444"

    def section(title, content_html, border_colour="#0a0a0a"):
        return f"""
<div style="margin-bottom:24px;border-left:3px solid {border_colour};padding-left:16px">
  <div style="font-family:'Courier New',monospace;font-size:9px;letter-spacing:.12em;
              text-transform:uppercase;color:#999;margin-bottom:8px">{title}</div>
  {content_html}
</div>
"""

    # Build post-by-post reviews
    post_reviews_html = ""
    for pr in post_reviews:
        sc  = pr.get("score", 0)
        col = "#22c55e" if sc >= 7 else "#f59e0b" if sc >= 5 else "#ef4444"
        post_reviews_html += f"""
<div style="border:1px solid #e2e2e2;padding:12px 14px;margin-bottom:10px">
  <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
    <strong style="font-size:13px">{pr.get('title','')}</strong>
    <span style="font-family:'Courier New',monospace;font-size:13px;font-weight:700;color:{col}">{sc}/10</span>
  </div>
  <div style="font-size:12px;color:#555;margin-bottom:4px">✓ {pr.get('best_part','')}</div>
  <div style="font-size:12px;color:#555;margin-bottom:4px">✗ {pr.get('weakest_part','')}</div>
  <div style="font-size:12px;color:#888;font-style:italic">Subscriber reaction: {pr.get('reader_reaction','')}</div>
</div>
"""

    # Build critical issues
    issues_html = ""
    for issue in issues:
        issues_html += f"""
<div style="border:1px solid #fecaca;background:#fef2f2;padding:12px 14px;margin-bottom:10px;border-radius:4px">
  <div style="font-size:13px;font-weight:600;color:#991b1b;margin-bottom:4px">{issue.get('issue','')}</div>
  <div style="font-size:11px;color:#666;font-family:'Courier New',monospace;margin-bottom:4px">
    POST: {issue.get('post','')}
  </div>
  <div style="font-size:12px;color:#555;margin-bottom:4px">Impact: {issue.get('impact','')}</div>
  <div style="font-size:12px;color:#166534;font-weight:500">Fix: {issue.get('fix','')}</div>
</div>
"""

    html = f"""
<div style="font-family:Georgia,serif;max-width:680px;margin:0 auto;color:#1a1a1a">
  <div style="background:#0a0a0a;padding:20px 24px">
    <div style="font-family:'Courier New',monospace;font-size:10px;letter-spacing:.15em;
                color:rgba(255,255,255,.4);text-transform:uppercase;margin-bottom:6px">
      Indian Market Guru · Editor-in-Chief Agent · {date_str}
    </div>
    <div style="font-size:22px;font-weight:700;color:#fff">Editorial Audit Report</div>
  </div>

  <div style="border:1px solid #e2e2e2;border-top:none;padding:28px 24px">
    
    <!-- Overall Score -->
    <div style="text-align:center;padding:20px;background:#f8f8f6;border:1px solid #e2e2e2;margin-bottom:28px">
      <div style="font-family:'Courier New',monospace;font-size:9px;letter-spacing:.12em;
                  text-transform:uppercase;color:#999;margin-bottom:8px">Overall Site Score</div>
      <div style="font-size:52px;font-weight:700;color:{score_colour};font-family:Georgia,serif;line-height:1">
        {score}<span style="font-size:24px;color:#ccc">/10</span>
      </div>
      <div style="font-size:14px;color:#333;margin-top:8px;font-style:italic">{verdict}</div>
    </div>

    <!-- What a subscriber would say -->
    {section("What a paying subscriber would say", 
      f'<p style="font-size:13px;line-height:1.7;color:#333;font-style:italic;border-left:2px solid #e2e2e2;padding-left:12px">"{subscriber}"</p>',
      "#0a0a0a")}

    <!-- Top 3 priorities -->
    {section("Your top 3 priorities this week",
      ''.join(f'<div style="display:flex;gap:10px;margin-bottom:8px"><span style="font-family:Courier New,monospace;font-size:11px;background:#0a0a0a;color:#fff;padding:2px 8px;flex-shrink:0">{i+1}</span><span style="font-size:13px;line-height:1.5">{p}</span></div>' for i,p in enumerate(priorities)),
      "#0a0a0a")}

    <!-- Critical issues -->
    {section("Critical issues to fix", issues_html or "<p style='font-size:13px;color:#22c55e'>No critical issues found.</p>", "#ef4444")}

    <!-- Strengths -->
    {section("What's working well",
      ''.join(f'<div style="font-size:13px;margin-bottom:6px;color:#333">✓ {s}</div>' for s in strengths),
      "#22c55e")}

    <!-- Post by post -->
    {section("Post-by-post review", post_reviews_html, "#6366f1")}

    <!-- Coherence -->
    {section("Site coherence",
      f'''<div style="font-size:12px;margin-bottom:8px"><strong>Signals + blog alignment:</strong> {coherence.get("signals_blog_alignment","")}</div>
      <div style="font-size:12px;margin-bottom:8px"><strong>Tone consistency:</strong> {coherence.get("tone_consistency","")}</div>
      <div style="font-size:12px"><strong>Content gaps:</strong> {", ".join(coherence.get("content_gaps",[]))}</div>''',
      "#94a3b8")}

  </div>

  <div style="padding:12px 24px;background:#f8f8f6;border:1px solid #e2e2e2;border-top:none;
              font-family:'Courier New',monospace;font-size:10px;color:#aaa;text-align:center">
    Generated by Agent 3 — Editor-in-Chief · Indian Market Guru Agent System<br>
    This report is private and is never published to the site.
  </div>
</div>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[IMG Editor] Site Audit: {score}/10 — {date_str}"
    msg["From"]    = SMTP_USER
    msg["To"]      = NOTIFY_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    print(f"  ✓ Editor report sent to {NOTIFY_EMAIL}")


# ── MAIN ─────────────────────────────────────────────────────────

def run(trigger="scheduled"):
    print(f"\n{'='*50}")
    print(f"AGENT 3 — Editor-in-Chief  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
    print(f"Trigger: {trigger}")
    print(f"{'='*50}")

    print("\n[1/3] Gathering site content...")
    posts = gather_site_content()

    if not posts:
        print("  ✗ No posts found. Aborting.")
        return

    print(f"\n[2/3] Running editorial audit on {len(posts)} posts...")
    audit = run_audit(posts, trigger)
    score = audit.get("overall_score", "—")
    print(f"  ✓ Audit complete. Overall score: {score}/10")

    print("\n[3/3] Sending report to your email...")
    send_editor_report(audit, posts)

    print(f"\n✓ Agent 3 complete.\n")


if __name__ == "__main__":
    trigger = sys.argv[1] if len(sys.argv) > 1 else "scheduled"
    run(trigger)
