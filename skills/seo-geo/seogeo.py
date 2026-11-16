#!/usr/bin/env python3
"""
seogeo.py - what search engines and AI answer engines can read about a
business from its website, scored out of 100, with the fixes in order.

SEO half: can the homepage be found and understood. Indexable, title,
description, one H1, language, canonical, sitemap, mobile, link previews,
alt text, enough words.

GEO half (generative engine optimisation): can an answer engine use it.
Its crawlers allowed in robots.txt, the business described as an entity in
structured data, the facts a person would ask stated plainly, FAQ-shaped
content, text that exists without JavaScript, llms.txt.

It reads one page, robots.txt, the sitemap and llms.txt: four or five
requests, stdlib only. It does not run JavaScript. A site that only exists
after JS runs scores low here, and that is a real finding, because many
crawlers do not run it either.

Usage
  python3 seogeo.py https://example.be
  python3 seogeo.py example.be --instagram theruliad.eu
  python3 seogeo.py https://example.be --out campaigns/2026-11-launch/seo-geo.md
  python3 seogeo.py https://example.be --json
"""

import argparse
import datetime as dt
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from collections import Counter
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
TIMEOUT = 20
MAX_BYTES = 4_000_000

# Crawlers that fetch pages to answer a person's question or build a search
# index an assistant answers from. Blocking these makes the business invisible
# in those answers, so they are scored.
ANSWER_BOTS = ["Googlebot", "Bingbot", "OAI-SearchBot", "ChatGPT-User", "PerplexityBot",
               "Perplexity-User", "Claude-SearchBot", "Claude-User", "Applebot", "DuckAssistBot"]
# Crawlers that collect training data. Blocking them is a policy choice, so it
# is reported and never scored.
TRAINING_BOTS = ["GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "CCBot",
                 "meta-externalagent", "Bytespider", "Amazonbot"]

ENTITY_TYPES = {"organization", "corporation", "localbusiness", "onlinebusiness", "onlinestore",
                "professionalservice", "ngo", "educationalorganization", "medicalorganization"}
ENTITY_SUFFIXES = ("business", "store", "dealer", "agency", "restaurant", "shop", "service",
                   "clinic", "office", "contractor", "salon")

STOPWORDS = set("""
a an and are as at be by for from has have in is it its of on or our that the this to we with you your
de het een en van in op te voor met is zijn uw je jij wij we ons onze ook of als bij aan om door naar
dat die dit deze er niet meer dan nog maar tot over uit wat wie hoe waar welke
le la les un une des et en du au aux pour par sur avec dans est sont nous vous votre vos notre nos ce
cette qui que plus ou
home welkom welcome accueil contact menu
""".split())


