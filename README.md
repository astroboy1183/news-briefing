# news-briefing

News → Telegram, two editions a day via GitHub Actions.
One agent, one task, one bot: `@jayanth_news_brief_bot`.

- **06:00 IST sharp** — the full morning briefing
- **21:00 IST** — a tight *evening wrap* of what broke AFTER the morning
  briefing (the seen-memory guarantees zero overlap); silent when
  genuinely nothing new

Forty-one verified feeds, seven sections plus a topline:

```
📰 News — Sat 11 Jul
127 fresh headlines · 41 feeds

🗞 Top: <the single biggest story>          ← also sent as a PHOTO front
                                              page (the article's own
                                              og:image, captioned)

📰 INDIA — 5 bullets      (Hindu, TOI, HT, IE, NDTV, India Today, News18, Scroll)
🏛 POLITICS — 3 bullets   (Indian politics: IE Political Pulse, News18
                           Politics, Hindu Elections)
💼 BUSINESS — 3 bullets   (Mint, ET, ET Markets, Moneycontrol)
📍 HYDERABAD — 3 bullets  (Hindu Telangana, TOI Hyd, Telangana Today, Siasat
                           + Telugu media: NTV, V6 Velugu, Sakshi)
🗽 US POLITICS & IMMIGRATION — 4 bullets
                          (NPR Politics, NYT Politics, Guardian US-politics,
                           Politico Politics + Congress, The Hill —
                           immigration stories ALWAYS get a slot: visas,
                           H-1B, green cards, border, USCIS, and the
                           India-US corridor)
🇺🇸 US — 4 bullets         (non-politics national: NPR, NYT, Guardian, CNN,
                           WaPo, ABC, Axios)
🌍 WORLD — 3 bullets      (BBC, Al Jazeera, Guardian, CNN, France24, DW)
```

