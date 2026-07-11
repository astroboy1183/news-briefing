#!/usr/bin/env python3
"""Offline unit tests for news_briefing — no network, no model, no deps.

Run: python3 test_links.py
Guards the promises that matter: a link the model invents can never reach
Telegram, the continuity memory survives malformed model tails, the
two-stage selector fails soft, the watchlist guarantee is deterministic,
and the photo/week enrichments can never sink the briefing.
"""

from news_briefing import validate_links

ALLOWED = {
    "https://www.thehindu.com/news/national/story-a",
    "https://timesofindia.indiatimes.com/story-b",
}


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    assert cond, name


# A verbatim source link survives untouched.
out = validate_links("Big story\nhttps://www.thehindu.com/news/national/story-a", ALLOWED)
check("verbatim source link kept", "https://www.thehindu.com/news/national/story-a" in out)
check("no marker for valid link", "link removed" not in out)

# A hallucinated link is stripped.
out = validate_links("Made up\nhttps://www.thehindu.com/news/national/FAKE", ALLOWED)
check("hallucinated link removed", "FAKE" not in out)
check("marker inserted", "[link removed: not in source feeds]" in out)

# Trailing punctuation on a valid link is preserved, link still recognised.
out = validate_links("See (https://timesofindia.indiatimes.com/story-b).", ALLOWED)
check("valid link with punctuation kept", "https://timesofindia.indiatimes.com/story-b" in out)
check("punctuation preserved", out.strip().endswith(")."))
check("no marker with punctuation", "link removed" not in out)

# Trailing punctuation on a hallucinated link is stripped, punctuation kept.
out = validate_links("See (https://evil.example.com/x).", ALLOWED)
check("hallucinated link with punctuation removed", "evil.example.com" not in out)
check("marker keeps trailing punctuation", out.strip().endswith(").")
      and "[link removed: not in source feeds]" in out)

# Plain prose with no URLs is returned unchanged.
text = "Just a headline with no link at all"
check("prose untouched", validate_links(text, ALLOWED) == text)

print("\nAll link-validation tests passed.")


# --- continuity-memory helpers ------------------------------------------------

import json
import tempfile
from pathlib import Path

import news_briefing as nb

reply = ('the briefing\n===STATE===\n'
         '{"briefed": ["story one", "story two"], "top_link": "https://x/top"}')
text, keys, top = nb.split_state(reply)
check("split_state extracts text, keys and top link",
      text == "the briefing" and keys == ["story one", "story two"]
      and top == "https://x/top")

text, keys, top = nb.split_state("no tail here")
check("missing tail costs memory not message",
      text == "no tail here" and keys == [] and top == "")

text, keys, top = nb.split_state("msg\n===STATE===\nnot json")
check("garbage tail costs memory not message",
      text == "msg" and keys == [] and top == "")

text, keys, top = nb.split_state('m\n===STATE===\n{"briefed": [], "top_link": 7}')
check("non-string top link discarded", top == "")

with tempfile.TemporaryDirectory() as tmp:
    saved_briefed, saved_dir = nb.BRIEFED_FILE, nb.STATE_DIR
    nb.STATE_DIR = Path(tmp)
    nb.BRIEFED_FILE = Path(tmp) / "briefed.json"
    try:
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        five_days = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        nb.save_briefed({today: ["fresh"], five_days: ["mid-week"],
                         "2020-01-01": ["ancient"]})
        loaded = nb.load_briefed()
    finally:
        nb.BRIEFED_FILE, nb.STATE_DIR = saved_briefed, saved_dir
check("briefed memory keeps a week, prunes older",
      "2020-01-01" not in loaded and loaded[today] == ["fresh"]
      and loaded[five_days] == ["mid-week"])

check("clean strips tags", nb.clean("<p>a</p>  <b>b</b>") == "a b")

print("All continuity tests passed.")


# --- editions -----------------------------------------------------------------

from datetime import datetime as real_dt

check("6am is the morning edition",
      nb.edition(real_dt(2026, 7, 11, 6, 0, tzinfo=nb.IST)) == "morning")
check("9pm is the evening wrap",
      nb.edition(real_dt(2026, 7, 11, 21, 0, tzinfo=nb.IST)) == "evening")
check("evening caps stay tight",
      sum(nb.EVENING_CAPS.values()) < sum(nb.SECTION_CAPS.values())
      and set(nb.EVENING_CAPS) == set(nb.SECTION_CAPS) == set(nb.FEEDS))

print("All edition tests passed.")


# --- watchlist ----------------------------------------------------------------

import os

os.environ["NEWS_WATCH"] = "H-1B, , RBI "
check("watch terms parsed and normalised", nb.watch_terms() == ["h-1b", "rbi"])
os.environ["NEWS_WATCH"] = ""
check("empty watchlist is empty", nb.watch_terms() == [])

