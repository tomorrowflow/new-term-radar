"""Signal ①, Reddit: names that appear in a day's new posts of some subreddits for the first time.

reddit_new   names in the titles of the day's newest posts in the configured subreddits that no
             post of the last `vocab_days` used. Reddit closed its RSS feeds (13 November 2026)
             and new Data API keys, so the posts come from AIsa's Reddit search, one query a
             subreddit ($0.0019). That search returns the 25 newest posts; its `top` sort ignores
             the time window, so it is used only to seed the vocabulary on the first run.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import snap, social
from .hn import title_names
from .i18n import t
from .terms import _drop_nested

TOP = 50          # before the judge: most post-title words are not names, so leave it room


def _posts(sub: str, sort: str) -> tuple[list[dict], int]:
    """A subreddit's posts by AIsa's search, highest score first."""
    posts, cost = social.reddit(f"subreddit:{sub}", time="day", sort=sort, limit=100)
    posts = [{"id": p["id"], "title": p["title"], "url": p["url"], "sub": p["subreddit"] or sub, "score": p["score"],
              "published": datetime.fromtimestamp(p["created"], timezone.utc).date().isoformat() if p["created"] else ""}
             for p in posts if (p["subreddit"] or sub).lower() == sub.lower()]
    return sorted(posts, key=lambda p: -p["score"]), cost


def _seed(c: dict) -> tuple[dict[str, str], int, int]:
    vocab: dict[str, str] = {}
    count = cost = 0
    for sub in c["subreddits"]:
        for sort in ("top", "new"):
            page, spent = _posts(sub, sort)
            cost += spent
            for p in page:
                count += 1
                for term in title_names(p["title"]):
                    vocab[term] = max(p["published"], vocab.get(term, ""))
    return vocab, count, cost


def reddit_new(cfg: dict, state: Path) -> dict:
    c = cfg["reddit"]
    path = state / "reddit_vocab.json.gz"
    vocab: dict[str, str] | None = snap.load(path)
    seeded = cost = 0
    if vocab is None:
        vocab, seeded, cost = _seed(c)
    posts = []
    for sub in c["subreddits"]:
        page, spent = _posts(sub, "new")
        cost += spent
        posts += list(enumerate(page, 1))

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
                     "posts": [{"account": f"r/{p['sub']}", "text": p["title"], "url": p["url"], "likes": p["score"]}
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
    return {"posts": len(posts), "seeded": seeded, "first_run": False, "cost_micros": cost, "terms": rows[:TOP]}
