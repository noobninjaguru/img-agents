"""
Shared Ghost Admin API helper.
All three agents use this to read and write content on the site.
"""
import jwt
import time
import requests
from datetime import datetime
from shared.config import GHOST_URL, GHOST_ADMIN_API_KEY


def get_ghost_token():
    """Generate a short-lived JWT token for Ghost Admin API."""
    key_id, secret = GHOST_ADMIN_API_KEY.split(":")
    iat = int(time.time())
    payload = {
        "iat": iat,
        "exp": iat + 300,
        "aud": "/admin/"
    }
    token = jwt.encode(payload, bytes.fromhex(secret), algorithm="HS256", headers={"kid": key_id})
    return token


def ghost_headers():
    return {
        "Authorization": f"Ghost {get_ghost_token()}",
        "Content-Type": "application/json"
    }


def get_all_posts(limit=15):
    """Fetch the most recent published posts from Ghost."""
    url = f"{GHOST_URL}/ghost/api/admin/posts/?limit={limit}&status=published&include=tags,authors"
    res = requests.get(url, headers=ghost_headers())
    res.raise_for_status()
    return res.json().get("posts", [])


def get_page_by_slug(slug):
    """Fetch a single page by its slug."""
    url = f"{GHOST_URL}/ghost/api/admin/pages/slug/{slug}/"
    res = requests.get(url, headers=ghost_headers())
    if res.status_code == 404:
        return None
    res.raise_for_status()
    pages = res.json().get("pages", [None])
    return pages[0] if pages else None


def create_or_update_page(slug, title, html_content, status="published"):
    """
    Create or update a Ghost page by slug.
    Used by agents to push data to the site.
    """
    existing = get_page_by_slug(slug)

    payload = {
        "pages": [{
            "title": title,
            "slug": slug,
            "html": html_content,
            "status": status,
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        }]
    }

    if existing:
        page_id = existing["id"]
        payload["pages"][0]["updated_at"] = existing["updated_at"]
        url = f"{GHOST_URL}/ghost/api/admin/pages/{page_id}/"
        res = requests.put(url, json=payload, headers=ghost_headers())
    else:
        url = f"{GHOST_URL}/ghost/api/admin/pages/"
        res = requests.post(url, json=payload, headers=ghost_headers())

    res.raise_for_status()
    return res.json()


def update_site_metadata(key, value):
    """
    Store agent data in a Ghost page used as a data store.
    The site's JavaScript reads this page to get live values.
    """
    slug = f"agent-data-{key}"
    return create_or_update_page(
        slug=slug,
        title=f"[Agent Data] {key}",
        html_content=f"<script type='application/json' id='agent-{key}'>{value}</script>",
        status="published"
    )
def update_gist(gist_id, filename, data):
    """Update a GitHub Gist with new data — readable directly by the website."""
    import requests, json
    from shared.config import GITHUB_TOKEN
    res = requests.patch(
        f'https://api.github.com/gists/{gist_id}',
        json={'files': {filename: {'content': json.dumps(data, indent=2)}}},
        headers={
            'Authorization': f'token {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json'
        }
    )
    res.raise_for_status()
    return res.json()