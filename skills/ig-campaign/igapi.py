#!/usr/bin/env python3
"""
igapi.py - the one place this pack talks to Instagram.

Meta's official Instagram API with Instagram Login (graph.instagram.com), for a
professional (Business or Creator) account. Nothing else in the pack touches
Instagram: no browser automation, no scraping, no password.

Reads run freely: profile, media, insights, comments, publishing quota.
Posting has three locks. `publish` is a dry run unless it carries --confirm,
and the dry run prints exactly what would go out after checking every media
URL. Under TR_OS, a command line holding `igapi.py ... publish ... --confirm`
is deploy-class: it waits on the operator's approval like a production deploy.

The client folder
  One client per project: marketing/instagram/ at the root of the project the
  session runs in, or --home DIR, or $IG_HOME. It holds voice.md, plan.md,
  log.md, campaigns/, insights/ and .secrets/token.json. The token file is
  mode 600 inside a git-ignored .secrets/ and is never printed.

Usage
  python3 igapi.py where
  python3 igapi.py init                                  # create the client folder
  python3 igapi.py connect --token-stdin < token.txt     # once per client
  python3 igapi.py status [--check]                      # who, token age, expiry
  python3 igapi.py refresh                               # monthly; token must be >24h old
  python3 igapi.py whoami
  python3 igapi.py media --limit 25
  python3 igapi.py insights MEDIA_ID
  python3 igapi.py account --days 28
  python3 igapi.py audit --limit 30                      # live numbers for /ig-audit
  python3 igapi.py comments MEDIA_ID
  python3 igapi.py quota
  python3 igapi.py publish image --url URL --caption-file caption.txt --ai-generated
  python3 igapi.py publish carousel --url URL --url URL --caption-file caption.txt
  python3 igapi.py publish reel --url URL --caption-file caption.txt [--trial manual]
  python3 igapi.py publish story --url URL
      --container-only builds and checks the container without posting;
      --confirm posts. Tag a post with --campaign SLUG for the campaign report.
  python3 igapi.py container CONTAINER_ID                # status of a container
  python3 igapi.py report SLUG                           # a campaign's numbers so far

Every command takes --json for machine-readable output.
"""

import argparse
import datetime as dt
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_VERSION = "v27.0"
GRAPH = "https://graph.instagram.com"
TIMEOUT = 30
UA = "ruliad-igapi/1.0 (+https://theruliad.eu)"

# What Instagram Login serves per media product type (Meta docs,
# instagram-media/insights, v27). `impressions` is gone for anything posted
# after July 2024; `views` replaced it. Album children have no insights.
MEDIA_METRICS = {
    "FEED": ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
             "follows", "profile_visits"],
    "REELS": ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
              "ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "reels_skip_rate"],
    "STORY": ["views", "reach", "replies", "shares", "total_interactions", "follows",
              "profile_visits"],
}
# Account level, all metric_type=total_value with period=day (instagram-user/insights).
ACCOUNT_METRICS = ["reach", "views", "accounts_engaged", "total_interactions", "likes",
                   "comments", "shares", "saves", "replies", "follows_and_unfollows",
                   "profile_links_taps"]

CAPTION_LIMIT = 2200
HASHTAG_LIMIT = 5             # Instagram's cap since 18 Dec 2025 (see ig-caption).
MENTION_LIMIT = 20
IMAGE_MAX_BYTES = 8 * 1024 * 1024
FEED_ASPECT = (4 / 5, 1.91)   # the narrowest and widest a feed image may be
STORY_ASPECT = 9 / 16
CAROUSEL_MAX = 20


class ApiError(Exception):
    def __init__(self, status, payload):
        err = (payload or {}).get("error") or {}
        self.status = status
        self.code = err.get("code")
        self.message = err.get("error_user_msg") or err.get("message") or "no message"
        super().__init__(f"HTTP {status}, code {self.code}: {self.message}")


def die(message, code=2):
    print(f"igapi: {message}", file=sys.stderr)
    sys.exit(code)


# --- the client folder -------------------------------------------------------

def project_root(start):
    try:
        run = subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if run.returncode == 0 and run.stdout.strip():
            return run.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def client_home(arg):
    chosen = arg or os.environ.get("IG_HOME")
    if chosen:
        return os.path.abspath(os.path.expanduser(chosen))
    return os.path.join(project_root(os.getcwd()), "marketing", "instagram")


def token_path(home):
    return os.path.join(home, ".secrets", "token.json")


