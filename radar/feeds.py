"""Signal ①, feeds: names new to the blogs, newsletters and YouTube channels you follow.

feeds_new   names in the items of the last `window_hours` of the RSS and Atom feeds in
            config.json ("feeds.sources") that no item of the last `vocab_days` named. A
            source's `role` is "lead" (it names new things first: Simon Willison, the Hugging
            Face blog, launch-day reviews) or "confirm" (a digest that repeats them a day later:
            AINews). A name from a lead source is reported; a name only digests carry is
            reported once two sources carry it. The first run seeds the vocabulary from the
            older items the feeds still hold, so it reports from day one.

Headlines are title case ("Gemini 4 Argon Puts Google Back On the Frontier"), so the
capitalisation rules that find names in posts return fragments here. A small model (the
judge's) reads each headline with the first lines of its item and lists the names in it.
Without a key it falls back to the Hacker News title rules, which miss most of them.

A source's `format` says how to read it: "rss" (RSS or Atom, the default, from `url`);
"youtube", a channel's long videos (by channel `id`, read with yt-dlp, youtube.recent();
YouTube's own RSS feeds answer 404 most of the time); or "follow-builders", the
feed-x.json that github.com/zarazhangrui/follow-builders commits every morning: a day of
posts by the AI builders it follows on X, read without an X key. Each post counts as its
author's (@swyx), so two builders naming a thing are two sources.
"""
from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

from . import UA, aisa, snap, youtube
from .hn import title_names
from .i18n import t
from .terms import MIN_LEN, _drop_nested

TOP = 30
CHUNK = 40
SNIPPET = 300
_ATOM = "{http://www.w3.org/2005/Atom}"
_MEDIA = "{http://search.yahoo.com/mrss/}"
_TAG = re.compile(r"<[^>]+>")
SYSTEM = (
    "You read headlines from AI blogs, newsletters and YouTube channels for a radar that looks for new product "
    "names. For each item, list the NAMES of specific products, models, tools, libraries, companies, papers or "
    "techniques it mentions: 'gemini 4 argon', 'mistral large 4', 'opus 5.5', 'granite switch 4.1.1', 'duet' "
    "(when the item is about a product called Duet). Not names: generic words and categories ('agents', "
    "'local ai', 'token spend'), people, the publication itself, or fragments of the headline. Keep a version "
    "with its name. Lowercase. Most items name zero to three things.\n"
    "Answer with JSON only, one object per item, in the same order: [{\"i\": 0, \"names\": [\"opus 5.5\"]}, ...]"
)


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as res:
        return res.read()