class Page(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.title = ""
        self.meta = {}
        self.canonical = None
        self.headings = {"h1": [], "h2": [], "h3": []}
        self.jsonld = []
        self.links = []
        self.images = 0
        self.images_no_alt = 0
        self.summaries = 0
        self.text = []
        self._stack = []
        self._capture = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang") or None
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = a.get("content", "").strip()
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonical = a.get("href") or None
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "img":
            self.images += 1
            if not a.get("alt", "").strip():
                self.images_no_alt += 1
        elif tag == "summary":
            self.summaries += 1
        if tag in ("title", "h1", "h2", "h3") or (tag == "script" and "ld+json" in a.get("type", "")):
            self._capture, self._buf = ("jsonld" if tag == "script" else tag), []
        if tag not in ("meta", "link", "img", "br", "hr", "input", "source"):
            self._stack.append(tag)

    def handle_endtag(self, tag):
        if self._capture and (tag == self._capture or (tag == "script" and self._capture == "jsonld")):
            text = "".join(self._buf).strip()
            if self._capture == "title":
                self.title = re.sub(r"\s+", " ", text)
            elif self._capture == "jsonld":
                self.jsonld.append(text)
            else:
                self.headings[self._capture].append(re.sub(r"\s+", " ", text))
            self._capture = None
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self._capture:
            self._buf.append(data)
        if not any(t in self.SKIP for t in self._stack) and data.strip():
            self.text.append(data.strip())


def decode(raw, encoding_header):
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif encoding_header == "deflate":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def _open(req, timeout):
    # The seam the tests replace; nothing else here opens a socket.
    return urllib.request.urlopen(req, timeout=timeout)


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "nl-BE,nl;q=0.9,fr;q=0.8,en;q=0.7", "Accept-Encoding": "gzip"})
    start = time.time()
    try:
        with _open(req, TIMEOUT) as resp:
            raw = decode(resp.read(MAX_BYTES), (resp.headers.get("Content-Encoding") or "").lower())
            status, final, headers = resp.status, resp.geturl(), resp.headers
    except urllib.error.HTTPError as err:
        err.close()
        return {"status": err.code, "url": url, "final": url, "headers": err.headers or {}, "text": "",
                "seconds": round(time.time() - start, 2)}
    except (urllib.error.URLError, OSError) as err:
        return {"status": 0, "url": url, "final": url, "headers": {}, "text": "",
                "error": str(getattr(err, "reason", err)), "seconds": round(time.time() - start, 2)}
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", headers.get("Content-Type") or "")
    if m:
        charset = m.group(1)
    return {"status": status, "url": url, "final": final, "headers": headers,
            "text": raw.decode(charset, "replace"), "seconds": round(time.time() - start, 2)}


def normalise(raw):
    raw = raw.strip()
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def types_of(node):
    t = node.get("@type")
    return [x for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]


def walk_jsonld(blobs):
    nodes = []
    for blob in blobs:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if "@type" in item:
                    nodes.append(item)
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
    return nodes


def is_entity(node):
    for t in types_of(node):
        low = t.lower()
        if low in ENTITY_TYPES or low.endswith(ENTITY_SUFFIXES):
            return True
    return False


def robots_rules(base, text):
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    home = base + "/"

    def allowed(bot):
        return rp.can_fetch(bot, home)
    sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", text)
    return ({b: allowed(b) for b in ANSWER_BOTS}, {b: allowed(b) for b in TRAINING_BOTS}, sitemaps)


def keywords(page):
    """Word pairs and words from the title, description and headings. Pairs never
    cross a title separator or a sentence, so "advisory | Name" is not a term."""
    segments = [page.title, page.meta.get("description", "")] + page.headings["h1"] + page.headings["h2"]
    pairs, singles = Counter(), Counter()
    for segment in segments:
        for chunk in re.split(r"[|—–:;,.!?()/]+|\s-\s", segment.lower()):
            words = [w for w in re.findall(r"[^\W\d_][\w'-]+", chunk) if w not in STOPWORDS and len(w) > 2]
            singles.update(words)
            pairs.update(f"{a} {b}" for a, b in zip(words, words[1:]))
    return [p for p, _ in pairs.most_common(8)], [w for w, _ in singles.most_common(10)]


def check(group, key, points, earned, detail, fix):
    earned = max(0, min(points, round(earned, 1)))
    status = "PASS" if earned >= points else "FAIL" if earned == 0 else "WARN"
    return {"group": group, "check": key, "points": points, "earned": earned,
            "status": status, "detail": detail, "fix": fix if status != "PASS" else ""}


