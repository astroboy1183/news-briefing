# news-briefing

Morning news briefing → Telegram, ~6:13 AM IST via GitHub Actions.

India + US + world/geopolitics headlines from seven RSS feeds, deduped and
filtered to what a professional should know. One agent, one task, one
bot: `@jayanth_news_brief_bot`.

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
- Two crons + dedupe guard: backup at 07:13 IST delivers only if the
  06:13 primary was dropped or failed.

- **Cross-day memory**: candidate links live in `state/seen.json`
  (committed back by the workflow) for 3 days, so a story lingering in
  the feeds is briefed exactly once. Anything shown to the model counts
  as seen — a story it skipped yesterday earned no second chance by
  merely reappearing.
- Tests run in CI on every push (`.github/workflows/tests.yml`).

## Ops

- Schedule: `.github/workflows/news-briefing.yml`
  (`43 0 * * *` UTC = 06:13 IST; backup 07:13)
- Run now: `gh workflow run news-briefing.yml -R astroboy1183/news-briefing`
- Secrets (Actions): `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Local test: `cd ~/agents/news_briefing && <any fleet venv>/bin/python news_briefing.py`
