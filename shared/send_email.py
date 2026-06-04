import requests
import os

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "re_3oVSkESd_2JbgtWjxjiE5Ys62C6wyLfsb")
FROM_EMAIL     = "Indian Market Guru <onboarding@resend.dev>"

def send_email(to, subject, html):
    res = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "from":    FROM_EMAIL,
            "to":      [to],
            "subject": subject,
            "html":    html
        }
    )
    if res.status_code in [200, 201]:
        return True
    raise Exception(f"Resend error {res.status_code}: {res.text}")
