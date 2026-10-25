"""Signal ①, Reddit: names that appear in a day's top posts of some subreddits for the first time.

reddit_new   names in the titles of the top posts of the day in the configured subreddits that
             no post of the last `vocab_days` used. Only the Atom RSS feeds work without a key,
             and unauthenticated Reddit allows about one request a minute per IP, so requests
             are paced PACE seconds apart. The first run seeds the vocabulary from the month's
             top posts.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

from . import UA, snap
from .hn import title_names
from .i18n import t
from .terms import _drop_nested

FEED = "https://www.reddit.com/r/{subs}/top/.rss"
PACE = 62
MAX_WAIT = 90
TOP = 25
_ATOM = "{http://www.w3.org/2005/Atom}"
_sleep = time.sleep
_last_request = 0.0


def _fetch(url: str) -> bytes:
    """One Reddit request, PACE seconds after the previous one. On a 429, waits out the window and tries once more."""
    global _last_request
    for attempt in (0, 1):
        if _last_request:
            wait = _last_request + PACE - time.monotonic()
            if wait > 0:
                _sleep(wait)
        _last_request = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt:
                raise
            reset = float(e.headers.get("x-ratelimit-reset") or PACE)
            _sleep(min(reset + 2, MAX_WAIT))
            _last_request = 0.0
    raise AssertionError("unreachable")


def _entries(xml: bytes) -> list[dict]:
    out = []
    for e in ET.fromstring(xml).iter(_ATOM + "entry"):
        link, cat = e.find(_ATOM + "link"), e.find(_ATOM + "category")
        out.append({"id": e.findtext(_ATOM + "id") or "",
                    "title": " ".join((e.findtext(_ATOM + "title") or "").split()),
                    "url": link.get("href", "") if link is not None else "",
                    "published": (e.findtext(_ATOM + "published") or "")[:10],
                    "sub": cat.get("term", "") if cat is not None else ""})
    return out


def _groups(c: dict) -> list[str]:
    subs, n = c["subreddits"], c["per_request"]
    return ["+".join(subs[i:i + n]) for i in range(0, len(subs), n)]


def _feed(group: str, period: str, limit: int, after: str = "") -> list[dict]:
    query = {"t": period, "limit": limit, **({"after": after} if after else {})}
    return _entries(_fetch(f"{FEED.format(subs=group)}?{urllib.parse.urlencode(query)}"))


def _seed(c: dict) -> tuple[dict[str, str], int]:
    vocab: dict[str, str] = {}
    count = 0
    for group in _groups(c):
        after = ""
        for _ in range(c["seed_pages"]):
            page = _feed(group, "month", 100, after)
            if not page:
                break
            for p in page:
                count += 1
                for term in title_names(p["title"]):
                    vocab[term] = max(p["published"], vocab.get(term, ""))
            after = page[-1]["id"]
    return vocab, count


def reddit_new(cfg: dict, state: Path) -> dict:
    c = cfg["reddit"]
    path = state / "reddit_vocab.json.gz"
    vocab: dict[str, str] | None = snap.load(path)
    seeded = 0
    if vocab is None:
        vocab, seeded = _seed(c)
    posts = [(rank, p) for group in _groups(c) for rank, p in enumerate(_feed(group, "day", c["limit"]), 1)]

    found: dict[str, list[tuple[int, dict]]] = {}
    for rank, p in posts:
        for term in title_names(p["title"]):
            if term not in vocab:
                found.setdefault(term, []).append((rank, p))
    rows = []
    for term, group in found.items():
        rank, best = min(group, key=lambda g: g[0])
        rows.append({"term": term, "recent": 1, "rank": rank,
                     "note": t("note_reddit", sub=best["sub"], rank=rank),
                     "examples": [f"Reddit r/{p['sub']}: {p['title'][:80]}" for _, p in group],
                     "posts": [{"account": f"r/{p['sub']}", "text": p["title"], "url": p["url"], "likes": 0}
                               for _, p in group]})
    rows.sort(key=lambda r: r["rank"])
    rows = _drop_nested(rows)
    for r in rows:
        del r["rank"]

    today, cutoff = date.today().isoformat(), (date.today() - timedelta(days=c["vocab_days"])).isoformat()
    for _, p in posts:
        for term in title_names(p["title"]):
            vocab[term] = today
    snap.save(path, {term: d for term, d in vocab.items() if d >= cutoff})
    return {"posts": len(posts), "seeded": seeded, "first_run": False, "terms": rows[:TOP]}