def analyse(url, instagram=None):
    url = normalise(url)
    root = fetch(url)
    final = root["final"]
    parts = urllib.parse.urlsplit(final)
    base = f"{parts.scheme}://{parts.netloc}"
    page = Page()
    if root["text"]:
        page.feed(root["text"])
    words = len(" ".join(page.text).split())
    nodes = walk_jsonld(page.jsonld)
    types = sorted({t for n in nodes for t in types_of(n)})
    entities = [n for n in nodes if is_entity(n)]

    robots = fetch(base + "/robots.txt")
    robots_text = robots["text"] if robots["status"] == 200 and "<html" not in robots["text"][:500].lower() else ""
    answer, training, sitemaps = robots_rules(base, robots_text)
    sitemap_url = sitemaps[0] if sitemaps else base + "/sitemap.xml"
    sitemap = fetch(sitemap_url)
    sitemap_locs = len(re.findall(r"<loc>", sitemap["text"])) if sitemap["status"] == 200 else 0
    llms = fetch(base + "/llms.txt")
    has_llms = llms["status"] == 200 and llms["text"].strip() and "<html" not in llms["text"][:500].lower()

    headers = root["headers"]
    x_robots = (headers.get("X-Robots-Tag") or "") if hasattr(headers, "get") else ""
    noindex = "noindex" in (page.meta.get("robots", "") + " " + x_robots).lower()
    links = [urllib.parse.urljoin(final, h) for h in page.links]
    ig_links = sorted({l for l in links if re.match(r"https?://(www\.)?instagram\.com/[^/?#]+", l)})
    same_as = sorted({s for n in entities for s in (n.get("sameAs") if isinstance(n.get("sameAs"), list)
                                                      else [n.get("sameAs")]) if isinstance(s, str)})
    questions = [h for h in page.headings["h2"] + page.headings["h3"] if h.strip().endswith("?")]
    tel = any(l.startswith("tel:") for l in page.links)
    mail = any(l.startswith("mailto:") for l in page.links)

    checks = []
    redirects = 0 if root["url"].rstrip("/") == final.rstrip("/") else 1
    ok = root["status"] == 200
    checks.append(check("SEO", "loads", 6, 6 if ok and parts.scheme == "https" else 3 if ok else 0,
                        f"HTTP {root['status']} at {final} in {root['seconds']}s"
                        + (" (redirected)" if redirects else "") + (f": {root.get('error')}" if root.get("error") else ""),
                        "serve the homepage over https with a 200"))
    checks.append(check("SEO", "indexable", 8, 0 if noindex or not answer.get("Googlebot", True) else 8,
                        "noindex set" if noindex else "Googlebot blocked in robots.txt"
                        if not answer.get("Googlebot", True) else "no noindex, Googlebot allowed",
                        "remove noindex / the Disallow that hides the homepage"))
    tl = len(page.title)
    checks.append(check("SEO", "title", 5, 5 if 15 <= tl <= 65 else 2 if tl else 0,
                        f"{tl} chars: {page.title[:70]!r}" if tl else "no <title>",
                        "a 15-65 character title: what, where, who (\"Used cars in Antwerp | Name\")"))
    dl = len(page.meta.get("description", ""))
    checks.append(check("SEO", "description", 4, 4 if 70 <= dl <= 160 else 2 if dl else 0,
                        f"{dl} chars" if dl else "no meta description",
                        "a 70-160 character description that answers \"why this business\""))
    h1 = len(page.headings["h1"])
    checks.append(check("SEO", "one h1", 4, 4 if h1 == 1 else 2 if h1 > 1 else 0,
                        f"{h1} h1: {page.headings['h1'][:2]}" if h1 else "no h1",
                        "exactly one h1 that names the offer and the place"))
    checks.append(check("SEO", "language", 3, 3 if page.lang else 0, f"lang={page.lang}" if page.lang
                        else "no lang attribute", "set <html lang=\"nl-BE\"> (or the page's language)"))
    checks.append(check("SEO", "canonical", 3, 3 if page.canonical else 0, page.canonical or "none",
                        "add <link rel=\"canonical\"> with the page's own absolute URL"))
    checks.append(check("SEO", "sitemap", 4, 4 if sitemap_locs else 0,
                        f"{sitemap_locs} URLs in {sitemap_url}" if sitemap_locs else f"nothing at {sitemap_url}",
                        "publish /sitemap.xml and name it in robots.txt"))
    checks.append(check("SEO", "mobile", 3, 3 if "viewport" in page.meta else 0,
                        "viewport set" if "viewport" in page.meta else "no viewport meta",
                        "add <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"))
    og = [k for k in ("og:title", "og:image") if page.meta.get(k)]
    checks.append(check("SEO", "link preview", 4, 2 * len(og), f"{', '.join(og) or 'no og:title / og:image'}",
                        "og:title and og:image, so a link in a bio or a DM shows a card"))
    alt_share = 1 - page.images_no_alt / page.images if page.images else 1
    checks.append(check("SEO", "alt text", 3, 3 if alt_share >= 0.8 else 3 * alt_share,
                        f"{page.images - page.images_no_alt} of {page.images} images have alt text"
                        if page.images else "no images in the HTML",
                        "describe every meaningful image in its alt text"))
    checks.append(check("SEO", "words", 3, 3 if words >= 250 else 3 * words / 250,
                        f"{words} words of visible text", "say what the business does in at least 250 words"))

    allowed = [b for b, ok_ in answer.items() if ok_]
    checks.append(check("GEO", "answer crawlers", 12, 12 * len(allowed) / len(answer),
                        f"{len(allowed)} of {len(answer)} allowed"
                        + (f"; blocked: {', '.join(b for b in answer if not answer[b])}" if len(allowed) < len(answer) else ""),
                        "let search and answer bots read the site (robots.txt); training bots are a separate choice"))
    checks.append(check("GEO", "entity markup", 10, 10 if entities else 0,
                        f"types: {', '.join(types) or 'no structured data'}",
                        "describe the business in JSON-LD: LocalBusiness (or its subtype) or Organization"))
    facts = {}
    for n in entities:
        for key in ("name", "address", "telephone", "url", "openingHours", "openingHoursSpecification", "geo"):
            if n.get(key):
                facts[key] = True
    want = ["name", "address", "telephone", "url"]
    have = [k for k in want if facts.get(k)]
    checks.append(check("GEO", "entity facts", 6, 6 * len(have) / len(want) if entities else 0,
                        f"has {', '.join(have) or 'none'}" + ("; opening hours" if facts.get("openingHours")
                                                              or facts.get("openingHoursSpecification") else ""),
                        "name, address, telephone and url in the entity, plus opening hours if people visit"))
    ig_in_same = [s for s in same_as if "instagram.com" in s]
    checks.append(check("GEO", "profiles linked", 4, 4 if same_as else 2 if ig_links else 0,
                        (f"sameAs: {len(same_as)} profile(s)" if same_as else "no sameAs")
                        + (f"; links to {', '.join(ig_links[:2])}" if ig_links else "; no Instagram link"),
                        "list the Instagram and other profiles in the entity's sameAs, and link them on the page"))
    faq = "FAQPage" in types or len(questions) >= 3 or page.summaries >= 3
    checks.append(check("GEO", "questions answered", 6, 6 if faq else 2 if questions or page.summaries else 0,
                        f"{len(questions)} question headings, {page.summaries} accordions"
                        + (", FAQPage markup" if "FAQPage" in types else ""),
                        "answer the five questions buyers ask, as headings with short answers (and FAQPage markup)"))
    checks.append(check("GEO", "contact facts", 4, (2 if tel else 0) + (2 if mail or facts.get("address") else 0),
                        f"tel link {'yes' if tel else 'no'}, mail or address {'yes' if mail or facts.get('address') else 'no'}",
                        "a tel: link and the address in plain text on the page"))
    checks.append(check("GEO", "text without js", 5, 5 if words >= 150 else 5 * words / 150,
                        f"{words} words in the HTML before any script runs",
                        "render the content on the server; many crawlers do not run JavaScript"))
    checks.append(check("GEO", "llms.txt", 3, 3 if has_llms else 0, "present" if has_llms else "absent",
                        "publish /llms.txt: a short plain-text summary of who the business is and its key pages"))

    seo = sum(c["earned"] for c in checks if c["group"] == "SEO")
    geo = sum(c["earned"] for c in checks if c["group"] == "GEO")
    pairs, singles = keywords(page)
    fixes = sorted((c for c in checks if c["status"] != "PASS"), key=lambda c: c["earned"] - c["points"])
    result = {
        "url": url, "final_url": final, "checked": dt.date.today().isoformat(),
        "score": round(seo + geo), "seo": round(seo, 1), "geo": round(geo, 1),
        "checks": checks, "fix_first": [{"check": c["check"], "lost": round(c["points"] - c["earned"], 1),
                                         "fix": c["fix"]} for c in fixes],
        "answer_crawlers": answer, "training_crawlers": training,
        "structured_data_types": types, "same_as": same_as, "instagram_links": ig_links,
        "search_terms": pairs, "top_words": singles, "title": page.title,
        "description": page.meta.get("description", ""), "h1": page.headings["h1"],
    }
    if instagram:
        handle = instagram.lstrip("@").lower()
        linked = any(handle in l.lower() for l in ig_links)
        in_same = any(handle in s.lower() for s in ig_in_same)
        result["instagram"] = {"handle": handle, "linked_from_page": linked, "in_sameAs": in_same}
    return result


