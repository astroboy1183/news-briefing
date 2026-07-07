# news-briefing

Morning news briefing → Telegram, ~6:13 AM IST via GitHub Actions.

India + US + world/geopolitics headlines from six RSS feeds, deduped and
filtered to what a professional should know. Tech news deliberately
excluded — the tech-news agent covers it in depth at 7:00; cricket has
its own agent too.

One agent, one task, one bot.
Part of the personal-agents fleet (`[feeds] → [summarize] → [Telegram]`).

- Schedule: `.github/workflows/news-briefing.yml` (`43 0 * * *` UTC = 06:13 IST; backup 07:13 with dedupe guard)
- Run now: `gh workflow run news-briefing.yml -R astroboy1183/news-briefing`
- Secrets (Actions): `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Feeds: `FEEDS` dict in `news_briefing.py`
