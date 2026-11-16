---
name: seo-geo
description: >-
  SEO and GEO baseline of a business website: what search engines and AI
  answer engines (ChatGPT, Perplexity, Claude, Google's AI answers) can read
  about the business today, scored out of 100, the fixes in order, and the
  search terms the site is built around. Use when the user asks for an SEO
  audit, a GEO or AI-visibility check, "can ChatGPT find us", a client's
  website baseline before a campaign, or search terms for captions.
---

# seo-geo

One tool lives in this folder and it runs:

```bash
python3 seogeo.py https://client.be --instagram clienthandle
python3 seogeo.py https://client.be --out marketing/instagram/campaigns/<slug>/seo-geo.md
python3 seogeo.py https://client.be --json
```

It reads the homepage, robots.txt, the sitemap and llms.txt, four or five
requests, and scores what it found out of 100.

## What it measures

**SEO, 50 points.** Whether the homepage can be found and understood: it
loads over https, it is indexable, a real title and description, one h1,
the language, a canonical, a sitemap, the mobile viewport, a link-preview
card, alt text, and enough words to say what the business does.

**GEO, 50 points.** Whether an answer engine can use it:

- **Answer crawlers allowed** in robots.txt: Googlebot, Bingbot, OAI-SearchBot,
  ChatGPT-User, PerplexityBot, Perplexity-User, Claude-SearchBot, Claude-User,
  Applebot, DuckAssistBot. Block these and the business is missing from the
  answers they build.
- **The business as an entity** in JSON-LD (LocalBusiness or a subtype, or
  Organization), with its facts: name, address, telephone, url.
- **Profiles linked**: the Instagram account and the others in the entity's
  `sameAs`, and linked on the page. This is how an engine knows the site and
  the account are the same business.
- **Questions answered**: the questions a buyer asks, as headings with short
  answers, ideally with FAQPage markup.
- **Contact facts in plain text**, a `tel:` link and the address.
- **Text without JavaScript**: words in the HTML before any script runs. A
  site that renders everything in the browser scores zero here, because many
  crawlers never run the script.
- **llms.txt**, a small signal, three points.

Training crawlers (GPTBot, ClaudeBot, Google-Extended, Applebot-Extended,
CCBot and others) are listed and never scored. Blocking them is the client's
choice about training data. It does not take the business out of search or
out of the answer engines, which use the crawlers above.

## What it does not measure

One page, no JavaScript rendering, no backlinks, no rankings, no Search
Console data, and no reading of what the engines actually say. Say so in
the report. The real GEO test is asking: give the user five questions a buyer
would type ("best used car dealer in Antwerp for a family car") to run in
ChatGPT, Perplexity and Google, and note whether the business shows up and
what is said about it. Put those five questions and the answers in the
report next to the score.

## Reading the result

- **80 and up**: solid. Fix what is left and move on.
- **60 to 79**: gaps an engine notices. The fix list is ordered by lost points.
- **Under 60**: outside its title, an answer engine knows almost nothing about
  this business.

"Text without js" failing usually means a single-page app. The fix is
prerendering or server rendering, and that is a build job, not a tweak.

## Hand-offs

- **Search terms** go to `/ig-caption` (`caption.py --keywords`), into alt
  text, and into the campaign plan. Instagram search reads captions, and
  search engines index public posts from professional accounts, so the
  account and the site should name the business, the place and the offer the
  same way.
- **Instagram ↔ site**: if the page does not link the account and `sameAs`
  does not list it, that goes in the fix list. It costs ten minutes.
- For a prospect, the fix list doubles as a sales conversation: it is the
  same list a rebuild fixes. Never inflate it.

## Rules

- Report what was measured, with the URL and the date. No invented traffic,
  rankings or "AI visibility scores" from anywhere else.
- It is polite by design, one site at a time. Run a list of sites only when
  the user asks, and slowly.
