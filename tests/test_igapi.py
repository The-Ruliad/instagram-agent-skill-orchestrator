"""Offline tests for skills/ig-campaign/igapi.py against a fake Instagram.

    python3 -m unittest discover -s tests

Nothing here opens a socket: igapi._open is the only seam that does, and every
test replaces it with FakeInstagram.
"""

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "skills", "ig-campaign", "igapi.py")
spec = importlib.util.spec_from_file_location("igapi", SKILL)
igapi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(igapi)

TOKEN = "IGAAtest-SECRET-token-0123456789"
NEW_TOKEN = "IGAArefreshed-SECRET-token-9876543210"


def jpeg(width, height):
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = (b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big")
            + width.to_bytes(2, "big") + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    return b"\xff\xd8" + app0 + sof0 + b"\x00" * 64


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


class Resp:
    def __init__(self, body, status=200, headers=None):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status
        self.headers = headers or {}

    def read(self, n=None):
        return self._body if n is None else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeInstagram:
    """Routes requests the way graph.instagram.com would, and records them."""

    def __init__(self):
        self.calls = []
        self.media_files = {
            "https://cdn.example/feed.jpg": (jpeg(1080, 1350), "image/jpeg"),
            "https://cdn.example/tall.jpg": (jpeg(1080, 1920), "image/jpeg"),
            "https://cdn.example/art.png": (PNG, "image/png"),
            "https://cdn.example/reel.mp4": (MP4, "video/mp4"),
        }
        self.containers = 0
        self.token_valid = True
        self.refuse_metric = "reels_skip_rate"

    def error(self, url, code, status=400, message="bad request"):
        body = json.dumps({"error": {"message": message, "code": code}}).encode()
        raise urllib.error.HTTPError(url, status, message, {}, io.BytesIO(body))

    def __call__(self, req, timeout):
        url = req.full_url
        if url in self.media_files:
            body, ctype = self.media_files[url]
            self.calls.append(("MEDIA", url, {}))
            return Resp(body, 206, {"Content-Type": ctype, "Content-Range": f"bytes 0-{len(body) - 1}/{len(body)}"})
        parts = urllib.parse.urlsplit(url)
        params = dict(urllib.parse.parse_qsl(parts.query))
        if req.data:
            params.update(urllib.parse.parse_qsl(req.data.decode()))
        method = req.get_method()
        path = parts.path.replace("/" + igapi.API_VERSION, "", 1)
        self.calls.append((method, path, params))
        if params.get("access_token") not in (TOKEN, NEW_TOKEN) or not self.token_valid:
            self.error(url, 190, 401, "Error validating access token")
        return Resp(self.route(method, path, params, url))

    def route(self, method, path, params, url):
        if path == "/me" and method == "GET":
            return {"id": "app-scoped-1", "user_id": "1784", "username": "theruliad.eu",
                    "account_type": "BUSINESS", "name": "The Ruliad", "followers_count": 120,
                    "follows_count": 10, "media_count": 3}
        if path == "/refresh_access_token":
            return {"access_token": NEW_TOKEN, "token_type": "bearer", "expires_in": 5184000}
        if path == "/me/media":
            if params.get("after") == "page2":
                return {"data": [{"id": "m3", "media_product_type": "FEED", "media_type": "IMAGE",
                                  "timestamp": "2026-11-01T10:00:00+0000", "caption": "third"}]}
            nxt = "https://graph.instagram.com/v27.0/me/media?" + urllib.parse.urlencode(
                {"access_token": params["access_token"], "after": "page2", "fields": params.get("fields", "")})
            return {"data": [{"id": "m1", "media_product_type": "REELS", "media_type": "VIDEO",
                              "timestamp": "2026-11-10T10:00:00+0000", "caption": "first reel\nmore"},
                             {"id": "m2", "media_product_type": "FEED", "media_type": "CAROUSEL_ALBUM",
                              "timestamp": "2026-11-05T10:00:00+0000", "caption": "a carousel"}],
                    "paging": {"next": nxt}}
        if path.endswith("/insights"):
            metrics = params["metric"].split(",")
            if self.refuse_metric in metrics:
                self.error(url, 100, message=f"metric {self.refuse_metric} is not supported")
            media = path.split("/")[1]
            base = {"m1": 900, "m2": 300, "m3": 100, "p1": 500}.get(media, 50)
            data = []
            for m in metrics:
                value = {"views": base, "reach": base // 2, "shares": base // 30, "saved": base // 20,
                         "follows": 2}.get(m, 7)
                if params.get("metric_type") == "total_value":
                    data.append({"name": m, "period": "day", "total_value": {"value": value}})
                else:
                    data.append({"name": m, "period": "lifetime", "values": [{"value": value}]})
            return {"data": data}
        if path.endswith("/content_publishing_limit"):
            return {"data": [{"quota_usage": 2, "config": {"quota_total": 100, "quota_duration": 86400}}]}
        if path.endswith("/media") and method == "POST":
            self.containers += 1
            return {"id": f"c{self.containers}"}
        if path.startswith("/c") and method == "GET":
            return {"status_code": "FINISHED", "status": "Finished"}
        if path.endswith("/media_publish"):
            return {"id": "p1"}
        if path == "/p1":
            return {"permalink": "https://www.instagram.com/p/FAKE/", "timestamp": "2026-11-16T12:00:00+0000"}
        if path.endswith("/comments"):
            return {"data": [{"id": "k1", "text": "Price?", "username": "buyer", "timestamp": "2026-11-16T12:05:00+0000",
                              "replies": {"data": [{"id": "k2", "text": "DM sent", "username": "theruliad.eu"}]}}]}
        if method == "GET" and path.count("/") == 1:
            media = path[1:]
            kind = {"m1": "REELS"}.get(media, "FEED")
            return {"id": media, "media_product_type": kind, "permalink": f"https://www.instagram.com/p/{media}/",
                    "timestamp": "2026-11-10T10:00:00+0000", "caption": "x"}
        self.error(url, 100, message=f"unrouted {method} {path}")


class IgapiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "marketing", "instagram")
        self.fake = FakeInstagram()
        self._orig_open, igapi._open = igapi._open, self.fake
        self._orig_sleep, igapi.time.sleep = igapi.time.sleep, lambda s: None

    def tearDown(self):
        igapi._open = self._orig_open
        igapi.time.sleep = self._orig_sleep
        self.tmp.cleanup()

    def run_cli(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        old_argv, old_stdin = sys.argv, sys.stdin
        sys.argv, sys.stdin = ["igapi.py", "--home", self.home, *argv], io.StringIO(stdin)
        code = 0
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                igapi.main()
        except SystemExit as stop:
            code = stop.code or 0
        finally:
            sys.argv, sys.stdin = old_argv, old_stdin
        text = out.getvalue() + err.getvalue()
        self.assertNotIn("SECRET", text, "a token leaked into the output")
        return code, out.getvalue(), err.getvalue()

    def connect(self):
        code, out, _ = self.run_cli("connect", "--token-stdin", stdin=TOKEN + "\n")
        self.assertEqual(code, 0, out)
        return out

    def test_connect_stores_token_privately(self):
        out = self.connect()
        self.assertIn("@theruliad.eu", out)
        path = igapi.token_path(self.home)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        with open(os.path.join(os.path.dirname(path), ".gitignore")) as fh:
            self.assertIn("*", fh.read())
        with open(path) as fh:
            rec = json.load(fh)
        self.assertEqual(rec["user_id"], "1784")
        self.assertEqual(rec["access_token"], TOKEN)

    def test_connect_refuses_token_on_command_line(self):
        code, _, err = self.run_cli("connect")
        self.assertEqual(code, 2)
        self.assertIn("stdin", err)

    def test_status_and_refresh(self):
        self.connect()
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("fresh", out)
        code, out, _ = self.run_cli("refresh")
        self.assertEqual(code, 0, out)
        with open(igapi.token_path(self.home)) as fh:
            rec = json.load(fh)
        self.assertEqual(rec["access_token"], NEW_TOKEN)
        self.assertIsNotNone(rec["expires_at"])
        code, out, _ = self.run_cli("--json", "status")
        self.assertGreater(json.loads(out)["days_left"], 59)

    def test_media_follows_paging(self):
        self.connect()
        code, out, _ = self.run_cli("--json", "media", "--limit", "3")
        self.assertEqual(code, 0)
        self.assertEqual([m["id"] for m in json.loads(out)], ["m1", "m2", "m3"])

    def test_insights_survive_one_refused_metric(self):
        self.connect()
        code, out, _ = self.run_cli("--json", "insights", "m1")
        self.assertEqual(code, 0, out)
        result = json.loads(out)
        self.assertEqual(result["insights"]["views"], 900)
        self.assertIn("reels_skip_rate", result["unavailable"])

    def test_account_reads_total_values(self):
        self.connect()
        code, out, _ = self.run_cli("--json", "account", "--days", "28")
        self.assertEqual(code, 0, out)
        totals = json.loads(out)["totals"]
        self.assertEqual(totals["accounts_engaged"], 7)
        sent = [c for c in self.fake.calls if c[1] == "/1784/insights"][0][2]
        self.assertEqual(sent["metric_type"], "total_value")

    def test_audit_ranks_by_outlier_multiple(self):
        self.connect()
        code, out, _ = self.run_cli("--json", "audit", "--limit", "3")
        self.assertEqual(code, 0, out)
        result = json.loads(out)
        self.assertEqual(result["median_views"], 300)
        self.assertEqual(result["rows"][0]["id"], "m1")
        self.assertEqual(result["rows"][0]["outlier"], 3.0)
        self.assertTrue(os.path.exists(result["written"]))

    def test_dry_run_passes_a_good_jpeg(self):
        self.connect()
        cap = os.path.join(self.tmp.name, "caption.txt")
        with open(cap, "w") as fh:
            fh.write("We rebuilt a dealer's site in 9 days.\n\nComment SITE and I send the checklist.\n#webdesign")
        code, out, _ = self.run_cli("publish", "image", "--url", "https://cdn.example/feed.jpg",
                                    "--caption-file", cap, "--ai-generated")
        self.assertEqual(code, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("1080x1350", out)
        self.assertIn("READY", out)
        self.assertFalse(any(c[0] == "POST" for c in self.fake.calls), "a dry run must not create anything")

    def test_dry_run_fails_png_and_wrong_aspect(self):
        self.connect()
        code, out, _ = self.run_cli("publish", "image", "--url", "https://cdn.example/art.png")
        self.assertEqual(code, 1)
        self.assertIn("JPEG only", out)
        code, out, _ = self.run_cli("publish", "image", "--url", "https://cdn.example/tall.jpg")
        self.assertEqual(code, 1)
        self.assertIn("outside the feed", out)

    def test_too_many_hashtags_block_confirm(self):
        self.connect()
        cap = os.path.join(self.tmp.name, "caption.txt")
        with open(cap, "w") as fh:
            fh.write("Line one.\n#a #b #c #d #e #f")
        code, out, _ = self.run_cli("publish", "image", "--url", "https://cdn.example/feed.jpg",
                                    "--caption-file", cap, "--confirm")
        self.assertEqual(code, 1)
        self.assertIn("6 hashtags", out)
        self.assertFalse(any(c[0] == "POST" for c in self.fake.calls))

    def test_confirm_posts_and_logs(self):
        self.connect()
        cap = os.path.join(self.tmp.name, "caption.txt")
        with open(cap, "w") as fh:
            fh.write("Nine days, one dealer, zero templates.")
        code, out, _ = self.run_cli("publish", "image", "--url", "https://cdn.example/feed.jpg",
                                    "--caption-file", cap, "--alt-text", "A car dealer's new homepage",
                                    "--ai-generated", "--campaign", "launch", "--confirm")
        self.assertEqual(code, 0, out)
        self.assertIn("POSTED", out)
        create = [c for c in self.fake.calls if c[0] == "POST" and c[1].endswith("/media")][0][2]
        self.assertEqual(create["image_url"], "https://cdn.example/feed.jpg")
        self.assertEqual(create["is_ai_generated"], "true")
        self.assertEqual(create["alt_text"], "A car dealer's new homepage")
        publish = [c for c in self.fake.calls if c[1].endswith("/media_publish")][0][2]
        self.assertEqual(publish["creation_id"], "c1")
        with open(os.path.join(self.home, "published.jsonl")) as fh:
            entry = json.loads(fh.readline())
        self.assertEqual(entry["campaign"], "launch")
        self.assertEqual(entry["permalink"], "https://www.instagram.com/p/FAKE/")
        with open(os.path.join(self.home, "log.md")) as fh:
            self.assertIn("launch", fh.read())
        code, out, _ = self.run_cli("--json", "report", "launch")
        self.assertEqual(code, 0, out)
        report = json.loads(out)
        self.assertEqual(report["posts"], 1)
        self.assertEqual(report["totals"]["views"], 500)

    def test_carousel_container_only_never_publishes(self):
        self.connect()
        code, out, _ = self.run_cli("publish", "carousel", "--url", "https://cdn.example/feed.jpg",
                                    "--url", "https://cdn.example/feed.jpg", "--container-only")
        self.assertEqual(code, 0, out)
        self.assertIn("NOT published", out)
        posts = [c for c in self.fake.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertEqual(posts[0][2]["is_carousel_item"], "true")
        self.assertEqual(posts[2][2]["media_type"], "CAROUSEL")
        self.assertEqual(posts[2][2]["children"], "c1,c2")
        self.assertFalse(any(c[1].endswith("/media_publish") for c in self.fake.calls))

    def test_reel_params(self):
        self.connect()
        code, out, _ = self.run_cli("publish", "reel", "--url", "https://cdn.example/reel.mp4",
                                    "--trial", "manual", "--container-only")
        self.assertEqual(code, 0, out)
        create = [c for c in self.fake.calls if c[0] == "POST"][0][2]
        self.assertEqual(create["media_type"], "REELS")
        self.assertEqual(create["video_url"], "https://cdn.example/reel.mp4")
        self.assertEqual(json.loads(create["trial_params"]), {"graduation_strategy": "MANUAL"})

    def test_comments(self):
        self.connect()
        code, out, _ = self.run_cli("comments", "m1")
        self.assertEqual(code, 0, out)
        self.assertIn("@buyer: Price?", out)
        self.assertIn("DM sent", out)

    def test_expired_token_is_explained(self):
        self.connect()
        self.fake.token_valid = False
        code, _, err = self.run_cli("whoami")
        self.assertEqual(code, 4)
        self.assertIn("connect again", err)

    def test_jpeg_size_reader(self):
        self.assertEqual(igapi.jpeg_size(jpeg(1080, 1350)), (1080, 1350))
        self.assertIsNone(igapi.jpeg_size(PNG))


if __name__ == "__main__":
    unittest.main()