The evening wrap uses the same sections with tight caps (Top + ~9
bullets). **Sunday mornings append 🗓 THE WEEK** — up to 5 story arcs
traced from the 7-day briefed memory ("H-1B fee rule: proposed Mon,
pushback Wed, paused Fri").

**👁 Watchlist**: the `NEWS_WATCH` secret holds comma-separated personal
topics (e.g. `H-1B, RBI, Hyderabad metro`). Matching candidates are
👁-marked for the selector, *deterministically forced into the selection
even if the model skips them* (bounded by `WATCH_EXTRA` per section),
and their bullets carry the 👁 prefix. Change topics anytime with
`gh secret set NEWS_WATCH`.

Bullets are written from the ARTICLES, not the headlines — a two-stage
pipeline: a cheap model (`NEWS_MODEL_SELECT`, default haiku) picks the
stories from ~220 candidates, the code fetches the full article text for
just those (boilerplate-stripped, 3k chars; paywalls fall back to the
snippet), and a stronger model (`NEWS_MODEL_WRITE`, default sonnet)
writes 2-3 sentences of concrete substance per story — numbers, names,
consequences — each with its validated source link. Telugu sources are
summarized in English. Two memories keep it honest across days:
`seen.json` (a story is briefed once — and the evening wrap can only
carry post-morning news) and `briefed.json` (what the bullets said — so
developments open with what's NEW, and Sunday can trace the week).

## How the code works

`news_briefing.py`, in pipeline order:

- **`FEEDS`** — `{section: [feed urls]}`, 41 sources, every one verified
  before inclusion (the header comment lists ~15 tested-and-rejected
  feeds, so nobody re-adds a dead one). `TITLES_PER_FEED = 6` caps each
  feed's contribution; editing this dict is the only change needed to
  tune coverage.
- **`edition(now)`** — morning (full briefing, 24h lookback) or evening
  (wrap: `EVENING_CAPS`, 16h lookback) by IST hour. The evening wrap is
  silent when nothing new was gathered — a message always means news
  broke after 6:00.
- **`watch_terms()` / `watch_hit(entry, terms)`** — the `NEWS_WATCH`
  personal watchlist: case-blind substring match over title + snippet.
- **`gather_headlines(seen, lookback)`** — parses each feed with
  `feedparser`; per-feed try/except (a dead feed is skipped), per-entry
  `.get()` (one malformed entry can't sink its feed), `fresh()` drops
  stale items, and anything in the seen-memory never re-enters.
- **`select_stories(headlines, briefed, model, caps)`** — stage 1, cheap
  model, returns chosen indices as JSON. The prompt encodes the editorial
  rules: 👁 candidates always selected (with a *deterministic* code-side
  force as backstop), one story in one section only, multi-feed
  corroboration = importance, outlet variety within a section, politics
  = governance substance not slanging matches, skip cinema filler from
  the Telugu feeds, India-US corridor always kept. An unparseable reply
  falls back to the first N per section — a broken selector costs
  quality, never the briefing.
- **`fetch_article(link)`** — readable text of each selected story
  (tags/boilerplate stripped, `ARTICLE_CHARS = 3000`); `''` on any
  failure (paywalls fall back to the snippet).
- **`write_briefing(selected, briefed, model, caps, ed)`** — stage 2,
  stronger model writes the bullets from real article TEXT, with the
  edition-aware intro, 👁 prefixes, English-only output, and the
  `===STATE===` JSON tail: `{"briefed": [story keys], "top_link": …}`.
- **`split_state(reply)`** — `(text, keys, top_link)`; a malformed tail
  costs the continuity memory and the photo, never the briefing.
- **`gathered_links()` / `validate_links()`** — every emitted URL is
  checked against the set actually handed to the model; hallucinated
  links are replaced with a marker. `top_link` passes the same check.
- **`fetch_og_image(link)` / `send_photo(url, caption)`** — the front
  page: the Top story's own `og:image`, sent via Telegram `sendPhoto`
  captioned with the 🗞 line. Pure enrichment — any failure and the text
  briefing (which repeats the Top line) goes out unchanged.
- **`week_in_review(briefed, model)`** — Sunday morning only: traces
  story arcs across the 7-day briefed memory. Needs 3+ days of history;
  outputs `NONE` (machine-checkable) when no real arcs exist; guarded so
  it can never sink the briefing.
- **`main()`** — edition → gather → select → fetch → write → validate →
  (photo) → send → save state. State is saved AFTER the send, so a state
  failure never costs the message.
- **`agentlib.py`** (vendored) — `ask_llm()` one-shot model call;
  `send_telegram()` chunked sends.

## Design notes

- Tech news is deliberately excluded — the tech-news agent covers it at
  7:00; cricket has its own agent too. One agent, one task. Politics is
  split by country: 🏛 POLITICS is Indian, 🗽 US POLITICS & IMMIGRATION
  is American (with immigration guaranteed a slot — no keyless
  immigration-only feed exists, NYT Immigration and USCIS are both 404,
  so the guarantee lives in the selector rule + the 👁 watchlist).
- The evening wrap exists because India's news cycle happens 9:00–21:00
  — a morning-only briefing reads most Indian news 12–20 hours stale.
  The seen-memory (all morning candidates are marked seen) makes the
  wrap new-only by construction, not by prompt.
- The watchlist guarantee is deterministic on purpose: a 👁 story the
  selector skips is forced back in by code, the same pattern as
  mail-digest's code-guaranteed VIP block.
- `seen.json` keeps 3 days (feeds re-serve stories for a day or two);
  `briefed.json` keeps 7 so Sunday's 🗓 THE WEEK can trace arcs.
- The photo front page costs one extra fetch of a page whose article we
  already pulled — `og:image` is near-universal on news sites. Telegram
  fetches the image URL itself; on any failure the plain-text briefing
  is unaffected.
- Feed rejections are documented in the `FEEDS` comment (The Wire, HT
  politics, Eenadu, Deccan Chronicle, Hans India and ten more) — every
  candidate was probed for reachability, entry count and freshness
  before inclusion; TOI's "politics" feed turned out to be their
  business feed and was rejected for misfiling.
- Tests run in CI on every push (`.github/workflows/tests.yml`).

## Ops

- Schedule: fleet-scheduler dispatches 06:00 and 21:00 IST sharp; backup
  crons `30 0` / `30 1` / `30 15` / `30 16 * * *` UTC (06:00 / 07:00 /
  21:00 / 22:00 IST). The dedupe guard uses a **3-hour window** (not
  "today") so each backup pairs with its own edition's primary while the
  evening wrap runs after a successful morning.
- Run now: `gh workflow run news-briefing.yml -R astroboy1183/news-briefing`
- Secrets (Actions): `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, `NEWS_WATCH` (optional watchlist, comma-separated)
- Local test: `cd ~/agents/news_briefing && <any fleet venv>/bin/python news_briefing.py`
