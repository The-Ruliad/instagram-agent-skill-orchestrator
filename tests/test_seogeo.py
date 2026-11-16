"""Offline tests for skills/seo-geo/seogeo.py against a fake website.

    python3 -m unittest discover -s tests
"""

import importlib.util
import io
import json
import os
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("seogeo", os.path.join(HERE, "..", "skills", "seo-geo", "seogeo.py"))
seogeo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seogeo)

ENTITY = {
    "@context": "https://schema.org",
    "@graph": [
        {"@type": "AutoDealer", "name": "Noorderhaven Cars", "url": "https://dealer.example/",
         "telephone": "+32 3 000 00 00", "address": {"@type": "PostalAddress", "addressLocality": "Antwerpen"},
         "sameAs": ["https://www.instagram.com/noorderhaven.cars/"]},
        {"@type": "FAQPage", "mainEntity": []},
    ],
}
HOME = f"""<!doctype html><html lang="nl-BE"><head>
<title>Tweedehands wagens in Antwerpen | Noorderhaven Cars</title>
<meta name="description" content="Gekeurde tweedehands wagens in Antwerpen met 12 maanden garantie, inruil en financiering op maat. Kom langs voor een proefrit.">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta property="og:title" content="Noorderhaven Cars"><meta property="og:image" content="https://dealer.example/og.jpg">
<link rel="canonical" href="https://dealer.example/">
<script type="application/ld+json">{json.dumps(ENTITY)}</script>
</head><body>
<h1>Tweedehands wagens in Antwerpen</h1>
<p>{"Wij verkopen gekeurde wagens met garantie. " * 40}</p>
<h2>Hoe werkt inruil?</h2><p>Zo.</p><h2>Kan ik financieren?</h2><p>Ja.</p><h2>Wanneer zijn jullie open?</h2>
<img src="a.jpg" alt="Showroom"><img src="b.jpg">
<a href="tel:+3230000000">Bel</a> <a href="https://www.instagram.com/noorderhaven.cars/">Instagram</a>
</body></html>"""
ROBOTS = "User-agent: PerplexityBot\nDisallow: /\n\nUser-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n" \
         "Sitemap: https://dealer.example/sitemap.xml\n"
SITEMAP = "<urlset><url><loc>https://dealer.example/</loc></url><url><loc>https://dealer.example/stock</loc></url></urlset>"
SPA = '<!doctype html><html lang="en"><head><title>App</title></head><body><div id="root"></div>' \
      '<script src="/assets/index.js"></script></body></html>'


class Resp:
    def __init__(self, body, url, status=200, ctype="text/html; charset=utf-8"):
        self.body, self.url, self.status = body.encode(), url, status
        self.headers = {"Content-Type": ctype}

    def read(self, n=None):
        return self.body

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def site(pages):
    def opener(req, timeout):
        url = req.full_url
        if url not in pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"not found"))
        body, ctype = pages[url]
        return Resp(body, url, ctype=ctype)
    return opener


class SeoGeoTest(unittest.TestCase):
    def tearDown(self):
        seogeo._open = self.orig

    def setUp(self):
        self.orig = seogeo._open

    def test_a_well_built_local_business(self):
        seogeo._open = site({
            "https://dealer.example/": (HOME, "text/html; charset=utf-8"),
            "https://dealer.example/robots.txt": (ROBOTS, "text/plain"),
            "https://dealer.example/sitemap.xml": (SITEMAP, "application/xml"),
        })
        r = seogeo.analyse("dealer.example", instagram="@noorderhaven.cars")
        checks = {c["check"]: c for c in r["checks"]}
        self.assertEqual(checks["entity markup"]["status"], "PASS")
        self.assertEqual(checks["entity facts"]["earned"], 6)
        self.assertEqual(checks["questions answered"]["status"], "PASS")
        self.assertEqual(checks["sitemap"]["earned"], 4)
        self.assertEqual(checks["alt text"]["status"], "WARN")
        self.assertFalse(r["answer_crawlers"]["PerplexityBot"])
        self.assertTrue(r["answer_crawlers"]["OAI-SearchBot"])
        self.assertFalse(r["training_crawlers"]["GPTBot"])
        self.assertEqual(checks["answer crawlers"]["status"], "WARN")
        self.assertEqual(checks["llms.txt"]["status"], "FAIL")
        self.assertTrue(r["instagram"]["linked_from_page"])
        self.assertTrue(r["instagram"]["in_sameAs"])
        self.assertIn("tweedehands wagens", r["search_terms"])
        self.assertGreaterEqual(r["score"], 85)
        self.assertEqual(r["fix_first"][0]["check"], "llms.txt")

    def test_a_client_rendered_app_scores_low_on_text(self):
        seogeo._open = site({"https://app.example/": (SPA, "text/html")})
        r = seogeo.analyse("https://app.example")
        checks = {c["check"]: c for c in r["checks"]}
        self.assertEqual(checks["text without js"]["earned"], 0)
        self.assertEqual(checks["entity markup"]["earned"], 0)
        self.assertTrue(all(r["answer_crawlers"].values()), "no robots.txt means nothing is blocked")
        self.assertLess(r["score"], 50)
        self.assertIn("SEO/GEO BASELINE", seogeo.render(r))

    def test_unreachable_site(self):
        def down(req, timeout):
            raise urllib.error.URLError("nodename nor servname provided")
        seogeo._open = down
        r = seogeo.analyse("https://gone.example")
        self.assertEqual(r["checks"][0]["earned"], 0)


if __name__ == "__main__":
    unittest.main()
