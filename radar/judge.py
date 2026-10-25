"""Is this term the name of a new thing, or a common word?

The signals surface whatever is new in the text they read: "Opus 5.5" and "HyperFrames",
but also "Lots", "Trained" and "voice notes". A small model reads each term with a few
places it appeared and scores it from 0 to 1; the report leads with the names. It also
flags a name that is old, a product or company widely known for more than a year
("tensorflow", "cloudflare"): that one's score is capped at OLD_MAX, below the report's cut,
because a signal found it new to its own vocabulary, not new to the world. A new version of
an old line ("obs studio 33", "deepseek 4.1 flash") is not old.

One chat call per CHUNK terms through AIsa's OpenAI-compatible endpoint, the model set in
config.json (judge.model). Without a key, or when the call fails, the terms keep no
score and the report lists them all: the judge sorts the report, it never blocks it.
"""
from __future__ import annotations

import json
import re

from . import aisa

CHUNK = 25
OLD_MAX = 0.4   # under judge.min_score (0.5 by default)
SYSTEM = (
    "You sort terms for a radar that looks for new product names. For each term, score from 0.0 to 1.0 how likely "
    "it is the NAME of one specific product, model, tool, library, company or technique, as opposed to a common "
    "word, a generic category, a feature description or a fragment of a sentence. `examples` are places where the "
    "term appeared (posts, repository descriptions, model ids, URL paths); use them.\n"
    "Names: 'opus 5.5', 'hyperframes', 'seedance', 'qwen image 2.1', 'strata' (when the examples show a project "
    "called Strata).\n"
    "Not names: 'dashboard', 'voice notes', 'pdf', 'lots', 'trained', 'video generator', 'new release', 'today'.\n"
    "Also set `old`: true when the term names something ESTABLISHED, a product, company, library, service or "
    "hardware that has existed for more than about a year and is widely known ('tensorflow', 'cloudflare', "
    "'civitai', 'ms office', 'nvidia rtx 3090', 'google', 'claude'). A new version, model or release of an "
    "established line is NOT old, the version is the news: 'obs studio 33', 'deepseek 4.1 flash', "
    "'kimi k3.1 pro', 'qwen3.8-27b'. A term you do not recognise is not old.\n"
    "Answer with JSON only, one object per term, in the same order: [{\"i\": 0, \"score\": 0.9, \"old\": false}, ...]"
)


def parse(answer: str, n: int) -> dict[int, tuple[float, bool]]:
    """{index: (score, old)} from the model's answer; indexes outside 0..n-1 and bad scores are dropped."""
    m = re.search(r"\[.*\]", answer, re.S)
    try:
        items = json.loads(m.group(0)) if m else []
    except ValueError:
        return {}
    out = {}
    for it in items if isinstance(items, list) else []:
        try:
            i, score = int(it["i"]), float(it["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < n and 0.0 <= score <= 1.0:
            out[i] = (score, it.get("old") is True)
    return out


def score(rows: list[dict], model: str) -> dict:
    """Sets r["named"] on each row the model scored, and r["old"] on an old name. Returns {"judged": n} or {"error": ...}."""
    if not model:
        return {"judged": 0, "skipped": True}
    judged = 0
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start:start + CHUNK]
        user = json.dumps([{"i": i, "term": r["term"], "examples": [e[:160] for e in r.get("examples", [])[:3]]}
                           for i, r in enumerate(chunk)], ensure_ascii=False)
        try:
            scores = parse(aisa.chat(model, SYSTEM, user), len(chunk))
        except aisa.AisaError as exc:
            return {"judged": judged, "error": str(exc)[:160]}
        for i, (s, old) in scores.items():
            chunk[i]["named"] = min(s, OLD_MAX) if old else s
            if old:
                chunk[i]["old"] = True
        judged += len(scores)
    return {"judged": judged}