e = {"title": "New H-1B fee rule announced", "snippet": ""}
check("watch hit on title", nb.watch_hit(e, ["h-1b"]))
e = {"title": "Quiet day", "snippet": "RBI holds repo rate"}
check("watch hit on snippet", nb.watch_hit(e, ["rbi"]))
check("no terms no hit", not nb.watch_hit(e, []))

print("All watchlist tests passed.")


# --- two-stage selection helpers ----------------------------------------------

CAPS = {"india": 2}


def fake_headlines(watch_idx=()):
    return {"india": [
        {"title": f"Story {i}", "snippet": "", "link": f"https://x/{i}",
         "watch": i in watch_idx}
        for i in range(4)
    ]}

saved_ask = nb.ask_llm
nb.ask_llm = lambda prompt, max_tokens=0, model="": '{"india": [2, 0, 99]}'
try:
    picked = nb.select_stories(fake_headlines(), {}, "m", CAPS)
finally:
    nb.ask_llm = saved_ask
check("selector picks valid indices in order, capped",
      [e["title"] for e in picked["india"]] == ["Story 2", "Story 0"])

# The model skipped the 👁 story at index 3 — it is forced back in.
nb.ask_llm = lambda prompt, max_tokens=0, model="": '{"india": [0, 1]}'
try:
    picked = nb.select_stories(fake_headlines(watch_idx={3}), {}, "m", CAPS)
finally:
    nb.ask_llm = saved_ask
check("skipped watchlist story is forced in",
      [e["title"] for e in picked["india"]] == ["Story 0", "Story 1", "Story 3"])

nb.ask_llm = lambda prompt, max_tokens=0, model="": "sorry no json"
try:
    picked = nb.select_stories(fake_headlines(), {}, "m", CAPS)
finally:
    nb.ask_llm = saved_ask
check("unparseable selector falls back to first N",
      len(picked["india"]) == 2 and picked["india"][0]["title"] == "Story 0")

from types import SimpleNamespace
saved_req = nb.requests
nb.requests = SimpleNamespace(get=lambda *a, **k: SimpleNamespace(
    text="<html><script>junk</script><p>the article body</p></html>",
    raise_for_status=lambda: None))
try:
    art = nb.fetch_article("https://x/1")
finally:
    nb.requests = saved_req
check("article fetch strips boilerplate", "the article body" in art and "junk" not in art)

def boom(*a, **k):
    raise OSError("blocked")
nb.requests = SimpleNamespace(get=boom)
try:
    art = nb.fetch_article("https://x/1")
finally:
    nb.requests = saved_req
check("article fetch failure returns empty", art == "")

print("All two-stage tests passed.")


# --- photo front page ----------------------------------------------------------

def page(html):
    return SimpleNamespace(text=html, raise_for_status=lambda: None)

nb.requests = SimpleNamespace(get=lambda *a, **k: page(
    '<meta property="og:image" content="https://img.x/pic.jpg"/>'))
try:
    img = nb.fetch_og_image("https://x/1")
finally:
    nb.requests = saved_req
check("og:image extracted", img == "https://img.x/pic.jpg")

nb.requests = SimpleNamespace(get=lambda *a, **k: page(
    '<meta content="https://img.x/2.jpg" property="og:image">'))
try:
    img = nb.fetch_og_image("https://x/1")
finally:
    nb.requests = saved_req
check("og:image found with reversed attribute order", img == "https://img.x/2.jpg")

nb.requests = SimpleNamespace(get=lambda *a, **k: page("<html>no meta</html>"))
try:
    img = nb.fetch_og_image("https://x/1")
finally:
    nb.requests = saved_req
check("missing og:image returns empty", img == "")

nb.requests = SimpleNamespace(get=boom)
try:
    img = nb.fetch_og_image("https://x/1")
finally:
    nb.requests = saved_req
check("og fetch failure returns empty", img == "")

os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)
check("send_photo without credentials is a quiet no-op",
      nb.send_photo("https://img.x/pic.jpg", "cap") is False)

print("All photo tests passed.")


# --- week in review -------------------------------------------------------------

check("week in review needs 3 days of memory",
      nb.week_in_review({"2026-07-10": ["a"], "2026-07-11": ["b"]}, "m") == "")

WEEK = {f"2026-07-{d:02d}": [f"story {d}"] for d in range(6, 11)}
nb.ask_llm = lambda prompt, max_tokens=0, model="": "🗓 THE WEEK\n• story 6: grew all week"
try:
    week = nb.week_in_review(WEEK, "m")
finally:
    nb.ask_llm = saved_ask
check("week block returned when arcs exist", week.startswith("🗓 THE WEEK"))

nb.ask_llm = lambda prompt, max_tokens=0, model="": "NONE"
try:
    week = nb.week_in_review(WEEK, "m")
finally:
    nb.ask_llm = saved_ask
check("NONE means no week block", week == "")

nb.ask_llm = lambda prompt, max_tokens=0, model="": "sure! here are arcs without the header"
try:
    week = nb.week_in_review(WEEK, "m")
finally:
    nb.ask_llm = saved_ask
check("malformed week reply dropped", week == "")

print("All week-in-review tests passed.")
