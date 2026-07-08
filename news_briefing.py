#!/usr/bin/env python3
"""News briefing.

One Telegram message every morning (~6:13 IST via GitHub Actions): India,
US and world/geopolitics headlines from the last day's feeds, deduped and
filtered down to what a professional should know.

One agent, one task, one bot. Tech news deliberately excluded — the
tech-news agent covers it in depth an hour later; cricket has its own
agent too.

Cross-day memory (state/seen.json, committed back by the workflow): a
story that lingers in the feeds for days is only ever briefed once —
candidate links are remembered for 3 days and filtered out on re-entry.

Hard failures raise and land in the Actions log.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
from dotenv import load_dotenv

from agentlib import ask_llm, send_telegram

BASE_DIR = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")

STATE_FILE = BASE_DIR / "state" / "seen.json"
SEEN_DAYS = 3  # feeds re-serve stories for a day or two; 3 covers weekends


def load_seen():
    """{link: 'YYYY-MM-DD'} of recently briefed candidates, pruned to window.

    Anything fed to the model counts as seen — a story it chose to skip
    yesterday was not important enough to resurface unchanged today."""
    try:
        seen = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_DAYS)).strftime(
        "%Y-%m-%d"
    )
    return {k: v for k, v in seen.items() if isinstance(v, str) and v >= cutoff}


def save_seen(seen):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen, indent=0, sort_keys=True) + "\n")

FEEDS = {
    "india": [
        "https://www.thehindu.com/news/national/feeder/default.rss",
        "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
        "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml",
    ],
    "us": [
        "https://feeds.npr.org/1001/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
    ],
    "world": [
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
}
TITLES_PER_FEED = 8
LOOKBACK_HOURS = 24


def fresh(entry, cutoff):
    """Keep entries newer than cutoff; undated entries are kept."""
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    if not stamp:
        return True
    return datetime(*stamp[:6], tzinfo=timezone.utc) >= cutoff


def gather_headlines(seen=frozenset()):
    """{'india': ['title | link', ...], ...} — failed feeds skipped."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    out = {}
    for section, urls in FEEDS.items():
        titles = []
        for url in urls:
            try:
                feed = feedparser.parse(url)
                # .get(): a single malformed entry must not sink its feed;
                # fresh(): stale/evergreen items never leak into the prompt;
                # seen: a story briefed in the last 3 days never repeats.
                titles += [
                    f"{e.get('title')} | {e.get('link', '')}"
                    for e in feed.entries[:TITLES_PER_FEED]
                    if e.get("title")
                    and fresh(e, cutoff)
                    and e.get("link", "") not in seen
                ]
            except Exception:
                continue  # dead feed → just use the others
        out[section] = titles
    return out


def gathered_links(headlines):
    """The set of source URLs we actually handed the model — for validation."""
    links = set()
    for titles in headlines.values():
        for t in titles:
            link = t.rsplit(" | ", 1)[-1].strip()
            if link.startswith("http"):
                links.add(link)
    return links


def validate_links(text, allowed):
    """Strip any emitted URL not in the gathered set (guards hallucinated links).

    Pure string matching: scan each whitespace token, and if it looks like a
    URL whose (punctuation-trimmed) form was never in a source feed, replace
    it with a marker so a made-up link can never reach me.
    """
    leading = "(<[{\"'"
    trailing = ".,);]}>\"'"
    lines = []
    for line in text.splitlines():
        parts = []
        for tok in line.split(" "):
            start = 0
            while start < len(tok) and tok[start] in leading:
                start += 1
            end = len(tok)
            while end > start and tok[end - 1] in trailing:
                end -= 1
            bare = tok[start:end]
            if bare.startswith(("http://", "https://")) and bare not in allowed:
                parts.append(
                    tok[:start] + "[link removed: not in source feeds]" + tok[end:]
                )
            else:
                parts.append(tok)
        lines.append(" ".join(parts))
    return "\n".join(lines)


def summarize(headlines):
    """One model call: raw titles in, three compact sections out."""
    india = "\n".join(f"- {t}" for t in headlines.get("india", []))
    us = "\n".join(f"- {t}" for t in headlines.get("us", []))
    world = "\n".join(f"- {t}" for t in headlines.get("world", []))

    prompt = (
        "You are composing my morning news briefing. Be terse. "
        "Plain text only — no markdown headers or bold.\n\n"
        "=== INPUT 1: India news headlines (title | link, multiple feeds) ===\n"
        f"{india or '(feeds unavailable)'}\n\n"
        "=== INPUT 2: US news headlines (title | link, multiple feeds) ===\n"
        f"{us or '(feeds unavailable)'}\n\n"
        "=== INPUT 3: World/geopolitics headlines (title | link, multiple feeds) ===\n"
        f"{world or '(feeds unavailable)'}\n\n"
        "Produce EXACTLY this output structure:\n\n"
        "📰 INDIA — 4 bullets max. Dedupe overlapping stories, drop "
        "clickbait/celebrity filler, keep what a professional should know.\n\n"
        "🇺🇸 US — 3 bullets max. National-level news only: politics, economy, "
        "policy. Drop local crime and celebrity stories.\n\n"
        "🌍 GEOPOLITICS — 3 bullets max. Conflicts, diplomacy, trade, major "
        "elections. Prefer stories with India or US relevance when choosing "
        "what to keep.\n\n"
        "After each bullet, put the story's link on its own line (when "
        "feeds overlap, pick the better-known source's link). Copy links "
        "verbatim — never invent one.\n\n"
        "If an input says unavailable, output that section as a single line "
        "saying so."
    )
    return ask_llm(prompt, max_tokens=4000)


def main():
    load_dotenv(BASE_DIR / ".env")
    seen = load_seen()
    headlines = gather_headlines(seen)
    scanned = sum(len(v) for v in headlines.values())

    header = (
        f"📰 News briefing — {datetime.now(IST):%a %d %b %Y}\n"
        f"({scanned} fresh headlines scanned)\n\n"
    )
    if scanned == 0:
        body = "Quiet day: nothing new since yesterday's briefing ☕"
    else:
        body = validate_links(summarize(headlines), gathered_links(headlines))
    send_telegram(header + body)

    # Remember what the model was shown — after the send, so a state
    # failure never costs the briefing itself.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for link in gathered_links(headlines):
        seen[link] = today
    try:
        save_seen(seen)
    except OSError:
        pass


if __name__ == "__main__":
    main()