def git_ignores(path):
    """True if git ignores `path`, False if it would track it, None outside a repo."""
    try:
        run = subprocess.run(["git", "-C", os.path.dirname(path), "check-ignore", "-q", path],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(run.returncode)


def ensure_secrets_dir(home):
    secrets = os.path.join(home, ".secrets")
    os.makedirs(secrets, mode=0o700, exist_ok=True)
    os.chmod(secrets, 0o700)
    ignore = os.path.join(secrets, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as fh:
            fh.write("# Access tokens live here. Never commit this folder.\n*\n")
    return secrets


def load_token(home):
    path = token_path(home)
    if not os.path.exists(path):
        die(f"no Instagram account connected for {home}\n"
            "  connect one with: python3 igapi.py connect --token-stdin   (see /ig-campaign)")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_token(home, record):
    ensure_secrets_dir(home)
    path = token_path(home)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    os.replace(path + ".tmp", path)
    os.chmod(path, 0o600)
    if git_ignores(path) is False:
        die(f"git would track {path}; fix the ignore rules before using this token", 3)


def now_utc():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def parse_time(value):
    return dt.datetime.fromisoformat(value) if value else None


# --- HTTP ---------------------------------------------------------------------

def _open(req, timeout):
    # One seam for the tests to replace; nothing else opens a socket.
    return urllib.request.urlopen(req, timeout=timeout)


def _send(req):
    try:
        with _open(req, TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as err:
        raw = err.read()
        err.close()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"error": {"message": raw[:300].decode("utf-8", "replace")}}
        # `from None`: the chained exception would carry the URL, and the URL the token.
        raise ApiError(err.code, payload) from None
    except (urllib.error.URLError, OSError) as err:
        raise ApiError(0, {"error": {"message": f"network: {getattr(err, 'reason', err)}"}}) from None
    try:
        return json.loads(body or b"{}")
    except ValueError:
        raise ApiError(200, {"error": {"message": "response was not JSON"}}) from None


def call(method, path, token, params=None, versioned=True):
    params = dict(params or {}, access_token=token)
    base = f"{GRAPH}/{API_VERSION}/{path}" if versioned else f"{GRAPH}/{path}"
    if method == "GET":
        req = urllib.request.Request(base + "?" + urllib.parse.urlencode(params),
                                     headers={"User-Agent": UA})
    else:
        req = urllib.request.Request(base, data=urllib.parse.urlencode(params).encode(),
                                     method=method, headers={"User-Agent": UA})
    return _send(req)


def follow(url):
    """Fetch a `paging.next` URL. Meta builds it with the token already in it."""
    if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(GRAPH).netloc:
        raise ApiError(0, {"error": {"message": "refusing a paging link to another host"}})
    return _send(urllib.request.Request(url, headers={"User-Agent": UA}))


def paged(path, token, params, limit):
    page = call("GET", path, token, dict(params, limit=min(limit, 100)))
    items = []
    while True:
        items.extend(page.get("data") or [])
        nxt = (page.get("paging") or {}).get("next")
        if len(items) >= limit or not nxt:
            return items[:limit]
        page = follow(nxt)


def metric_values(resp):
    out = {}
    for item in resp.get("data") or []:
        if "total_value" in item:
            out[item.get("name")] = (item.get("total_value") or {}).get("value")
        else:
            values = item.get("values") or []
            out[item.get("name")] = values[-1].get("value") if values else None
    return out


def fatal(err):
    # An expired token or a dead network fails every call alike: retrying per metric only adds noise.
    return err.code == 190 or err.status in (0, 401)


def fetch_metrics(path, token, metrics, extra=None):
    """All metrics in one call; if Instagram refuses the set, one at a time, so a
    metric this post or account cannot have does not cost the others."""
    extra = dict(extra or {})
    try:
        return metric_values(call("GET", path, token, dict(extra, metric=",".join(metrics)))), {}
    except ApiError as err:
        if fatal(err):
            raise
        if len(metrics) == 1:
            return {}, {metrics[0]: err.message}
    got, missing = {}, {}
    for metric in metrics:
        try:
            got.update(metric_values(call("GET", path, token, dict(extra, metric=metric))))
        except ApiError as err:
            if fatal(err):
                raise
            missing[metric] = err.message
    return got, missing


# --- output -------------------------------------------------------------------

def emit(args, result, human):
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        human(result)


def fmt(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def ratio(num, den):
    return round(num / den, 4) if isinstance(num, (int, float)) and den else None


# --- commands: the folder and the token ---------------------------------------

def cmd_where(args):
    home = client_home(args.home)
    token = os.path.exists(token_path(home))
    result = {"home": home, "exists": os.path.isdir(home), "connected": token,
              "files": sorted(os.listdir(home)) if os.path.isdir(home) else []}

    def human(r):
        print(f"client folder  {r['home']}")
        print(f"exists         {'yes' if r['exists'] else 'no (python3 igapi.py init)'}")
        print(f"connected      {'yes' if r['connected'] else 'no'}")
        if r["files"]:
            print("contents       " + ", ".join(r["files"]))
    emit(args, result, human)


def cmd_init(args):
    home = client_home(args.home)
    for sub in ("campaigns", "insights"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    ensure_secrets_dir(home)
    here = os.path.dirname(os.path.abspath(__file__))
    created = []
    for name in ("voice.md",):
        dst = os.path.join(home, name)
        src = os.path.join(here, "templates", name)
        if not os.path.exists(dst) and os.path.exists(src):
            with open(src, encoding="utf-8") as fh, open(dst, "w", encoding="utf-8") as out:
                out.write(fh.read())
            created.append(name)
    tracked = git_ignores(os.path.join(home, "voice.md"))
    result = {"home": home, "created": created, "git_tracks_drafts": tracked is False}

    def human(r):
        print(f"client folder ready: {r['home']}")
        for name in r["created"]:
            print(f"  + {name} (fill it in before the first post)")
        if r["git_tracks_drafts"]:
            print("  note: git would track the drafts here. If campaign drafts must stay private,\n"
                  "        add marketing/ to .git/info/exclude. The .secrets/ folder ignores itself.")
    emit(args, result, human)


def cmd_connect(args):
    if not args.token_stdin:
        die("pass the token on stdin with --token-stdin; it never goes on a command line")
    token = sys.stdin.read().strip()
    if not token or any(ch.isspace() for ch in token):
        die("stdin did not hold a single token")
    me = call("GET", "me", token, {"fields": "user_id,username,account_type"})
    home = client_home(args.home)
    os.makedirs(home, exist_ok=True)
    when = now_utc()
    record = {
        "access_token": token,
        "user_id": me.get("user_id") or me.get("id"),
        "app_scoped_id": me.get("id"),
        "username": me.get("username"),
        "account_type": me.get("account_type"),
        "connected_at": when.isoformat(),
        "refreshed_at": None,
        "expires_at": (when + dt.timedelta(seconds=args.expires_in)).isoformat() if args.expires_in else None,
        "api_version": API_VERSION,
    }
    save_token(home, record)
    result = {k: v for k, v in record.items() if k != "access_token"}
    result["token_file"] = token_path(home)

    def human(r):
        print(f"connected @{r['username']} ({r['account_type']}), Instagram user {r['user_id']}")
        print(f"token stored in {r['token_file']} (mode 600, git-ignored)")
        if r["account_type"] not in ("BUSINESS", "MEDIA_CREATOR"):
            print("warning: publishing and insights need a Business or Creator account")
    emit(args, result, human)


def token_status(rec):
    when = now_utc()
    connected = parse_time(rec.get("refreshed_at") or rec.get("connected_at"))
    expires = parse_time(rec.get("expires_at"))
    age_days = (when - connected).total_seconds() / 86400 if connected else None
    left_days = (expires - when).total_seconds() / 86400 if expires else None
    if left_days is not None and left_days <= 0:
        advice = "expired: generate a new token and connect again"
    elif age_days is not None and age_days < 1:
        advice = "fresh; refresh works once it is a day old"
    elif left_days is None:
        advice = "expiry unknown until the first refresh: run `igapi.py refresh`"
    elif left_days < 15:
        advice = "refresh now: `igapi.py refresh`"
    else:
        advice = "ok"
    return {"username": rec.get("username"), "user_id": rec.get("user_id"),
            "account_type": rec.get("account_type"),
            "token_age_days": round(age_days, 1) if age_days is not None else None,
            "expires_at": rec.get("expires_at"),
            "days_left": round(left_days, 1) if left_days is not None else None,
            "advice": advice}


def cmd_status(args):
    home = client_home(args.home)
    rec = load_token(home)
    result = dict(token_status(rec), home=home)
    if args.check:
        try:
            me = call("GET", "me", rec["access_token"], {"fields": "username,account_type"})
            result["live"] = f"ok, @{me.get('username')}"
        except ApiError as err:
            result["live"] = f"FAILED: {err.message}"

    def human(r):
        print(f"@{r['username']}  ({r['account_type']}, user {r['user_id']})  {r['home']}")
        print(f"token age   {fmt(r['token_age_days'])} days")
        print(f"expires     {r['expires_at'] or 'unknown'}  ({fmt(r['days_left'])} days left)")
        print(f"advice      {r['advice']}")
        if "live" in r:
            print(f"live check  {r['live']}")
    emit(args, result, human)


def cmd_refresh(args):
    home = client_home(args.home)
    rec = load_token(home)
    resp = call("GET", "refresh_access_token", rec["access_token"],
                {"grant_type": "ig_refresh_token"}, versioned=False)
    if not resp.get("access_token"):
        die("Instagram answered without a token; nothing changed")
    when = now_utc()
    rec["access_token"] = resp["access_token"]
    rec["refreshed_at"] = when.isoformat()
    if resp.get("expires_in"):
        rec["expires_at"] = (when + dt.timedelta(seconds=int(resp["expires_in"]))).isoformat()
    save_token(home, rec)
    result = token_status(rec)
    emit(args, result, lambda r: print(f"refreshed @{r['username']}: expires {r['expires_at']} "
                                       f"({fmt(r['days_left'])} days)"))


# --- commands: reads -------------------------------------------------------------

def session(args):
    home = client_home(args.home)
    rec = load_token(home)
    return home, rec, rec["access_token"], rec.get("user_id") or "me"


def cmd_whoami(args):
    _, _, token, _ = session(args)
    me = call("GET", "me", token, {"fields": "user_id,username,name,account_type,profile_picture_url,"
                                             "followers_count,follows_count,media_count"})

    def human(r):
        print(f"@{r.get('username')}  {r.get('name') or ''}  ({r.get('account_type')})")
        print(f"followers {fmt(r.get('followers_count'))}  following {fmt(r.get('follows_count'))}  "
              f"posts {fmt(r.get('media_count'))}  user {r.get('user_id')}")
    emit(args, me, human)


MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count"


def first_line(caption, width=60):
    line = (caption or "").strip().splitlines()[0] if (caption or "").strip() else ""
    return line if len(line) <= width else line[:width - 1] + "…"


def cmd_media(args):
    _, _, token, _ = session(args)
    items = paged("me/media", token, {"fields": MEDIA_FIELDS}, args.limit)

    def human(rows):
        for m in rows:
            print(f"{m.get('timestamp', '')[:10]}  {m.get('media_product_type', ''):<6} "
                  f"{m.get('media_type', ''):<15} likes {fmt(m.get('like_count')):>6}  "
                  f"comments {fmt(m.get('comments_count')):>5}  {m.get('id')}  {first_line(m.get('caption'), 40)}")
    emit(args, items, human)


def product_type(media):
    kind = (media.get("media_product_type") or "FEED").upper()
    return "REELS" if kind in ("REELS", "REEL", "CLIPS") else kind if kind in MEDIA_METRICS else "FEED"


def media_insights(token, media):
    metrics = MEDIA_METRICS[product_type(media)]
    values, missing = fetch_metrics(f"{media['id']}/insights", token, metrics)
    return values, missing


def cmd_insights(args):
    _, _, token, _ = session(args)
    media = call("GET", args.media_id, token, {"fields": MEDIA_FIELDS})
    values, missing = media_insights(token, media)
    result = {"media": media, "insights": values, "unavailable": missing}

    def human(r):
        m = r["media"]
        print(f"{m.get('permalink')}  {product_type(m)}  {m.get('timestamp', '')[:16]}")
        for name, value in r["insights"].items():
            print(f"  {name:<32} {fmt(value)}")
        for name, why in r["unavailable"].items():
            print(f"  {name:<32} unavailable: {why}")
    emit(args, result, human)


def cmd_account(args):
    _, _, token, ig_id = session(args)
    if not 1 <= args.days <= 30:
        die("--days must be between 1 and 30 (one request window)")
    until = int(time.time())
    since = until - args.days * 86400
    values, missing = fetch_metrics(f"{ig_id}/insights", token, ACCOUNT_METRICS,
                                    {"period": "day", "metric_type": "total_value",
                                     "since": since, "until": until})
    result = {"days": args.days, "since": since, "until": until, "totals": values, "unavailable": missing}

    def human(r):
        print(f"ACCOUNT  last {r['days']} days  (Instagram's numbers can lag up to 48 hours)")
        for name, value in r["totals"].items():
            print(f"  {name:<24} {fmt(value)}")
        for name, why in r["unavailable"].items():
            print(f"  {name:<24} unavailable: {why}")
    emit(args, result, human)


def cmd_comments(args):
    _, _, token, _ = session(args)
    items = paged(f"{args.media_id}/comments", token,
                  {"fields": "id,text,username,timestamp,like_count,replies{id,text,username,timestamp}"},
                  args.limit)

    def human(rows):
        for c in rows:
            print(f"{c.get('timestamp', '')[:16]}  @{c.get('username')}: {c.get('text')}  [{c.get('id')}]")
            for reply in (c.get("replies") or {}).get("data") or []:
                print(f"    ↳ @{reply.get('username')}: {reply.get('text')}")
    emit(args, items, human)


def quota(token, ig_id):
    resp = call("GET", f"{ig_id}/content_publishing_limit", token, {"fields": "config,quota_usage"})
    row = (resp.get("data") or [{}])[0]
    return {"used": row.get("quota_usage"), "total": (row.get("config") or {}).get("quota_total"),
            "window_hours": round(((row.get("config") or {}).get("quota_duration") or 86400) / 3600)}


def cmd_quota(args):
    _, _, token, ig_id = session(args)
    result = quota(token, ig_id)
    emit(args, result, lambda r: print(f"published through the API: {fmt(r['used'])} of "
                                       f"{fmt(r['total'])} in the last {r['window_hours']}h"))


def score_posts(rows):
    """The /ig-audit measures, from live numbers: outlier multiple against the
    account's own median views, then sends, saves and follows per reach."""
    views = [r["insights"].get("views") for r in rows if isinstance(r["insights"].get("views"), (int, float))]
    median = statistics.median(views) if views else None
    for r in rows:
        ins = r["insights"]
        r["outlier"] = ratio(ins.get("views"), median)
        r["sends_per_reach"] = ratio(ins.get("shares"), ins.get("reach"))
        r["saves_per_reach"] = ratio(ins.get("saved"), ins.get("reach"))
        r["follows_per_reach"] = ratio(ins.get("follows"), ins.get("reach"))
    return median


def audit_rows(token, media_list):
    rows = []
    for m in media_list:
        values, missing = media_insights(token, m)
        rows.append({"id": m["id"], "permalink": m.get("permalink"), "timestamp": m.get("timestamp"),
                     "type": product_type(m), "first_line": first_line(m.get("caption")),
                     "insights": values, "unavailable": sorted(missing)})
    return rows


def print_rows(rows, title):
    print(title)
    for r in rows:
        ins = r["insights"]
        print(f"  {fmt(r['outlier']):>6}x  {r['type']:<5} {(r.get('timestamp') or '')[:10]}  "
              f"views {fmt(ins.get('views')):>7}  "
              f"reach {fmt(ins.get('reach')):>7}  sends/reach {fmt(r['sends_per_reach']):>6}  "
              f"saves/reach {fmt(r['saves_per_reach']):>6}  {r['first_line']}")


def cmd_audit(args):
    home, _, token, _ = session(args)
    media_list = paged("me/media", token, {"fields": MEDIA_FIELDS}, args.limit)
    rows = audit_rows(token, media_list)
    median = score_posts(rows)
    ranked = sorted(rows, key=lambda r: (r["outlier"] is None, -(r["outlier"] or 0)))
    stamp = now_utc().strftime("%Y-%m-%d")
    out = args.out or os.path.join(home, "insights", f"audit-{stamp}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    result = {"generated": now_utc().isoformat(), "posts": len(rows), "median_views": median,
              "note": "hold at 3s and non-follower share per post are not in the API; read them in the app",
              "rows": ranked}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    result["written"] = out

    def human(r):
        print(f"AUDIT  ·  {r['posts']} posts  ·  median views {fmt(r['median_views'])}  ·  {r['written']}")
        print_rows(r["rows"][:5], "TOP 5 BY OUTLIER MULTIPLE")
        if len(r["rows"]) > 5:
            print_rows(r["rows"][-5:], "BOTTOM 5")
        print(f"NOTE  {r['note']}. Hand this file to /ig-audit.")
    emit(args, result, human)


def cmd_container(args):
    _, _, token, _ = session(args)
    st = call("GET", args.container_id, token, {"fields": "status_code,status"})
    emit(args, st, lambda r: print(f"{args.container_id}: {r.get('status_code')} {r.get('status') or ''}"))


# --- publishing ----------------------------------------------------------------

def jpeg_size(head):
    """(width, height) from a JPEG's first bytes, or None."""
    if head[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(head):
        if head[i] != 0xFF:
            i += 1
            continue
        marker = head[i + 1]
        if marker == 0xFF or marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 1 if marker == 0xFF else 2
            continue
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return int.from_bytes(head[i + 7:i + 9], "big"), int.from_bytes(head[i + 5:i + 7], "big")
        i += 2 + int.from_bytes(head[i + 2:i + 4], "big")
    return None


def sniff(head):
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        return "video/mp4"
    return None


def probe(url):
    """Status, type, size and (JPEG) pixel size of a media URL, from its first 256 KB."""
    info = {"url": url, "ok": False}
    if urllib.parse.urlsplit(url).scheme != "https":
        info["problem"] = "not an https URL Instagram can fetch"
        return info
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-262143"})
    try:
        with _open(req, TIMEOUT) as resp:
            head = resp.read(262144)
            status = resp.status
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            total = resp.headers.get("Content-Range", "").rpartition("/")[2] or resp.headers.get("Content-Length")
    except urllib.error.HTTPError as err:
        info["problem"] = f"HTTP {err.code}: Instagram fetches the URL itself, so it must be public"
        return info
    except (urllib.error.URLError, OSError) as err:
        info["problem"] = f"could not reach it: {getattr(err, 'reason', err)}"
        return info
    info.update(status=status, content_type=sniff(head) or ctype,
                bytes=int(total) if str(total).isdigit() else None)
    dims = jpeg_size(head)
    if dims:
        info["width"], info["height"] = dims
        info["aspect"] = round(dims[0] / dims[1], 4)
    info["ok"] = status in (200, 206)
    return info


def check_media(kind, infos):
    problems, warnings = [], []
    images = kind in ("image", "carousel", "story")
    for n, info in enumerate(infos, 1):
        tag = f"media {n}"
        if not info.get("ok"):
            problems.append(f"{tag}: {info.get('problem', 'not reachable')}")
            continue
        ctype = info.get("content_type") or ""
        if ctype.startswith("image/"):
            if ctype != "image/jpeg":
                problems.append(f"{tag}: {ctype} - Instagram's API takes JPEG only "
                                "(Higgsfield: output_format jpeg)")
            if info.get("bytes") and info["bytes"] > IMAGE_MAX_BYTES:
                problems.append(f"{tag}: {info['bytes'] / 1e6:.1f} MB, over the 8 MB image limit")
            aspect = info.get("aspect")
            if aspect and kind in ("image", "carousel") and not FEED_ASPECT[0] - 0.005 <= aspect <= FEED_ASPECT[1] + 0.005:
                problems.append(f"{tag}: aspect {aspect} is outside the feed's 4:5 to 1.91:1 "
                                "(use 4:5 at 1080x1350)")
            if aspect and kind == "story" and abs(aspect - STORY_ASPECT) > 0.02:
                warnings.append(f"{tag}: aspect {aspect}; stories fill the screen at 9:16")
        elif ctype.startswith("video/") or ctype == "application/octet-stream":
            if kind == "image":
                problems.append(f"{tag}: a video in an image post; use `publish reel`")
            elif kind == "reel":
                warnings.append(f"{tag}: video; Instagram checks length (3 s to 15 min) and "
                                "codec when it builds the container")
        else:
            problems.append(f"{tag}: content type {ctype or 'unknown'} is not an image or a video")
        if not images and ctype.startswith("image/"):
            problems.append(f"{tag}: an image in a reel; use `publish image`")
    if kind == "carousel":
        aspects = {i.get("aspect") for i in infos if i.get("aspect")}
        if len(aspects) > 1:
            warnings.append("carousel items differ in aspect; Instagram crops them all to the first")
    return problems, warnings


def caption_lint(caption):
    """Run ig-caption's linter if the sibling skill is installed next to this one."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ig-caption", "caption.py")
    if not caption or not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("ig_caption", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.analyse(caption, cut=mod.TRUNCATE, keywords=[])
    return {"verdict": result["verdict"],
            "checks": [c for c in result["checks"] if c["status"] in ("FAIL", "WARN")]}


def check_caption(caption):
    problems = []
    if len(caption) > CAPTION_LIMIT:
        problems.append(f"caption is {len(caption)} characters; the limit is {CAPTION_LIMIT}")
    tags = [w for w in caption.split() if w.startswith("#") and len(w) > 1]
    if len(tags) > HASHTAG_LIMIT:
        problems.append(f"{len(tags)} hashtags; Instagram allows {HASHTAG_LIMIT}")
    mentions = [w for w in caption.split() if w.startswith("@") and len(w) > 1]
    if len(mentions) > MENTION_LIMIT:
        problems.append(f"{len(mentions)} @mentions; the API allows {MENTION_LIMIT}")
    return problems


def container_params(kind, url, info, args, caption, child=False):
    is_video = (info.get("content_type") or "").startswith("video/")
    params = {}
    if kind == "reel":
        params.update(media_type="REELS", video_url=url, share_to_feed=str(not args.no_feed).lower())
        if args.cover_url:
            params["cover_url"] = args.cover_url
        if args.trial:
            params["trial_params"] = json.dumps({"graduation_strategy": args.trial.upper()})
    elif kind == "story":
        params["media_type"] = "STORIES"
        params["video_url" if is_video else "image_url"] = url
    elif child:
        params["is_carousel_item"] = "true"
        if is_video:
            params.update(media_type="VIDEO", video_url=url)
        else:
            params["image_url"] = url
    else:
        params["image_url"] = url
        if args.alt_text:
            params["alt_text"] = args.alt_text
    if caption and not child and kind != "story":
        params["caption"] = caption
    if args.ai_generated and not child:
        params["is_ai_generated"] = "true"
    return params


def wait_ready(token, container_id, video):
    # Images settle in seconds. Meta asks to poll video no more than about once a
    # minute for five minutes; back off towards that.
    deadline = time.time() + (300 if video else 60)
    delay = 3 if video else 1
    while True:
        st = call("GET", container_id, token, {"fields": "status_code,status"})
        code = st.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return st
        if code in ("ERROR", "EXPIRED"):
            raise ApiError(0, {"error": {"message": f"container {container_id} {code}: {st.get('status') or ''}"}})
        if time.time() > deadline:
            raise ApiError(0, {"error": {"message": f"container {container_id} still {code}; check later "
                                                    f"with `igapi.py container {container_id}`"}})
        time.sleep(delay)
        delay = min(delay * 2, 30)


def build_containers(token, ig_id, kind, urls, infos, args, caption):
    if kind == "carousel":
        children = []
        for url, info in zip(urls, infos):
            cid = call("POST", f"{ig_id}/media", token,
                       container_params(kind, url, info, args, caption, child=True))["id"]
            wait_ready(token, cid, (info.get("content_type") or "").startswith("video/"))
            children.append(cid)
        params = {"media_type": "CAROUSEL", "children": ",".join(children)}
        if caption:
            params["caption"] = caption
        if args.ai_generated:
            params["is_ai_generated"] = "true"
        parent = call("POST", f"{ig_id}/media", token, params)["id"]
        wait_ready(token, parent, False)
        return parent
    info = infos[0]
    cid = call("POST", f"{ig_id}/media", token, container_params(kind, urls[0], info, args, caption))["id"]
    wait_ready(token, cid, kind == "reel" or (info.get("content_type") or "").startswith("video/"))
    return cid


def append_log(home, entry):
    with open(os.path.join(home, "published.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log = os.path.join(home, "log.md")
    new = not os.path.exists(log)
    with open(log, "a", encoding="utf-8") as fh:
        if new:
            fh.write("# Published\n\nWritten by igapi.py on every post. /ig-plan and /ig-audit read it.\n\n")
        fh.write(f"- {entry['published_at'][:16].replace('T', ' ')}  {entry['kind'].upper():<8} "
                 f"{entry.get('campaign') or '-':<16} {entry.get('permalink') or entry['media_id']}  "
                 f"{entry.get('first_line') or ''}\n")


def cmd_publish(args):
    home, rec, token, ig_id = session(args)
    kind = args.kind
    urls = args.url or []
    if kind == "carousel" and not 2 <= len(urls) <= CAROUSEL_MAX:
        die(f"a carousel takes 2 to {CAROUSEL_MAX} --url items")
    if kind != "carousel" and len(urls) != 1:
        die(f"`publish {kind}` takes exactly one --url")
    if args.confirm and args.container_only:
        die("--confirm posts; --container-only stops before posting. Pick one.")
    caption = ""
    if args.caption_file:
        with open(args.caption_file, encoding="utf-8") as fh:
            caption = fh.read().strip()
    if kind == "story" and caption:
        die("stories take no caption")

    infos = [probe(u) for u in urls]
    problems, warnings = check_media(kind, infos)
    problems += check_caption(caption)
    lint = caption_lint(caption)
    if not args.ai_generated:
        warnings.append("no --ai-generated: say so if any of this media was generated (Higgsfield counts)")
    try:
        q = quota(token, ig_id)
    except ApiError as err:
        q = {"error": err.message}
    plan = {"account": rec.get("username"), "kind": kind, "media": infos, "caption": caption,
            "caption_chars": len(caption), "alt_text": args.alt_text, "ai_generated": args.ai_generated,
            "trial": args.trial, "campaign": args.campaign, "quota": q, "caption_lint": lint,
            "problems": problems, "warnings": warnings,
            "mode": "confirm" if args.confirm else "container-only" if args.container_only else "dry-run"}

    if problems or plan["mode"] == "dry-run":
        emit(args, plan, human_plan)
        sys.exit(1 if problems else 0)

    container = build_containers(token, ig_id, kind, urls, infos, args, caption)
    plan["container_id"] = container
    if plan["mode"] == "container-only":
        plan["note"] = "container is ready and NOT published; it expires in 24 hours"
        emit(args, plan, human_plan)
        return
    published = call("POST", f"{ig_id}/media_publish", token, {"creation_id": container})
    media_id = published.get("id")
    try:
        meta = call("GET", media_id, token, {"fields": "permalink,timestamp"})
    except ApiError:
        meta = {}
    entry = {"published_at": now_utc().isoformat(), "kind": kind, "media_id": media_id,
             "permalink": meta.get("permalink"), "campaign": args.campaign,
             "first_line": first_line(caption), "urls": urls, "ai_generated": args.ai_generated,
             "trial": args.trial, "account": rec.get("username")}
    append_log(home, entry)
    plan.update(media_id=media_id, permalink=meta.get("permalink"))
    emit(args, plan, human_plan)


def human_plan(p):
    head = {"dry-run": "DRY RUN - nothing posted", "container-only": "CONTAINER BUILT - not posted",
            "confirm": "POSTED"}[p["mode"]]
    print(f"{head}  ·  @{p['account']}  ·  {p['kind'].upper()}"
          + (f"  ·  campaign {p['campaign']}" if p.get("campaign") else ""))
    print("=" * 72)
    for n, m in enumerate(p["media"], 1):
        dims = f"{m['width']}x{m['height']} (aspect {m['aspect']})" if m.get("width") else ""
        size = f"{m['bytes'] / 1e6:.1f} MB" if m.get("bytes") else ""
        state = "ok " if m.get("ok") else "BAD"
        print(f"  {state} media {n}: {m.get('content_type') or '?'} {size} {dims}")
        print(f"        {m['url']}")
    if p["caption"]:
        visible = p["caption"][:125].replace("\n", " / ")
        print(f"  caption {p['caption_chars']} chars; the feed shows:")
        print(f"    | {visible}{' ...more' if p['caption_chars'] > 125 else ''}")
    if p.get("caption_lint"):
        print(f"  ig-caption linter: {p['caption_lint']['verdict']}")
        for c in p["caption_lint"]["checks"]:
            print(f"    {c['status']:<5} {c['check']}: {c['detail']}")
    print(f"  alt text: {p['alt_text'] or '-'}   AI label: {'yes' if p['ai_generated'] else 'no'}"
          f"   trial reel: {p['trial'] or 'no'}")
    q = p.get("quota") or {}
    if "used" in q:
        print(f"  API posts in the last {q['window_hours']}h: {fmt(q['used'])} of {fmt(q['total'])}")
    for w in p["warnings"]:
        print(f"  WARN  {w}")
    for x in p["problems"]:
        print(f"  FAIL  {x}")
    if p.get("container_id"):
        print(f"  container {p['container_id']}")
    if p.get("permalink") or p.get("media_id"):
        print(f"  live: {p.get('permalink') or p.get('media_id')}")
    if p.get("note"):
        print(f"  {p['note']}")
    if p["mode"] == "dry-run" and not p["problems"]:
        print("  READY. Post only after the user's explicit yes: rerun with --confirm.")


# --- campaign report -----------------------------------------------------------

def cmd_report(args):
    home, _, token, _ = session(args)
    path = os.path.join(home, "published.jsonl")
    entries = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
    posts = [e for e in entries if e.get("campaign") == args.slug and e.get("media_id")]
    if not posts:
        die(f"no posts tagged --campaign {args.slug} in {path}")
    rows = audit_rows(token, [{"id": e["media_id"], "permalink": e.get("permalink"),
                               "timestamp": e.get("published_at"),
                               "media_product_type": "REELS" if e["kind"] == "reel" else
                               "STORY" if e["kind"] == "story" else "FEED",
                               "caption": e.get("first_line")} for e in posts])
    baseline = None
    if args.baseline:
        others = [m for m in paged("me/media", token, {"fields": MEDIA_FIELDS}, args.baseline)
                  if m["id"] not in {e["media_id"] for e in posts}]
        base_rows = audit_rows(token, others)
        views = [r["insights"].get("views") for r in base_rows if isinstance(r["insights"].get("views"), (int, float))]
        baseline = statistics.median(views) if views else None
    for r in rows:
        ins = r["insights"]
        r["outlier"] = ratio(ins.get("views"), baseline)
        r["sends_per_reach"] = ratio(ins.get("shares"), ins.get("reach"))
        r["saves_per_reach"] = ratio(ins.get("saved"), ins.get("reach"))
        r["follows_per_reach"] = ratio(ins.get("follows"), ins.get("reach"))
    totals = {}
    for key in ("views", "reach", "likes", "comments", "shares", "saved", "follows", "total_interactions"):
        nums = [r["insights"].get(key) for r in rows if isinstance(r["insights"].get(key), (int, float))]
        totals[key] = sum(nums) if nums else None
    result = {"campaign": args.slug, "generated": now_utc().isoformat(), "posts": len(rows),
              "baseline_median_views": baseline, "totals": totals, "rows": rows,
              "note": "reach does not add up across posts (the same person counts once per post)"}
    folder = os.path.join(home, "campaigns", args.slug)
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, f"report-{now_utc().strftime('%Y-%m-%d')}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    result["written"] = out

    def human(r):
        print(f"CAMPAIGN {r['campaign']}  ·  {r['posts']} posts  ·  baseline median views "
              f"{fmt(r['baseline_median_views'])}  ·  {r['written']}")
        print("  " + "  ".join(f"{k} {fmt(v)}" for k, v in r["totals"].items()))
        print_rows(r["rows"], "POSTS")
        print(f"NOTE  {r['note']}")
    emit(args, result, human)


# --- CLI ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Instagram API (Instagram Login) for the ig-* skills.")
    ap.add_argument("--home", help="client folder (default: marketing/instagram at the project root, or $IG_HOME)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("where", help="show the client folder").set_defaults(fn=cmd_where)
    sub.add_parser("init", help="create the client folder").set_defaults(fn=cmd_init)
    p = sub.add_parser("connect", help="store a token read from stdin")
    p.add_argument("--token-stdin", action="store_true")
    p.add_argument("--expires-in", type=int, help="seconds until expiry, if Meta told you")
    p.set_defaults(fn=cmd_connect)
    p = sub.add_parser("status", help="token age and expiry")
    p.add_argument("--check", action="store_true", help="also make one live call")
    p.set_defaults(fn=cmd_status)
    sub.add_parser("refresh", help="refresh the long-lived token").set_defaults(fn=cmd_refresh)
    sub.add_parser("whoami", help="the connected account").set_defaults(fn=cmd_whoami)
    p = sub.add_parser("media", help="recent posts")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(fn=cmd_media)
    p = sub.add_parser("insights", help="one post's numbers")
    p.add_argument("media_id")
    p.set_defaults(fn=cmd_insights)
    p = sub.add_parser("account", help="account totals for the last N days")
    p.add_argument("--days", type=int, default=28)
    p.set_defaults(fn=cmd_account)
    p = sub.add_parser("audit", help="rank recent posts the /ig-audit way")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--out")
    p.set_defaults(fn=cmd_audit)
    p = sub.add_parser("comments", help="comments on one of your posts")
    p.add_argument("media_id")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_comments)
    sub.add_parser("quota", help="API posts used in the last 24h").set_defaults(fn=cmd_quota)
    p = sub.add_parser("container", help="status of a media container")
    p.add_argument("container_id")
    p.set_defaults(fn=cmd_container)
    p = sub.add_parser("publish", help="dry run by default; --confirm posts")
    p.add_argument("kind", choices=["image", "carousel", "reel", "story"])
    p.add_argument("--url", action="append", help="public https URL of the media (repeat for a carousel)")
    p.add_argument("--caption-file")
    p.add_argument("--alt-text")
    p.add_argument("--cover-url", help="reel cover image")
    p.add_argument("--no-feed", action="store_true", help="reel stays off the main grid")
    p.add_argument("--trial", choices=["manual", "ss_performance"],
                   help="trial reel: shown to non-followers first")
    p.add_argument("--ai-generated", action="store_true", help="label the post as AI-generated")
    p.add_argument("--campaign", help="campaign slug for igapi.py report")
    p.add_argument("--container-only", action="store_true")
    p.add_argument("--confirm", action="store_true", help="actually post")
    p.set_defaults(fn=cmd_publish)
    p = sub.add_parser("report", help="a campaign's numbers so far")
    p.add_argument("slug")
    p.add_argument("--baseline", type=int, default=0,
                   help="also compare against the median views of your last N other posts")
    p.set_defaults(fn=cmd_report)

    args = ap.parse_args()
    try:
        args.fn(args)
    except ApiError as err:
        hint = ""
        if err.code == 190:
            hint = "\n  the token is invalid or expired: generate a new one and run connect again"
        elif err.code in (10, 200) or "permission" in err.message.lower():
            hint = "\n  the token lacks a permission this needs; regenerate it with the scopes /ig-campaign lists"
        die(f"Instagram said: {err.message}{hint}", 4)


if __name__ == "__main__":
    main()
