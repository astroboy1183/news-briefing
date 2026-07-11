#!/usr/bin/env python3
"""News briefing.

Two Telegram editions a day via GitHub Actions, built from 36 verified
feeds across six sections:

  morning (6:00 IST sharp) — the full briefing
  evening (21:00 IST)      — a tight wrap of what broke SINCE the morning
                             (the seen-memory guarantees zero overlap);
                             silent if genuinely nothing new

  🗞 Top          — the single biggest story, one line (sent as a photo
                     front page when the article carries an og:image)
  📰 INDIA        — national news
  🏛 POLITICS     — Indian political developments with governance substance
  💼 BUSINESS     — economy/RBI/markets/corporate
  📍 HYDERABAD    — Telangana/city news, incl. Telugu media (omitted when
                     nothing notable)
  🇺🇸 US           — national + ALWAYS the India-US corridor (visas,
                     H-1B, immigration, trade) when present
  🌍 WORLD        — conflicts, diplomacy, major elections

Bullets are written from the ARTICLES, not the headlines (two-stage
select/fetch/write), each with its source link validated against the
gathered set. NEWS_WATCH (comma-separated topics, from a secret) is a
personal watchlist: matching stories are always selected and 👁-flagged.
Sunday mornings append 🗓 THE WEEK — the week's story arcs traced from
the briefed memory.

Two memories (state/, committed back by the workflow):
  seen.json    — candidate links shown to the model, 3 days: a story
                 lingering in feeds is briefed exactly once, and the
                 evening wrap only ever carries post-morning news
  briefed.json — what the bullets actually SAID, 7 days: developments
                 get framed as developments, and Sunday can trace arcs

Hard failures raise and land in the Actions log.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
from dotenv import load_dotenv

from agentlib import ask_llm, send_telegram

BASE_DIR = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")

# section → feeds. Every URL verified before inclusion (sweeps 10–11 Jul
# 2026). Tested and REJECTED as dead/empty/misfiled: Politico, ThePrint,
# Deccan Herald, Business Standard, Financial Express, The Wire, Hindu
# politics topic, HT politics, India Today politics, TOI "politics"
# (actually their business feed), Eenadu, Deccan Chronicle, Hans India,
# News Minute (1-entry feed), Greatandhra (film-gossip heavy), and the
# NTV/V6/Sakshi category feeds (redirect to the general ones kept below).
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
    "politics": [
        "https://indianexpress.com/section/political-pulse/feed/",
        "https://www.news18.com/rss/politics.xml",
        # episodic (quiet between polls) but high-quality when it counts
        "https://www.thehindu.com/elections/feeder/default.rss",
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
        # Telugu media — local news that never reaches the national English
        # feeds; the writer renders bullets in English
        "https://ntvtelugu.com/feed",
        "https://www.v6velugu.com/feed",
        "https://www.sakshi.com/rss.xml",
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
# 6 per feed × 36 sources ≈ 200 candidates keeps the prompt bounded while
# wider sourcing still improves coverage — the bullets stay capped, so the
# MESSAGE never grows, only its selection pool.
TITLES_PER_FEED = 6
SNIPPET_CHARS = 250  # feed summary excerpt per entry; some feeds have
LOOKBACK_HOURS = 24  # none — the model then judges by title alone
EVENING_LOOKBACK_HOURS = 16  # 6:00 → 21:00 plus margin; seen-memory
                             # already blocks anything the morning carried

# Two-stage bullets: a cheap model SELECTS from headlines, the code
# fetches the full articles for just the selected stories, a stronger
# model WRITES from real article text. Snippets can't carry numbers,
# names and consequences; articles can.
SECTION_CAPS = {  # morning, the full briefing
    "india": 5, "politics": 3, "business": 3,
    "hyderabad": 3, "us": 5, "world": 3,
}
EVENING_CAPS = {  # the wrap stays tight — Top + ~8 bullets
    "india": 2, "politics": 1, "business": 1,
    "hyderabad": 1, "us": 2, "world": 1,
}
WATCH_EXTRA = 2        # watchlist stories forced in per section, at most
ARTICLE_CHARS = 3000   # per fetched article, boilerplate-stripped
FETCH_TIMEOUT = 15     # one slow news site must not stall the run
FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (news-briefing digest)"}

TAG_RE = re.compile(r"<[^>]+>")

STATE_DIR = BASE_DIR / "state"
SEEN_FILE = STATE_DIR / "seen.json"
BRIEFED_FILE = STATE_DIR / "briefed.json"
SEEN_DAYS = 3     # feeds re-serve stories for a day or two; 3 covers weekends
BRIEFED_DAYS = 7  # a week of "what the bullets said" — Sunday traces arcs
STATE_MARKER = "===STATE==="


def edition(now):
    """morning (the full briefing) or evening (the wrap), by IST hour."""
    return "morning" if now.hour < 12 else "evening"


def watch_terms():
    """Personal watchlist from NEWS_WATCH (comma-separated, case-blind)."""
    raw = os.environ.get("NEWS_WATCH", "")
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def watch_hit(entry, terms):
    """Does this candidate mention a watchlist topic? Title + snippet."""
    hay = (entry.get("title", "") + " " + entry.get("snippet", "")).lower()
    return any(t in hay for t in terms)


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
    yesterday was not important enough to resurface unchanged today. The
    same memory makes the evening wrap new-only: the morning run saved
    every candidate it gathered."""
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
    cutoff = (datetime.now(timezone.utc) - timedelta(days=BRIEFED_DAYS)).strftime(
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
    """(message text, today's briefed keys, top-story link) from the reply.

    The model appends a JSON tail after STATE_MARKER; a malformed tail
    costs the continuity memory and the photo, never the briefing."""
    if STATE_MARKER not in reply:
        return reply.strip(), [], ""
    text, _, tail = reply.partition(STATE_MARKER)
    start, end = tail.find("{"), tail.rfind("}")
    keys, top_link = [], ""
    if start != -1 and end > start:
        try:
            state = json.loads(tail[start : end + 1])
            keys = state.get("briefed", [])
            top_link = state.get("top_link", "")
        except (ValueError, AttributeError):
            keys, top_link = [], ""
    if not isinstance(top_link, str):
        top_link = ""
    return text.strip(), [k for k in keys if isinstance(k, str)], top_link


def gather_headlines(seen=frozenset(), lookback=LOOKBACK_HOURS):
    """{section: [{title, snippet, link}, …]} — failed feeds skipped."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)
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


def select_stories(headlines, briefed, model, caps):
    """Stage 1: a cheap model picks which stories deserve the bullets.

    Returns {section: [entries]} capped per ``caps``. Watchlist (👁)
    candidates the model skipped are forced back in — bounded by
    WATCH_EXTRA so one broad term can't flood a section. An unparseable
    reply falls back to the first N candidates per section — a broken
    selector must cost quality, never the briefing."""
    blocks = []
    for section, entries in headlines.items():
        lines = "\n".join(
            f"{i}. {'👁 ' if e.get('watch') else ''}{e['title']}"
            f"{' | ' + e['snippet'] if e['snippet'] else ''}"
            for i, e in enumerate(entries)
        )
        blocks.append(f"=== {section} ===\n{lines or '(none)'}")
    recently = [key for lines in briefed.values() for key in lines]

    reply = ask_llm(
        "You are selecting stories for my news briefing. I am a data "
        "engineer in Hyderabad with strong US ties.\n\n"
        + "\n\n".join(blocks)
        + "\n\n=== RECENTLY BRIEFED (already covered) ===\n"
        + ("\n".join(f"- {k}" for k in recently) or "(none)")
        + "\n\nPick per section, by candidate index: "
        + ", ".join(f"{s} up to {n}" for s, n in caps.items())
        + ". Rules:\n"
        "- 👁 marks my personal watchlist — ALWAYS select 👁 candidates.\n"
        "- Several feeds carrying the same story = importance signal; keep "
        "exactly one copy, from the best-known source, in ONE section only "
        "(if it fits several, pick the best fit).\n"
        "- Vary outlets within a section — one outlet must not fill it.\n"
        "- politics = Indian political developments with governance "
        "substance (elections, parliament, party moves with consequences); "
        "skip pure slanging matches.\n"
        "- Skip clickbait/celebrity/cinema filler (some Telugu feeds carry "
        "a lot of it).\n"
        "- Skip stories already briefed UNLESS a candidate carries a "
        "genuine development.\n"
        "- In 'us', India-US corridor stories (visas, H-1B, immigration, "
        "trade) are always worth a slot when present.\n\n"
        "Output ONLY one JSON object mapping section name to an array of "
        'chosen indices, e.g. {"india": [0, 4], "us": [2]}. No prose.',
        max_tokens=400,
        model=model,
    )
    try:
        start, end = reply.find("{"), reply.rfind("}")
        picks = json.loads(reply[start : end + 1])
        selected = {}
        for section, entries in headlines.items():
            idx = [
                i
                for i in picks.get(section, [])
                if isinstance(i, int) and 0 <= i < len(entries)
            ]
            chosen = [entries[i] for i in idx[: caps[section]]]
            # deterministic watchlist guarantee, model-proof
            forced = [e for e in entries if e.get("watch") and e not in chosen]
            selected[section] = chosen + forced[:WATCH_EXTRA]
        return selected
    except (ValueError, AttributeError, TypeError):
        return {s: entries[: caps[s]] for s, entries in headlines.items()}


def fetch_article(link):
    """Readable text of a news page, tags stripped — '' on any failure.

    Snippets can't carry numbers, names and consequences; the article
    can. Paywalled/blocking sites just fall back to the snippet."""
    try:
        resp = requests.get(link, timeout=FETCH_TIMEOUT, headers=FETCH_HEADERS)
        resp.raise_for_status()
        html = re.sub(
            r"(?is)<(script|style|head|nav|footer|header|aside)[^>]*>.*?</\1>",
            " ",
            resp.text,
        )
        text = re.sub(r"<[^>]+>", " ", html)
        return " ".join(text.split())[:ARTICLE_CHARS]
    except Exception:
        return ""


def fetch_og_image(link):
    """The page's og:image URL — the story's own front-page photo.

    '' on any failure; the photo is an enrichment, never a dependency."""
    try:
        resp = requests.get(link, timeout=FETCH_TIMEOUT, headers=FETCH_HEADERS)
        resp.raise_for_status()
        meta = re.search(
            r"<meta[^>]+(?:property|name)=[\"']og:image[\"'][^>]*>",
            resp.text[:120_000],
            re.I,
        )
        if not meta:
            return ""
        content = re.search(r"content=[\"']([^\"']+)[\"']", meta.group(0), re.I)
        url = (content.group(1) if content else "").strip()
        return url if url.startswith("http") else ""
    except Exception:
        return ""


def send_photo(photo_url, caption):
    """Front page: the Top story as a Telegram photo. Best-effort — False
    on any failure, and the text briefing (which repeats the Top line)
    goes out regardless, so a broken image costs nothing."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat and photo_url):
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat, "photo": photo_url,
                  "caption": caption[:1000]},
            timeout=FETCH_TIMEOUT,
        )
        return bool(resp.json().get("ok"))
    except Exception:
        return False


