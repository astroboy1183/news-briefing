#!/usr/bin/env python3
"""Offline unit tests for validate_links — no network, no model, no deps.

Run: python3 test_links.py
Guards the promise that a link the model invents (one never present in the
gathered feed set) can never reach Telegram.
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


# --- continuity-memory helpers (added with the five-section upgrade) ---------

import json
import tempfile
from pathlib import Path

import news_briefing as nb

reply = 'the briefing\n===STATE===\n{"briefed": ["story one", "story two"]}'
text, keys = nb.split_state(reply)
check("split_state extracts text and keys", text == "the briefing" and keys == ["story one", "story two"])

text, keys = nb.split_state("no tail here")
check("missing tail costs memory not message", text == "no tail here" and keys == [])

text, keys = nb.split_state("msg\n===STATE===\nnot json")
check("garbage tail costs memory not message", text == "msg" and keys == [])

with tempfile.TemporaryDirectory() as tmp:
    saved_briefed, saved_dir = nb.BRIEFED_FILE, nb.STATE_DIR
    nb.STATE_DIR = Path(tmp)
    nb.BRIEFED_FILE = Path(tmp) / "briefed.json"
    try:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        nb.save_briefed({today: ["fresh"], "2020-01-01": ["ancient"]})
        loaded = nb.load_briefed()
    finally:
        nb.BRIEFED_FILE, nb.STATE_DIR = saved_briefed, saved_dir
check("briefed memory prunes old days", "2020-01-01" not in loaded and loaded[today] == ["fresh"])

check("clean strips tags", nb.clean("<p>a</p>  <b>b</b>") == "a b")

print("All continuity tests passed.")


# --- two-stage selection helpers ---------------------------------------------

def fake_headlines():
    return {"india": [
        {"title": f"Story {i}", "snippet": "", "link": f"https://x/{i}"}
        for i in range(4)
    ]}

saved_ask = nb.ask_llm
nb.ask_llm = lambda prompt, max_tokens=0, model="": '{"india": [2, 0, 99]}'
try:
    picked = nb.select_stories(fake_headlines(), {}, "m")
finally:
    nb.ask_llm = saved_ask
check("selector picks valid indices in order",
      [e["title"] for e in picked["india"]] == ["Story 2", "Story 0"])

nb.ask_llm = lambda prompt, max_tokens=0, model="": "sorry no json"
try:
    picked = nb.select_stories(fake_headlines(), {}, "m")
finally:
    nb.ask_llm = saved_ask
check("unparseable selector falls back to first N",
      len(picked["india"]) == 4 and picked["india"][0]["title"] == "Story 0")

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
