#!/usr/bin/env python3
"""News briefing.

One Telegram message every morning (~6:13 IST via GitHub Actions), built
from 14 feeds across five sections:

  🗞 Top          — the single biggest story of the day, one line
  📰 INDIA        — national news (Hindu, TOI, HT, Indian Express)
  💼 BUSINESS     — economy/RBI/markets/corporate (Mint, Economic Times)
  📍 HYDERABAD    — Telangana/city news (omitted when nothing notable)
  🇺🇸 US           — national + ALWAYS the India-US corridor (visas,
                     H-1B, immigration, trade) when present
  🌍 WORLD        — conflicts, diplomacy, major elections

Bullets are 1-2 sentences of substance (what happened + why it matters),
not rewritten headlines — feed summaries ride along in the prompt where
the feed provides them. Every bullet carries its source link, validated
against the gathered set so an invented URL can never reach me.

Two memories (state/, committed back by the workflow):
  seen.json    — candidate links shown to the model, 3 days: a story
                 lingering in feeds is briefed exactly once
  briefed.json — what the bullets actually SAID, 3 days: developments
                 get framed as developments ("the verdict in X: …"),
                 never re-explained from scratch

Hard failures raise and land in the Actions log.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
from dotenv import load_dotenv

from agentlib import ask_llm, send_telegram

BASE_DIR = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")

# section → feeds. Every URL verified before inclusion (last sweep 10 Jul
# 2026: Politico, ThePrint, Deccan Herald, Business Standard and
# Financial Express tested and REJECTED — dead or empty feeds).
FEEDS = {
    "india": [
        "https://www.thehindu.com/news/national/feeder/default.rss",
        "https://timesofindia.indiatimes.com/rssfeedstopstories.cms",
        "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml",
        "https://indianexpress.com/section/india/feed/",
        "https://feeds.feedburner.com/ndtvnews-top-stories",
        "https://www.indiatoday.in/rss/1206578",
        "https://www.news18.com/rss/india.xml",
        "https://feeds.feedburner.com/ScrollinArticles.rss",
    ],
    "business": [
        "https://www.livemint.com/rss/news",
        "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
        "https://www.moneycontrol.com/rss/latestnews.xml",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    ],
    "hyderabad": [
        "https://www.thehindu.com/news/national/telangana/feeder/default.rss",
        "https://timesofindia.indiatimes.com/rssfeeds/-2128816011.cms",
        "https://telanganatoday.com/feed",
        "https://www.siasat.com/feed/",
    ],
    "us": [
        "https://feeds.npr.org/1001/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
        "https://www.theguardian.com/us-news/rss",
        "http://rss.cnn.com/rss/cnn_us.rss",
        "https://feeds.washingtonpost.com/rss/national",
        "https://abcnews.go.com/abcnews/usheadlines",
        "https://thehill.com/feed/",
        "https://api.axios.com/feed/",
    ],
    "world": [
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
        "https://www.theguardian.com/world/rss",
        "http://rss.cnn.com/rss/edition_world.rss",
        "https://www.france24.com/en/rss",
        "https://rss.dw.com/rdf/rss-en-world",
    ],
}
# 6 (not 8) per feed now that there are 30 sources: ~180 candidates keeps
# the prompt bounded while wider sourcing still improves coverage — the
# bullets stay capped, so the MESSAGE never grows, only its selection pool.
TITLES_PER_FEED = 6
SNIPPET_CHARS = 250  # feed summary excerpt per entry; some feeds have
LOOKBACK_HOURS = 24  # none — the model then judges by title alone

TAG_RE = re.compile(r"<[^>]+>")

STATE_DIR = BASE_DIR / "state"
SEEN_FILE = STATE_DIR / "seen.json"
BRIEFED_FILE = STATE_DIR / "briefed.json"
SEEN_DAYS = 3  # feeds re-serve stories for a day or two; 3 covers weekends
STATE_MARKER = "===STATE==="


def clean(html):
    """Strip tags and collapse whitespace — feed summaries arrive as HTML."""
    return " ".join(TAG_RE.sub(" ", html or "").split())


def fresh(entry, cutoff):
    """Keep entries newer than cutoff; undated entries are kept."""
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    if not stamp:
        return True
    return datetime(*stamp[:6], tzinfo=timezone.utc) >= cutoff


def load_seen():
    """{link: 'YYYY-MM-DD'} of recently briefed candidates, pruned to window.

    Anything fed to the model counts as seen — a story it chose to skip
    yesterday was not important enough to resurface unchanged today."""
    try:
        seen = json.loads(SEEN_FILE.read_text())
    except (OSError, ValueError):
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_DAYS)).strftime(
        "%Y-%m-%d"
    )
    return {k: v for k, v in seen.items() if isinstance(v, str) and v >= cutoff}


def save_seen(seen):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=0, sort_keys=True) + "\n")


def load_briefed():
    """{date: [story keys]} — what recent bullets actually said, pruned."""
    try:
        briefed = json.loads(BRIEFED_FILE.read_text())
    except (OSError, ValueError):
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_DAYS)).strftime(
        "%Y-%m-%d"
    )
    return {
        d: [s for s in lines if isinstance(s, str)]
        for d, lines in briefed.items()
        if isinstance(d, str) and d >= cutoff and isinstance(lines, list)
    }


def save_briefed(briefed):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BRIEFED_FILE.write_text(json.dumps(briefed, indent=1, sort_keys=True) + "\n")


def split_state(reply):
    """(message text, today's briefed-story keys) from the model reply.

    The model appends a JSON tail after STATE_MARKER; a malformed tail
    costs the continuity memory, never the briefing."""
    if STATE_MARKER not in reply:
        return reply.strip(), []
    text, _, tail = reply.partition(STATE_MARKER)
    start, end = tail.find("{"), tail.rfind("}")
    keys = []
    if start != -1 and end > start:
        try:
            keys = json.loads(tail[start : end + 1]).get("briefed", [])
        except (ValueError, AttributeError):
            keys = []
    return text.strip(), [k for k in keys if isinstance(k, str)]


def gather_headlines(seen=frozenset()):
    """{section: [{title, snippet, link}, …]} — failed feeds skipped."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    out = {}
    for section, urls in FEEDS.items():
        entries = []
        for url in urls:
            try:
                feed = feedparser.parse(url)
                for e in feed.entries[:TITLES_PER_FEED]:
                    # .get(): a malformed entry must not sink its feed;
                    # fresh(): stale items never enter; seen: a story
                    # briefed in the last 3 days never repeats.
                    if not e.get("title") or not fresh(e, cutoff):
                        continue
                    if e.get("link", "") in seen:
                        continue
                    entries.append(
                        {
                            "title": e["title"],
                            "snippet": clean(e.get("summary", ""))[:SNIPPET_CHARS],
                            "link": e.get("link", ""),
                        }
                    )
            except Exception:
                continue  # dead feed → just use the others
        out[section] = entries
    return out


def gathered_links(headlines):
    """The set of source URLs we actually handed the model — for validation."""
    return {
        e["link"]
        for entries in headlines.values()
        for e in entries
        if e["link"].startswith("http")
    }


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


def summarize(headlines, briefed):
    """One model call: sectioned candidates in, substantive briefing out."""
    blocks = []
    for section, entries in headlines.items():
        lines = "\n".join(
            f"- {e['title']}{' | ' + e['snippet'] if e['snippet'] else ''} | {e['link']}"
            for e in entries
        )
        blocks.append(
            f"=== {section.upper()} candidates ===\n{lines or '(feeds unavailable)'}"
        )
    recently = [key for lines in briefed.values() for key in lines]

    prompt = (
        "You are composing my morning news briefing. I am a data engineer "
        "in Hyderabad with strong US ties. Be terse and substantive. Plain "
        "text only — no markdown headers or bold.\n\n"
        + "\n\n".join(blocks)
        + "\n\n=== RECENTLY BRIEFED (last 3 days — already covered) ===\n"
        + ("\n".join(f"- {k}" for k in recently) or "(none)")
        + "\n\nProduce EXACTLY this output structure:\n\n"
        "🗞 Top: <the single biggest story today, one line>\n\n"
        "📰 INDIA — 5 bullets max. National news a professional should "
        "know; dedupe overlap, drop clickbait/celebrity filler.\n\n"
        "💼 BUSINESS — 3 bullets max. Economy, RBI, markets policy, major "
        "corporate/tech-industry moves — the ones a data engineer would "
        "care about.\n\n"
        "📍 HYDERABAD — 2 bullets max, Telangana/city news that affects "
        "living there. OMIT this section entirely if nothing notable.\n\n"
        "🇺🇸 US — 5 bullets max. National politics, economy, policy — and "
        "ALWAYS include India-US corridor stories when present (visas, "
        "H-1B, immigration, trade, Indians in the US); they are never "
        "filler. Drop local crime and celebrity stories.\n\n"
        "🌍 WORLD — 3 bullets max. Conflicts, diplomacy, trade, major "
        "elections; prefer India or US relevance.\n\n"
        "Rules:\n"
        "- Each bullet: 1-2 sentences of substance — what happened AND why "
        "it matters — then the story's link on its own line. Where only a "
        "title was provided, stay conservative: report the headline fact, "
        "never invent detail.\n"
        "- RECENTLY BRIEFED lists stories already covered. If a candidate "
        "is a development of one, lead with what is NEW ('The verdict in "
        "…: '), never re-explain from scratch. If it merely repeats with "
        "nothing new, skip it.\n"
        "- When feeds overlap, pick the better-known source's link. Copy "
        "links verbatim — never invent one.\n"
        "- A section whose input says unavailable: one line saying so.\n\n"
        f"Then output the line {STATE_MARKER} and ONE JSON object: "
        '{"briefed": [a terse story key for each bullet you wrote, e.g. '
        '"SC verdict on electoral bonds", "H-1B fee hike proposal"]}. '
        "No text after the JSON."
    )
    return ask_llm(prompt, max_tokens=4000)


def main():
    load_dotenv(BASE_DIR / ".env")
    seen = load_seen()
    briefed = load_briefed()
    headlines = gather_headlines(seen)
    scanned = sum(len(v) for v in headlines.values())
    feed_count = sum(len(u) for u in FEEDS.values())

    header = (
        f"📰 News — {datetime.now(IST):%a %d %b}\n"
        f"{scanned} fresh headlines · {feed_count} feeds"
    )
    briefed_today = []
    if scanned == 0:
        body = "Quiet day: nothing new since yesterday's briefing ☕"
    else:
        body, briefed_today = split_state(summarize(headlines, briefed))
        body = validate_links(body, gathered_links(headlines))
    send_telegram(header + "\n\n" + body)

    # Remember what the model was shown AND what it said — after the send,
    # so a state failure never costs the briefing itself.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for link in gathered_links(headlines):
        seen[link] = today
    if briefed_today:
        briefed.setdefault(today, [])
        briefed[today] += briefed_today
    try:
        save_seen(seen)
        save_briefed(briefed)
    except OSError:
        pass


if __name__ == "__main__":
    main()
