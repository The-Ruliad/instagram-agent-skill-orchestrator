---
name: ig-campaign
description: >-
  Run a client's Instagram campaign end to end: the brief, an SEO/GEO baseline
  of the client's website, the plan, scripts and captions from the ig-* skills,
  images and video from Higgsfield, the humanizer, then posting through Meta's
  official Instagram API only after an explicit yes, and live insights after.
  Use when the user says "campaign", "launch on Instagram", "run Instagram for
  <client>", "post this", "connect Instagram", "how is the campaign doing", or
  wants an Instagram report.
---

# ig-campaign

The other skills in this pack write. This one runs the campaign: it decides
what gets written, gets the assets made, puts every post in front of the user,
posts what they approve, and reads the numbers back.

It adds the one thing the original pack refused to do, posting, and it does it
the only way that does not get an account restricted: Meta's own API, for a
professional account, one post at a time, after a yes. Every call goes through
`igapi.py` in this folder. Nothing else touches Instagram.

## The client folder

One client per project. Everything lives in `marketing/instagram/` at the
project root:

```
marketing/instagram/
  voice.md             the client's voice, positions, proof and limits. Every ig-* skill reads it.
  plan.md  swipe.md    this week (/ig-plan) and what works in the niche (/ig-viral)
  log.md               every post igapi.py published; published.jsonl is the same, for scripts
  insights/            audits pulled from the API
  campaigns/<slug>/    brief.md, seo-geo.md, plan.md, posts/NN-<slug>/, report-*.json
  .secrets/token.json  the access token: mode 600, git-ignored, never printed, never pasted
```

`python3 igapi.py where` prints the folder and `python3 igapi.py init` creates
it with a `voice.md` to fill in. A client is its own project folder. Never keep
two clients in one: the token in the folder decides which account a post lands
on.

## Connecting an account, once per client

The account must be professional (Business or Creator). The app is The
Ruliad's Meta app "The Ruliad Campaigns" (app 1170679578806876, owned by the
business portfolio The Ruliad BV) with the "Manage messaging & content on
Instagram" use case. Apps created since late 2026 get "API setup with Unified
login": its login configuration, "Instagram API (unified login)", carries
`instagram_basic`, `instagram_content_publish`, `instagram_manage_comments`,
`instagram_manage_insights`, `instagram_manage_messages`, `ads_read` and
`ads_management`.

1. App Dashboard → App roles → Roles → Add people → **Instagram Tester** →
   the client's handle. The client accepts it in Instagram under Settings →
   Apps and websites → Tester invites. Instagram may first show its EU
   ad-data choice; that choice belongs to the account owner. Standard Access
   covers accounts we manage and have added to the app; anything else needs
   App Review for Advanced Access.
2. Use cases → Instagram API → Customize → API setup with Unified login →
   step 2 → "Generate token" next to the handle. The owner approves in the
   window Meta opens. In the "Token generated" box, tick "I understand" and
   click **Copy**: the box shows the token once.
3. From the project root, hand it to igapi.py from the clipboard, never on a
   command line and never in a chat message (the browser extension refuses to
   read tokens anyway), then clear the clipboard:
   `pbpaste | python3 igapi.py connect --token-stdin --host graph.facebook.com --user-id <the account id the dashboard shows> --system-user`
4. `python3 igapi.py status --check` must print the client's handle.

A Unified-login token is a system-user token and never expires, so there is
nothing to refresh. If one leaks, remove the app under the account's
Instagram → Apps and websites, then generate a new one. Older apps on
"Instagram login" get 60-day tokens on graph.instagram.com: connect without
`--host`, and run `igapi.py refresh` monthly.

Never ask for, store or type an Instagram or Facebook password. A step that
needs a login is the user's or the client's.

## Before the run

In TR_OS, write the session board first (the dev-board skill) and put
everything that spends or posts in its Approvals phase, then run
`bash ~/TR_OS/scripts/tros-preflight` once:

```
## Approvals
- [ ] [grant:spend_generation max=40] Campaign assets on Higgsfield: 6 images, 2 reels
- [ ] [grant:instagram_publish max=6] Post the six approved posts of <slug>
```

`instagram_publish` is the gate on any `igapi.py ... publish ... --confirm`
command line. Size `max` to the posts in the plan, not more.

## The run

### 1. Brief: `campaigns/<slug>/brief.md`

Slug is `YYYY-MM-<word>`. Ask for these and write them down:

- The client, what they sell and to whom.
- **One goal**: awareness, leads, bookings, a launch, hiring. One.
- **The number that says it worked, and by when.** "40 people comment
  OFFERTE by 15 December" is a target. "More engagement" is not.
- The period, the posting rhythm, and the Higgsfield credits for assets.
- The website, three competitors, and ten accounts worth being seen by.
- What the client cannot say: regulated claims, prices, names, NDAs. It goes
  into voice.md under Off limits.

### 2. Baseline: `campaigns/<slug>/seo-geo.md` and the before-numbers

