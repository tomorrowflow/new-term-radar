"""config.json: what to watch. Copy config.example.json and edit it.

Everything has a default except the X accounts and the sitemaps, which only you can
choose. Secrets are never read from this file; they come from the environment.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import i18n, terms

DEFAULTS = {
    "lang": "en",
    "x_accounts": {"labs": [], "bloggers": []},
    "sitemaps": {},
    "topics": {},
    "known_terms": [],
    "stop_words": [],
    "judge": {"model": "gpt-4.1-mini", "min_score": 0.5},
    "breakout": {"window_days": 14, "min_stars": 300, "velocity_min": 200, "delta_min": 400, "categories": {
        "ai": {"topics": ["ai", "llm", "agents", "ai-agents", "ai-agent", "machine-learning", "deep-learning",
                          "generative-ai", "mcp", "rag"], "min_stars": 100, "velocity_min": 50, "delta_min": 100, "gain_min": 20},
        "self-hosted": {"topics": ["self-hosted", "selfhosted", "homelab", "self-hosting"],
                        "min_stars": 30, "velocity_min": 15, "delta_min": 40, "gain_min": 5}}},
    "new_sites": {"queries": {"ai": "%ai%"}, "weekday": 0, "window_days": 90, "min_etv": 1000, "min_keywords": 20},
    "hn": {"min_points": 50, "show_min_points": 15, "window_hours": 48, "vocab_days": 60},
    "reddit": {"subreddits": ["LocalLLaMA", "singularity", "ClaudeAI", "OpenAI", "ChatGPT", "StableDiffusion", "comfyui",
                            "MachineLearning", "artificial", "selfhosted"],
               "per_request": 10, "limit": 100, "seed_pages": 5, "vocab_days": 60},
    "feeds": {"window_hours": 48, "vocab_days": 60, "sources": [
        {"name": "Simon Willison", "url": "https://simonwillison.net/atom/everything/", "role": "lead"},
        {"name": "Hugging Face blog", "url": "https://huggingface.co/blog/feed.xml", "role": "lead"},
        {"name": "Every Vibe Check", "url": "https://every.to/vibe-check/feed", "role": "lead"},
        {"name": "Latent Space / AINews", "url": "https://www.latent.space/feeds/all.xml", "role": "confirm"},
        {"name": "AI News Radar", "url": "https://raw.githubusercontent.com/LearnPrompt/ai-news-radar/master/data/feed-en.xml", "role": "confirm"},
        {"name": "follow-builders", "url": "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json",
         "format": "follow-builders", "role": "lead"},
        {"name": "Matthew Berman", "url": "https://www.youtube.com/feeds/videos.xml?playlist_id=UULFawZsQWqfGSbCI5yjkdVkTA", "role": "lead"},
        {"name": "Theo", "url": "https://www.youtube.com/feeds/videos.xml?playlist_id=UULFbRP3c757lWg9M-U7TyEkXA", "role": "lead"},
        {"name": "Cole Medin", "url": "https://www.youtube.com/feeds/videos.xml?playlist_id=UULFMwVTLZIRRUyyVrkjDpn4pA", "role": "lead"},
        {"name": "Sam Witteveen", "url": "https://www.youtube.com/feeds/videos.xml?playlist_id=UULF55ODQSvARtgSyc8ThfiepQ", "role": "lead"},
        {"name": "Matt Pocock", "url": "https://www.youtube.com/feeds/videos.xml?playlist_id=UULFswG6FSbgZjbWtdf_hMLaow", "role": "confirm"}]},
    "youtube": {"window_days": 14, "ipv6": False, "channels": [{"name": "GitHub Awesome", "id": "UC9Rrud-8CaHokDtK9FszvRg"}]},
    "review": {"after_days": 14, "per_day": 5},
    "usecases": {"per_day": 5, "model": "claude-sonnet-4-6", "min_volume": 50, "trends_max": 10, "recheck_days": 3,
                 "location_code": 2840, "benchmark": "gpts", "min_daily": 300, "related": True, "related_geo": "US",
                 "country_trends": True, "country_min_volume": 1000, "country_max": 8},
    "competition": {"max_rows": 8, "weak_dr": 30, "location_code": 2840},
    "revenue": {"weekday": 0, "pages_growth": 3, "pages_new": 3, "min_revenue_usd": 500, "category": "", "max_leads": 10},
    "asks": {"weekday": 0, "time": "week", "min_score": 2, "model": "gpt-4.1-mini", "min_volume": 50, "max_leads": 12,
             "location_code": 2840, "patterns": [
                 '"is there an ai tool"', '"is there an ai that"', '"is there a tool that"', '"any ai tool for"',
                 '"looking for an ai tool"', '"i wish there was a tool"', '"is there a way to automatically"',
                 '"how do i automate"', '"tool that can automatically"', '"recommend an ai"', '"best ai tool for"',
                 '"ai tool that can"']},
    "pains": {"model": "gpt-4.1-mini", "extensions": 5, "review_pages": 2, "max_stars": 3, "reddit_time": "year", "videos": 3,
              "reddit_queries": ["{k} alternative", "{k} frustrating", '"{k}" "doesn\'t work"', "{k} wish there was"]},
    "tutorials": {"max_leads": 3, "videos": 3, "model": "gpt-4.1-mini"},
    "sellers": {"weekday": 0, "platforms": ["stripe", "polar", "lemonsqueezy", "gumroad", "creem"], "per_platform": 100,
                "max_leads": 12, "visits": True, "rank_max": 350, "max_visits": 300000, "pool": 60, "model": "gpt-4.1-mini"},
    "gsc": {},
}


def load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"{path} not found: copy config.example.json to {path.name} and edit it")
    raw = json.loads(path.read_text())
    cfg = {}
    for k, default in DEFAULTS.items():
        v = raw.get(k, default)
        cfg[k] = {**default, **v} if isinstance(default, dict) and default and isinstance(v, dict) else v
    cfg["_dir"] = path.resolve().parent
    i18n.set_lang(cfg["lang"])
    terms.add_stop(cfg["stop_words"])
    return cfg


def known(cfg: dict, term: str) -> str:
    """The entry of known_terms that already covers this term, if any: what you have a
    page for, or never want reported."""
    for k in cfg["known_terms"]:
        k = k.lower()
        if k and (k in term or term in k):
            return k
    return ""
