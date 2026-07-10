# news-briefing

Morning news briefing → Telegram, 6:00 AM IST sharp via GitHub Actions.
One agent, one task, one bot: `@jayanth_news_brief_bot`.

Thirty verified feeds, five sections plus a topline:

```
📰 News — Fri 10 Jul
94 fresh headlines · 30 feeds

🗞 Top: <the single biggest story today>

📰 INDIA — 5 bullets      (Hindu, TOI, HT, IE, NDTV, India Today, News18, Scroll)
💼 BUSINESS — 3 bullets   (Mint, ET, ET Markets, Moneycontrol)
📍 HYDERABAD — 2 bullets  (Hindu Telangana, TOI Hyd, Telangana Today, Siasat)
🇺🇸 US — 5 bullets         (NPR, NYT, Guardian, CNN, WaPo, ABC, The Hill,
                           Axios — India-US corridor stories always kept:
                           visas, H-1B, immigration, trade)
🌍 WORLD — 3 bullets      (BBC, Al Jazeera, Guardian, CNN, France24, DW)
```

Bullets are written from the ARTICLES, not the headlines — a two-stage
pipeline: a cheap model (`NEWS_MODEL_SELECT`, default haiku) picks the
stories from ~180 candidates, the code fetches the full article text for
just those ~18 (boilerplate-stripped, 3k chars; paywalls fall back to
the snippet), and a stronger model (`NEWS_MODEL_WRITE`, default sonnet)
writes 2-3 sentences of concrete substance per story — numbers, names,
consequences — each with its validated source link. Two memories keep it
honest across days: `seen.json` (a story is briefed once) and
`briefed.json` (what the bullets said — so developments open with what's
NEW instead of re-explaining).

## How the code works

`news_briefing.py`, in pipeline order:

- **`FEEDS`** — `{section: [feed urls]}`: The Hindu + Times of India +
  Hindustan Times (india), NPR + NYT (us), BBC World + Al Jazeera (world).
  Editing this dict is the only change needed to tune coverage.
  `TITLES_PER_FEED = 8` caps how much each feed contributes;
  `LOOKBACK_HOURS = 24` bounds how far back a story may be.
- **`fresh(entry, cutoff)`** — keeps only entries stamped within the last
  `LOOKBACK_HOURS` (`published_parsed`, falling back to `updated_parsed`).
  Undated entries are kept — some feeds omit timestamps and dropping them
  would lose real news. Stops stale/evergreen feed items from leaking into
  the prompt.
- **`gather_headlines()`** — parses each feed with `feedparser`, takes up
  to 8 fresh `title | link` lines per feed. Three layers of defense: the
  whole feed is wrapped in `try/except` (a dead feed is skipped, the other
  feeds in that section still deliver), entries are read with
  `.get("title")` so one malformed entry can't sink its feed, and `fresh()`
  filters out anything older than a day.
- **`gathered_links()` / `validate_links()`** — the set of source URLs
  actually handed to the model is kept, and after the model replies every
  emitted `http(s)` token is checked against it (pure stdlib string
  matching). Any link that was never in a source feed is replaced with
  `[link removed: not in source feeds]`, so a hallucinated URL can never
  reach Telegram. Covered by `test_links.py` (offline, no deps).
- **`summarize(headlines)`** — one model call (`max_tokens=4000` for
  headroom). The prompt gives the raw
  `title | link` lines per section and demands fixed sections with hard
  caps: INDIA (4 bullets, dedupe overlap, drop clickbait), US (3,
  national politics/economy/policy only), GEOPOLITICS (3, prefer
  India/US relevance). Every bullet is followed by its story link on its
  own line, copied verbatim (never invented). A section whose feeds all
  failed renders as a single "unavailable" line.
- **`main()`** — gather → summarize → validate links → send, with a
  headline count in the header. If *every* feed is unreachable it skips the
  model and sends a one-liner instead.
- **`agentlib.py`** (vendored) — `ask_llm()` one-shot model call;
  `send_telegram()` chunked sends.

## Design notes

- Tech news is deliberately excluded — the tech-news agent covers it in
  depth at 7:00; cricket has its own agent too. One agent, one task.
- The fleet-scheduler dispatches 06:00 IST sharp; the GitHub crons
  (06:00 / 07:00 IST) are guarded backups.

- **Cross-day memory**: candidate links live in `state/seen.json`
  (committed back by the workflow) for 3 days, so a story lingering in
  the feeds is briefed exactly once. Anything shown to the model counts
  as seen — a story it skipped yesterday earned no second chance by
  merely reappearing.
- Tests run in CI on every push (`.github/workflows/tests.yml`).

- **Wider India sourcing**: Indian Express and Mint join the three
  mainstream feeds — policy/economy depth, not just headlines
  (verified 9 Jul 2026).

## Ops

- Schedule: fleet-scheduler dispatches 06:00 IST sharp; backup crons
  `30 0 * * *` / `30 1 * * *` UTC (06:00 / 07:00 IST)
- Run now: `gh workflow run news-briefing.yml -R astroboy1183/news-briefing`
- Secrets (Actions): `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Local test: `cd ~/agents/news_briefing && <any fleet venv>/bin/python news_briefing.py`