- Run `/seo-geo` on the client's website. It reports what search engines and
  AI answer engines can read about the client today, and the search terms the
  site is built around. Those terms become the caption search terms
  (`caption.py --keywords`) and the alt text. Instagram lets search engines
  index public posts from professional accounts, so captions and alt text are
  now part of the client's search footprint, and the site and the account
  should name the business, the place and the offer the same way.
- `igapi.py whoami`, `igapi.py account --days 28` and `igapi.py audit --limit 30`
  give the before-numbers. The campaign is judged against the account's own
  median, never against an industry average.

### 3. Plan: `campaigns/<slug>/plan.md`

Run `/ig-plan` with the brief, the baseline and voice.md. In a campaign every
post serves the one goal. Each slot gets a date, format, job (proof, teach,
opinion, story, offer), hook formula, search term, asset brief and the one ask.

### 4. Write: `campaigns/<slug>/posts/NN-<slug>/`

`script.md` from `/ig-reel`, `/ig-carousel` or `/ig-story`; `caption.txt` from
`/ig-caption`; then `/ig-human` on both, and `caption.py --keywords` with the
search terms from step 2. Nothing goes to step 5 with a FAIL on it.

### 5. Make the assets

- **Images**: Higgsfield `generate_image` with `gpt_image_2_6`,
  `output_format: "jpeg"`, aspect `4:5` for feed posts and carousels, `9:16`
  for stories. JPEG is not optional: Instagram's API refuses anything else.
  Preflight with `get_cost: true` and stay inside the approved credits.
- **Video**: Higgsfield `generate_video` (`seedance_2_5`, `aspect_ratio:
  "9:16"`). Anything with a character, a voice-over or a product scene goes
  through `/marketing-media-engine`, with this campaign as its business case.
- **Type and layout** on carousels and covers: the client's own colours, type
  and logo, set with the design skills (better-typography, better-layout).
- The result link Higgsfield returns is public, and it is what igapi.py posts.
  Record each asset in the post's `assets.json`: job id, model, prompt, link,
  credits.
- Anything generated is posted with `--ai-generated`.

### 6. The review sheet, which is where the yes comes from

For each post, run the dry run: the publish command without `--confirm`.

```bash
python3 igapi.py publish image --url <jpeg link> --caption-file caption.txt \
  --alt-text "..." --ai-generated --campaign <slug>
```

It checks that the media is public, JPEG, inside the feed's 4:5 to 1.91:1
and under 8 MB. It shows the caption the way the feed cuts it, runs the
caption linter and shows how many API posts are left today. Show the user
the asset link, the caption box and that verdict, post by post. Ask them for
a yes. "Looks good" on a list is a yes to that list, as it stood.

On a client's account, the client approves the campaign in writing before
the first post. Ask the user to confirm they have it.

### 7. Post

```bash
python3 igapi.py publish image --url <jpeg link> --caption-file caption.txt \
  --alt-text "..." --ai-generated --campaign <slug> --confirm
```

One command per post, on its own, nothing chained. Under TR_OS that line
waits on the `instagram_publish` gate. Never route around the gate, never
loop posts through a script, never post a variant the user did not see. The
post lands in `log.md` and `published.jsonl` with its permalink.

- Carousels: `publish carousel --url A --url B ...` (2 to 20 items).
- Reels: `publish reel --url <mp4 link>`. When two hooks are close, post one
  with `--trial manual`: a trial reel goes to non-followers first, which is the
  cheapest hook test Instagram offers.
- Stories: `publish story --url <link>`, no caption.
- The API posts immediately; there is no scheduling. Post each one when the
  plan says, or say plainly that it went out early.
- `--container-only` builds and checks a container without posting. It
  expires in 24 hours. Use it to test a new kind of post.

### 8. Monitor

- At 24 hours, 72 hours and 7 days: `igapi.py report <slug> --baseline 25`.
  Instagram's numbers lag up to 48 hours, so the 24-hour read is a pulse,
  not a verdict.
- Comments: `igapi.py comments <media id>`, then `/ig-reply` drafts the
  answers. The user posts them.
- At the end: `igapi.py audit --limit 30`, then `/ig-audit` on that file.
  Write `campaigns/<slug>/report.md`: the target against the result, the
  posts that worked (outlier multiple, sends per reach), what to stop, and
  the next campaign.
- The API has no retention graph and no hold at 3 seconds. For the reels
  that matter, ask the user for the screenshot from the app.

## Rules

- **Meta's API, and nothing else.** No browser automation of Instagram, no
  scraping, no follow, like or comment bots, no passwords. That is what gets
  accounts restricted, and it is why the original pack would not post at all.
- **No post without a yes for that post.** A dry run is not a yes. A plan is
  not a yes.
- **Nothing fabricated.** No invented numbers, clients, reviews or results
  under the client's name. A draft that needs a number it does not have comes
  back with `{{number}}` in it.
- **The AI label goes on** anything Higgsfield made.
- **One client, one folder, one token.**

## Not built yet

- **Paid campaigns.** Boosts and ads need a Meta ad account and the Marketing
  API. This pack plans and measures organic campaigns only.
- **DMs and comment replies through the API.** They need more scopes and, for
  DMs, App Review. `/ig-dm` and `/ig-reply` write; the user sends.
- **Scheduling.** See step 7.
