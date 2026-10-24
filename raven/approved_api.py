"""RAVEN official Reddit Data API adapter, hard-gated until explicitly approved.

No uncredentialed fallback, cookies, scraping workaround, write methods, private APIs,
token logging, model training, or background polling.
"""
from __future__ import annotations
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

API_ORIGIN = "https://oauth.reddit.com"
MAX_RESPONSE_BYTES = 700_000
APPROVAL_PATH = Path(__file__).resolve().parent / "state" / "reddit_api_approval.json"
APPROVED_SUBS = frozenset({"betareadersforai"})
POST_ID = re.compile(r"^[a-z0-9]{3,15}$", re.I)

class AccessNotApproved(PermissionError): pass
class APIBlocked(RuntimeError): pass
class APIRateLimited(RuntimeError): pass

@dataclass(frozen=True)
class APIConfig:
    app_id: str
    access_token: str
    user_agent: str
    owner_consent: bool

    def __repr__(self) -> str:
        return "APIConfig(app_id='[redacted]', access_token='[redacted]', user_agent='[redacted]', owner_consent=%s)" % self.owner_consent

def approval_status(path: Path = APPROVAL_PATH) -> bool:
    """Approval evidence is a LOCAL administrative guard, not proof of Reddit approval."""
    try:
        source = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError,ValueError,TypeError):
        return False
    return bool(source.get("reddit_explicit_approval") is True
                and source.get("owner_approved_read_only") is True
                and source.get("use_case") == "nexa-volunteer-beta-research"
                and isinstance(source.get("reddit_approval_reference"),str)
                and len(source["reddit_approval_reference"].strip()) >= 8)

def from_environment(env: dict | None = None, approval_path: Path = APPROVAL_PATH) -> APIConfig:
    env = os.environ if env is None else env
    if not approval_status(approval_path):
        raise AccessNotApproved("Reddit Data API approval and owner consent not recorded; no request made")
    app_id = env.get("RAVEN_REDDIT_APP_ID","")
    token = env.get("RAVEN_REDDIT_OAUTH_TOKEN","")
    agent = env.get("RAVEN_REDDIT_USER_AGENT","")
    if not app_id or not token or not agent:
        raise AccessNotApproved("Registered app ID, OAuth token, and User-Agent required in environment")
    if len(token)>4096 or any(ord(c)<33 or ord(c)>126 for c in token):
        raise AccessNotApproved("Invalid token format")
    if not re.fullmatch(r"[A-Za-z0-9_-]{5,120}",app_id):
        raise AccessNotApproved("Invalid registered app ID")
    if len(agent)>220 or not agent.startswith("windows:") or "by /u/Historical-Wheel-749" not in agent:
        raise AccessNotApproved("Authentic, registered descriptive User-Agent is required")
    return APIConfig(app_id=app_id,access_token=token,user_agent=agent,owner_consent=True)

def approved_url(subreddit: str, post_id: str, *, limit: int = 40) -> str:
    if not isinstance(subreddit,str) or subreddit.casefold() not in APPROVED_SUBS:
        raise ValueError("Subreddit not explicitly approved for RAVEN")
    if not isinstance(post_id,str) or not POST_ID.fullmatch(post_id):
        raise ValueError("Invalid post ID")
    if not isinstance(limit,int) or not 1 <= limit <= 50:
        raise ValueError("Read cap exceeded")
    # Official GET comments endpoint, not a .json unauthenticated workaround.
    path=f"/r/{subreddit.casefold()}/comments/{post_id.lower()}"
    return API_ORIGIN+path+"?"+urllib.parse.urlencode({"limit":limit,"depth":3})

def get_comments(config: APIConfig, *, subreddit: str, post_id: str,
                 limit: int = 40, transport: Callable | None = None) -> object:
    if not isinstance(config, APIConfig) or not config.owner_consent:
        raise AccessNotApproved("Owner authorization required")
    # Even direct callers cannot reach the network without approval.
    # Injected transports are only for isolated/offline testing.
    if transport is None and not approval_status():
        raise AccessNotApproved("No approved Reddit API access recorded; request blocked")
    url=approved_url(subreddit,post_id,limit=limit)
    request=urllib.request.Request(url,method="GET",headers={
        "Authorization":"bearer "+config.access_token,
        "User-Agent":config.user_agent,
        "Accept":"application/json"
    })
    if transport is None:
        def transport(req):
            with urllib.request.urlopen(req,timeout=15) as stream:
                code=stream.status
                raw=stream.read(MAX_RESPONSE_BYTES+1)
                return code,raw
    try:
        status,body=transport(request)
    except urllib.error.HTTPError as exc:
        status=exc.code
        body=b""
    except (urllib.error.URLError,TimeoutError) as exc:
        raise ConnectionError("Approved Reddit API unavailable") from None
    if status in (401,403):
        raise APIBlocked("Official Reddit API denied access; no retries or fallback")
    if status==429:
        raise APIRateLimited("Reddit API rate limit reached; no retry or fallback")
    if status!=200:
        raise RuntimeError("Reddit API returned a non-success status")
    if not isinstance(body,bytes) or len(body)>MAX_RESPONSE_BYTES:
        raise ValueError("Invalid or excessive Reddit API payload")
    try:
        data=json.loads(body)
    except ValueError:
        raise ValueError("API did not return JSON") from None
    if not isinstance(data,(list,dict)):
        raise ValueError("Unexpected Reddit JSON response")
    return data
