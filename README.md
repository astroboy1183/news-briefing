# news-briefing

Morning news briefing → Telegram, ~6:13 AM IST via GitHub Actions.

India + US + world/geopolitics headlines from six RSS feeds, deduped and
filtered to what a professional should know. One agent, one task, one
bot: `@jayanth_news_brief_bot`.

## How the code works

`news_briefing.py`, in pipeline order:

- **`FEEDS`** — `{section: [feed urls]}`: The Hindu + Times of India
  (india), NPR + NYT (us), BBC World + Al Jazeera (world). Editing this
  dict is the only change needed to tune coverage.
  `TITLES_PER_FEED = 8` caps how much each feed contributes.
- **`gather_headlines()`** — parses each feed with `feedparser`, takes up
  to 8 titles per feed. Two layers of defense: the whole feed is wrapped
  in `try/except` (a dead feed is skipped, the other feed in that section
  still delivers), and entries are read with `.get("title")` so one
  malformed entry can't sink its feed.
- **`summarize(headlines)`** — one model call. The prompt gives the raw
  titles per section and demands fixed sections with hard caps: INDIA (4
  bullets, dedupe overlap, drop clickbait), US (3, national politics/
  economy/policy only), GEOPOLITICS (3, prefer India/US relevance). A
  section whose feeds all failed renders as a single "unavailable" line.
- **`main()`** — gather → summarize → send, with a headline count in the
  header. If *every* feed is unreachable it skips the model and sends a
  one-liner instead.
- **`agentlib.py`** (vendored) — `ask_llm()` one-shot model call;
  `send_telegram()` chunked sends.

## Design notes

- Tech news is deliberately excluded — the tech-news agent covers it in
  depth at 7:00; cricket has its own agent too. One agent, one task.
- Two crons + dedupe guard: backup at 07:13 IST delivers only if the
  06:13 primary was dropped or failed.

## Ops

- Schedule: `.github/workflows/news-briefing.yml`
  (`43 0 * * *` UTC = 06:13 IST; backup 07:13)
- Run now: `gh workflow run news-briefing.yml -R astroboy1183/news-briefing`
- Secrets (Actions): `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Local test: `cd ~/agents/news_briefing && <any fleet venv>/bin/python news_briefing.py`
