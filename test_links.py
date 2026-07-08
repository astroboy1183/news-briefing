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