def write_briefing(selected, briefed, model, caps, ed="morning"):
    """Stage 2: a stronger model writes the bullets from full article text."""
    blocks = []
    for section, entries in selected.items():
        lines = []
        for e in entries:
            body = e.get("article") or e.get("snippet") or "(title only)"
            flag = "👁 " if e.get("watch") else ""
            lines.append(f"- {flag}{e['title']}\n  TEXT: {body}\n  LINK: {e['link']}")
        blocks.append(
            f"=== {section.upper()} — write up to {caps[section]} "
            f"bullets ===\n" + ("\n".join(lines) or "(feeds unavailable)")
        )
    recently = [key for lines in briefed.values() for key in lines]

    intro = (
        "You are composing my morning news briefing"
        if ed == "morning"
        else "You are composing my EVENING WRAP — a tight update of what "
        "broke after this morning's 6:00 briefing (everything below is "
        "new since then)"
    )
    prompt = (
        f"{intro} from pre-selected stories, each with article text where "
        "it could be fetched. I am a data engineer in Hyderabad with "
        "strong US ties. Be terse and substantive. Plain text only — no "
        "markdown headers or bold.\n\n"
        + "\n\n".join(blocks)
        + "\n\n=== RECENTLY BRIEFED (last days — already covered) ===\n"
        + ("\n".join(f"- {k}" for k in recently) or "(none)")
        + "\n\nProduce EXACTLY this output structure:\n\n"
        "🗞 Top: <the single biggest story, one line — broadest "
        "consequence wins>\n\n"
        "📰 INDIA\n🏛 POLITICS\n💼 BUSINESS\n📍 HYDERABAD\n🇺🇸 US\n🌍 WORLD\n\n"
        "Rules:\n"
        "- Each bullet: 2-3 sentences of real substance drawn from TEXT — "
        "the concrete facts (numbers, names, dates) and why it matters — "
        "then the story's LINK on its own line. Where TEXT is missing, "
        "stay conservative: report the headline fact, never invent "
        "detail.\n"
        "- Start bullets for stories marked 👁 with 👁 — they hit my "
        "personal watchlist.\n"
        "- Some sources are Telugu; write every bullet in English.\n"
        "- OMIT any section whose input is empty — no placeholder lines.\n"
        "- If a story develops something in RECENTLY BRIEFED, lead with "
        "what is NEW, never re-explain from scratch.\n"
        "- Copy links verbatim — never invent one.\n\n"
        f"Then output the line {STATE_MARKER} and ONE JSON object: "
        '{"briefed": [a terse story key for each bullet you wrote, e.g. '
        '"SC verdict on electoral bonds", "H-1B fee hike proposal"], '
        '"top_link": the LINK of the story your Top line describes}. '
        "No text after the JSON."
    )
    return ask_llm(prompt, max_tokens=4000, model=model)