def render(r):
    out = []
    host = urllib.parse.urlsplit(r["final_url"]).netloc
    head = f"SEO/GEO BASELINE  ·  {host}  ·  {r['checked']}"
    out += [head, "=" * len(head),
            f"SCORE  {r['score']}/100    SEO {r['seo']:g}/50    GEO {r['geo']:g}/50", ""]
    for group in ("SEO", "GEO"):
        out.append(group)
        for c in (c for c in r["checks"] if c["group"] == group):
            out.append(f"  {c['status']:<5} {c['earned']:>4g}/{c['points']:<3} {c['check']:<20} {c['detail']}")
        out.append("")
    fmt = lambda d: " · ".join(f"{b} {'ok' if ok else 'BLOCKED'}" for b, ok in d.items())
    out += ["AI CRAWLERS (robots.txt)", f"  answer engines  {fmt(r['answer_crawlers'])}",
            f"  training        {fmt(r['training_crawlers'])}  (a policy choice, not scored)", ""]
    if r.get("instagram"):
        ig = r["instagram"]
        out += [f"INSTAGRAM @{ig['handle']}  linked from the page: {'yes' if ig['linked_from_page'] else 'no'}"
                f"  ·  in sameAs: {'yes' if ig['in_sameAs'] else 'no'}", ""]
    if r["fix_first"]:
        out.append("FIX FIRST")
        for n, f in enumerate(r["fix_first"][:8], 1):
            out.append(f"  {n}. ({f['lost']:g} pts) {f['check']}: {f['fix']}")
        out.append("")
    out += ["SEARCH TERMS THE SITE IS BUILT AROUND",
            "  " + (", ".join(f'"{t}"' for t in r["search_terms"]) or "(none found: the title and headings say little)"),
            "  words: " + ", ".join(r["top_words"]),
            "  → use them as caption search terms (caption.py --keywords) and in alt text."]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="SEO and GEO baseline of a business website.")
    ap.add_argument("url")
    ap.add_argument("--instagram", help="the business's handle, to check the site links to it")
    ap.add_argument("--out", help="write the report to a .md or .json file as well")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = analyse(args.url, args.instagram)
    text = json.dumps(result, indent=2, ensure_ascii=False) if args.json else render(result)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(result, indent=2, ensure_ascii=False) if args.out.endswith(".json")
                     else "```\n" + render(result) + "\n```\n")
    sys.exit(0 if result["final_url"] and result["checks"][0]["earned"] else 1)


if __name__ == "__main__":
    main()