def _when(text: str) -> datetime | None:
    text = (text or "").strip()
    try:
        d = parsedate_to_datetime(text) if text[:1].isalpha() else datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _plain(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", html or "")).split())[:SNIPPET]


def _items(xml: bytes) -> list[dict]:
    """RSS 2.0 items or Atom entries (YouTube's included): title, url, when, the first lines."""
    root, out = ET.fromstring(xml), []
    for e in root.iter(_ATOM + "entry"):
        link = e.find(_ATOM + "link")
        body = e.findtext(_ATOM + "summary") or e.findtext(_ATOM + "content") or e.findtext(f"{_MEDIA}group/{_MEDIA}description")
        out.append({"title": e.findtext(_ATOM + "title"), "url": link.get("href", "") if link is not None else "",
                    "when": _when(e.findtext(_ATOM + "published") or e.findtext(_ATOM + "updated")), "text": body})
    for e in root.iter("item"):
        out.append({"title": e.findtext("title"), "url": (e.findtext("link") or "").strip(),
                    "when": _when(e.findtext("pubDate")), "text": e.findtext("description")})
    for it in out:
        it["title"], it["text"] = " ".join((it["title"] or "").split()), _plain(it["text"])
    return [it for it in out if it["title"] and it["when"]]


def _builders(body: bytes) -> list[dict]:
    """follow-builders' feed-x.json: {"x": [{"handle", "tweets": [{"text", "url", "createdAt"}]}]}."""
    out = []
    for b in json.loads(body).get("x", []):
        for tw in b.get("tweets", []):
            text = " ".join((tw.get("text") or "").split())
            out.append({"title": text[:200], "url": tw.get("url", ""), "when": _when(tw.get("createdAt")), "text": text[:SNIPPET],
                        "by": f"@{b['handle']}"})
    return [it for it in out if it["title"] and it["when"]]


# Each reads a source into items: (source, cfg, since: the window's start).
FORMATS = {
    "rss": lambda src, cfg, since: _items(_fetch(src["url"])),
    "follow-builders": lambda src, cfg, since: _builders(_fetch(src["url"])),
    # Only the window: a video costs a request, and the vocabulary is seeded from the other sources.
    "youtube": lambda src, cfg, since: youtube.recent(src["id"], since, cfg["youtube"].get("ipv6", False)),
}


def _names(items: list[dict], model: str) -> str:
    """Sets it["names"] on every item. Returns how: "model", or "rules" with the reason the model was not used."""
    if model:
        try:
            for start in range(0, len(items), CHUNK):
                chunk = items[start:start + CHUNK]
                user = json.dumps([{"i": i, "headline": it["title"], "start": it["text"][:200]} for i, it in enumerate(chunk)],
                                  ensure_ascii=False)
                for i, found in _parse(aisa.chat(model, SYSTEM, user, max_tokens=3000), len(chunk)).items():
                    chunk[i]["names"] = found
            for it in items:
                it.setdefault("names", [])
            return "model"
        except aisa.AisaError as exc:
            how = f"rules ({str(exc)[:80]})"
    else:
        how = "rules"
    for it in items:
        it["names"] = title_names(it["title"])
    return how


def _parse(answer: str, n: int) -> dict[int, list[str]]:
    m = re.search(r"\[.*\]", answer, re.S)
    try:
        rows = json.loads(m.group(0)) if m else []
    except ValueError:
        return {}
    out = {}
    for r in rows if isinstance(rows, list) else []:
        try:
            i, found = int(r["i"]), r["names"]
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < n and isinstance(found, list):
            out[i] = list(dict.fromkeys(" ".join(str(x).lower().split()) for x in found
                                        if isinstance(x, str) and len(x.strip()) >= MIN_LEN))[:5]
    return out


def feeds_new(cfg: dict, state: Path) -> dict:
    c = cfg["feeds"]
    now = datetime.now(timezone.utc)
    since, oldest = now - timedelta(hours=c["window_hours"]), now - timedelta(days=c["vocab_days"])
    items, failed = {}, []
    for src in c["sources"]:
        try:
            for it in FORMATS[src.get("format", "rss")](src, cfg, since):
                if it["when"] >= oldest:
                    items.setdefault(it["url"] or f"{src['name']}:{it['title']}", {**it, "source": it.get("by", src["name"]),
                                                                                  "role": src.get("role", "lead")})
        except (OSError, ET.ParseError, ValueError, KeyError) as exc:
            failed.append(f"{src['name']}: {type(exc).__name__}")
    if not c["sources"] or len(failed) == len(c["sources"]):
        raise RuntimeError("no feed could be read: " + "; ".join(failed))

    path = state / "feeds_vocab.json.gz"
    vocab: dict[str, str] | None = snap.load(path)
    first_run = vocab is None
    recent = [it for it in items.values() if it["when"] >= since]
    older = [it for it in items.values() if it["when"] < since]
    how = _names(recent + older if first_run else recent, cfg["judge"]["model"])
    if first_run:
        vocab = {n: it["when"].date().isoformat() for it in older for n in it["names"]}

    found: dict[str, list[dict]] = {}
    for it in recent:
        for name in it["names"]:
            if name not in vocab:
                found.setdefault(name, []).append(it)
    rows = []
    for name, group in found.items():
        sources = list(dict.fromkeys(it["source"] for it in group))
        lead = any(it["role"] == "lead" for it in group)
        if not lead and len(sources) < 2:
            continue
        group.sort(key=lambda it: it["when"])
        rows.append({"term": name, "recent": len(sources), "lead": lead, "first": group[0]["when"],
                     "note": t("note_feeds", sources=", ".join(sources)),
                     "examples": [f"{it['source']}: {it['title'][:80]}" for it in group],
                     "posts": [{"account": it["source"], "text": it["title"], "url": it["url"], "likes": 0} for it in group]})
    rows.sort(key=lambda r: (-r["recent"], -r["lead"], r["first"]))
    rows = _drop_nested(rows)
    for r in rows:
        del r["lead"], r["first"]

    # A name only one digest carried stays out of the vocabulary: a lead source or a second
    # digest may still report it while the item is in the window.
    today, cutoff = date.today().isoformat(), (date.today() - timedelta(days=c["vocab_days"])).isoformat()
    waiting = {name for name in found if name not in {r["term"] for r in rows}}
    for it in recent:
        for name in it["names"]:
            if name not in waiting:
                vocab[name] = today
    snap.save(path, {n: d for n, d in vocab.items() if d >= cutoff})
    return {"items": len(recent), "feeds": len(c["sources"]) - len(failed), "failed": failed, "names_by": how,
            "seeded": len(older) if first_run else 0, "first_run": False, "terms": rows[:TOP]}
