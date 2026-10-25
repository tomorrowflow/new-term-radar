"""Signal ①, Hacker News: names that appear on HN for the first time.

hn_new   names in the stories of the last `window_hours` that reached `min_points` (or, for
         Show HN, `show_min_points`) and that no earlier HN story above `min_points` used in the
         last `vocab_days`. The Algolia HN API is public, no key. The vocabulary lives in the
         state directory; the first run seeds it from the last `vocab_days` of stories.

Stories are ranked by points / sqrt(age in hours + 1), the velocity idea of last30days-skill
(MIT, github.com/mvanhorn/last30days-skill).
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import UA, snap
from .i18n import t
from .terms import MIN_LEN, STOP, _drop_nested, names

API = "https://hn.algolia.com/api/v1/search_by_date"
ITEM = "https://news.ycombinator.com/item?id="
PER_POST = 3
TOP = 25
CHUNK_DAYS = 10     # Algolia returns at most 1000 hits a query; 10 days above 50 points is about 660
_SHOW = re.compile(r"^\s*show hn\s*:\s*(.*)$", re.I)
_PRODUCT_END = re.compile(r" [–—-] |:|,")


def _hits(tag: str, min_points: int, after: int, before: int) -> list[dict]:
    query = urllib.parse.urlencode({
        "tags": tag, "hitsPerPage": 1000, "attributesToRetrieve": "title,points,created_at_i",
        "numericFilters": f"created_at_i>{after},created_at_i<{before},points>={min_points}"})
    req = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as res:
        return json.load(res)["hits"]


def title_names(title: str) -> list[str]:
    """Show HN: the product name before the first dash, colon or comma, when it is 1 to 4 words.
    Otherwise the strong names of the title. At most PER_POST, longest first."""
    m = _SHOW.match(title)
    if m:
        title = m.group(1)
        prefix = _PRODUCT_END.split(title, 1)[0].strip().lower()
        if 1 <= len(prefix.split()) <= 4 and len(prefix) >= MIN_LEN and not all(w in STOP for w in prefix.split()):
            return [prefix]
    return sorted(names(title, strong=True), key=lambda n: (-len(n), n))[:PER_POST]


def _stories(tag: str, min_points: int, after: int, before: int) -> list[dict]:
    return [{"id": h["objectID"], "title": " ".join((h.get("title") or "").split()),
             "points": h.get("points") or 0, "created": h["created_at_i"]}
            for h in _hits(tag, min_points, after, before)]


def _seed(min_points: int, vocab_days: int, start: int, now: int) -> tuple[dict[str, str], int]:
    """Terms of the stories above min_points from `vocab_days` ago up to `start`, by day."""
    vocab: dict[str, str] = {}
    count, lo = 0, now - vocab_days * 86400
    while lo < start:
        hi = min(lo + CHUNK_DAYS * 86400, start)
        for s in _stories("story", min_points, lo, hi + 1):
            count += 1
            day = datetime.fromtimestamp(s["created"], timezone.utc).date().isoformat()
            for term in title_names(s["title"]):
                vocab[term] = max(day, vocab.get(term, ""))
        lo = hi
    return vocab, count


def hn_new(cfg: dict, state: Path) -> dict:
    c = cfg["hn"]
    now = int(time.time())
    start = now - c["window_hours"] * 3600
    path = state / "hn_vocab.json.gz"
    vocab: dict[str, str] | None = snap.load(path)
    seeded = 0
    if vocab is None:
        vocab, seeded = _seed(c["min_points"], c["vocab_days"], start, now)
    stories = {s["id"]: s for tag, pts in (("story", c["min_points"]), ("show_hn", c["show_min_points"]))
               for s in _stories(tag, pts, start, now + 60)}

    found: dict[str, list[dict]] = {}
    for s in stories.values():
        s["score"] = s["points"] / math.sqrt((now - s["created"]) / 3600 + 1)
        s["show"] = bool(_SHOW.match(s["title"]))
        for term in title_names(s["title"]):
            if term not in vocab:
                found.setdefault(term, []).append(s)
    rows = []
    for term, group in found.items():
        best = max(group, key=lambda s: s["score"])
        rows.append({"term": term, "recent": 1, "score": best["score"],
                     "note": t("note_hn_show" if best["show"] else "note_hn", points=best["points"]),
                     "examples": [f"HN: {s['title'][:80]} ({s['points']} points)" for s in group],
                     "posts": [{"account": "HN", "text": s["title"], "url": ITEM + s["id"], "likes": s["points"]}
                               for s in group]})
    rows.sort(key=lambda r: -r["score"])
    rows = _drop_nested(rows)
    for r in rows:
        del r["score"]

    today, cutoff = date.today().isoformat(), (date.today() - timedelta(days=c["vocab_days"])).isoformat()
    for s in stories.values():
        for term in title_names(s["title"]):
            vocab[term] = today
    snap.save(path, {term: d for term, d in vocab.items() if d >= cutoff})
    return {"stories": len(stories), "seeded": seeded, "first_run": False, "terms": rows[:TOP]}