def week_in_review(briefed, model):
    """Sunday's 🗓 THE WEEK: the week's story arcs, traced from what the
    briefings actually said. Needs 3+ days of memory; '' otherwise."""
    days = {d: keys for d, keys in sorted(briefed.items()) if keys}
    if len(days) < 3:
        return ""
    history = "\n".join(f"{d}: " + "; ".join(keys) for d, keys in days.items())
    reply = ask_llm(
        "Below are the story keys my news briefings covered each day this "
        "week, oldest first:\n\n"
        + history
        + "\n\nWrite a '🗓 THE WEEK' section: up to 5 bullets, each "
        "tracing ONE story's arc across the week (e.g. '• H-1B fee rule: "
        "proposed Mon, industry pushback Wed, paused Fri'), most "
        "consequential first. Only connect what these keys support — "
        "NEVER invent developments beyond them. A story appearing once "
        "with no follow-up is not an arc. Plain text, no links, no "
        "commentary. If fewer than 2 real arcs exist, output exactly: "
        "NONE",
        max_tokens=600,
        model=model,
    ).strip()
    if reply == "NONE" or "🗓" not in reply:
        return ""
    return reply


def main():
    load_dotenv(BASE_DIR / ".env")
    now = datetime.now(IST)
    ed = edition(now)
    caps = SECTION_CAPS if ed == "morning" else EVENING_CAPS
    lookback = LOOKBACK_HOURS if ed == "morning" else EVENING_LOOKBACK_HOURS

    seen = load_seen()
    briefed = load_briefed()
    headlines = gather_headlines(seen, lookback)
    terms = watch_terms()
    for entries in headlines.values():
        for e in entries:
            e["watch"] = watch_hit(e, terms)
    scanned = sum(len(v) for v in headlines.values())
    feed_count = sum(len(u) for u in FEEDS.values())

    # A quiet evening is normal — the wrap earns its place by only
    # existing when news broke after the morning briefing.
    print(f"{ed} edition: {scanned} fresh candidates from {feed_count} feeds")
    if scanned == 0 and ed == "evening":
        print("evening wrap: nothing new since this morning — staying silent")
        return

    label = "" if ed == "morning" else " · evening wrap"
    header = (
        f"📰 News — {datetime.now(IST):%a %d %b}{label}\n"
        f"{scanned} fresh headlines · {feed_count} feeds"
    )
    # env read after load_dotenv so .env values work too
    select_model = os.environ.get("NEWS_MODEL_SELECT") or "claude-haiku-4-5"
    write_model = os.environ.get("NEWS_MODEL_WRITE") or "claude-sonnet-5"

    briefed_today, top_link = [], ""
    if scanned == 0:
        body = "Quiet day: nothing new since yesterday's briefing ☕"
    else:
        selected = select_stories(headlines, briefed, select_model, caps)
        for entries in selected.values():
            for e in entries:  # fetch real article text for the chosen few
                e["article"] = fetch_article(e["link"])
        body, briefed_today, top_link = split_state(
            write_briefing(selected, briefed, write_model, caps, ed)
        )
        allowed = gathered_links(headlines)
        body = validate_links(body, allowed)
        if top_link not in allowed:
            top_link = ""  # same guarantee as the bullets: no invented URLs

    # Sunday morning: the week's arcs, from the 7-day briefed memory.
    if ed == "morning" and now.weekday() == 6:
        try:
            week = week_in_review(briefed, write_model)
        except Exception:
            week = ""  # an enrichment must never sink the briefing
        if week:
            body += "\n\n" + week

    # Front page: the Top story's own photo, captioned with the topline.
    # Best-effort — the text briefing below carries the Top line anyway.
    if top_link:
        image = fetch_og_image(top_link)
        top_line = next(
            (l for l in body.splitlines() if l.startswith("🗞")), ""
        )
        if image and top_line:
            ok = send_photo(image, top_line)
            print(f"front-page photo: {'sent' if ok else 'failed (non-fatal)'}")

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
