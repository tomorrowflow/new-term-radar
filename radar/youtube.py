"""Context, not a signal: what a YouTube channel that walks through repos says about each one.

GitHub Awesome posts "GitHub Trending Today #56" twice a week: 35 repos, 20 seconds
each, with a description line per repo ("00:11 - learnharness https://github.com/...").
read_new() reads the channels in config.json ("youtube.channels") and keeps one segment
per repo line: where it starts, a deep link to that second, and what the narrator says
until the next line (the video's own English captions). Segments live in radar.db, so a
repo breaking out today can point to the video that showed it a week ago (featured()).

yt-dlp lists a channel's long videos (the UULF playlist: the channel id with UC replaced,
no Shorts) and reads each one's description and caption track. Not the channel's RSS feed:
since late October 2026 feeds/videos.xml answers 404 for most channels most of the time.
YouTube rate-limits and asks some servers to sign in; a video that fails is skipped with its
reason and tried again on the next run, while it is still in the window. A video without
captions keeps its segments without text. recent() lists the same way for feeds.py.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

from .breakout import repo_term

TEXT_MAX = 1000
LIST_MAX = 15
_LINE = re.compile(r"^((?:\d{1,2}:)?\d{1,2}:\d{2})\s*-\s*(.*)$")
_REPO = re.compile(r"^(.*?)\s*https?://github\.com/([\w.-]+/[\w.-]+?)/?$")


def _seconds(stamp: str) -> int:
    s = 0
    for part in stamp.split(":"):
        s = s * 60 + int(part)
    return s


def segments(description: str, duration: int) -> list[dict]:
    """The repo lines of a description, each running until the next timestamp line."""
    marks = []
    for line in description.splitlines():
        m = _LINE.match(line.strip())
        if m:
            repo = _REPO.match(m.group(2))
            marks.append((_seconds(m.group(1)), repo.group(1) if repo else "", repo.group(2) if repo else ""))
    out = []
    for i, (start, name, repo) in enumerate(marks):
        if repo:
            end = marks[i + 1][0] if i + 1 < len(marks) else duration or start + 60
            out.append({"start": start, "end": end, "repo": repo, "name": name or repo.split("/")[1]})
    return out


def _ydl(ipv6: bool):
    """`ipv6` sends every request over IPv6: YouTube answers caption requests from some
    servers' IPv4 addresses with 429 and serves the same server over IPv6."""
    import yt_dlp  # only YouTube needs it; the radar runs without it

    return yt_dlp.YoutubeDL({"skip_download": True, "quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                             "playlistend": LIST_MAX, **({"source_address": "::"} if ipv6 else {})})


def _list(channel_id: str, ipv6: bool = False) -> list[str]:
    """The ids of the channel's latest long videos, newest first."""
    with _ydl(ipv6) as y:
        info = y.extract_info(f"https://www.youtube.com/playlist?list=UULF{channel_id[2:]}", download=False)
    return [e["id"] for e in info.get("entries") or []]


def _published(info: dict) -> datetime:
    if info.get("timestamp"):
        return datetime.fromtimestamp(info["timestamp"], timezone.utc)
    return datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)


def _read(video_id: str, ipv6: bool = False, captions: bool = True) -> tuple[dict, list[tuple[int, str]] | None]:
    """The video's metadata, and (start ms, text) of its English captions as spoken, or None
    when it has none (or `captions` is False). The original track (en-orig): the translated
    "en" track is rate-limited far sooner."""
    with _ydl(ipv6) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        if not captions:
            return info, None
        tracks = info.get("subtitles", {}).get("en") or (info.get("automatic_captions") or {}).get("en-orig") or []
        url = next((f["url"] for f in tracks if f.get("ext") == "json3"), None)
        if not url:
            return info, None
        events = json.load(y.urlopen(url)).get("events", [])
    # Each word at its own time: one caption event can run across the next repo's timestamp.
    return info, [(e["tStartMs"] + w.get("tOffsetMs", 0), w.get("utf8", "")) for e in events for w in e.get("segs") or []]


def _said(caps: list[tuple[int, str]], start: int, end: int) -> str:
    return " ".join("".join(text for ms, text in caps if start * 1000 <= ms < end * 1000).split())[:TEXT_MAX]


def read_new(cfg: dict, conn: sqlite3.Connection) -> dict:
    c = cfg["youtube"]
    since = datetime.now(timezone.utc) - timedelta(days=c["window_days"])
    known = {r[0] for r in conn.execute("SELECT video_id FROM videos")}
    read, failed, feeds_read = [], [], 0
    for ch in c["channels"]:
        try:
            ids = _list(ch["id"], c.get("ipv6", False))
        except Exception as exc:  # noqa: BLE001  yt-dlp's DownloadError
            failed.append(f"{ch['name']}: {_reason(exc)}")
            continue
        feeds_read += 1
        for video_id in ids:
            if video_id in known:
                continue
            try:
                info, caps = _read(video_id, c.get("ipv6", False))
            except Exception as exc:  # noqa: BLE001  yt-dlp's DownloadError, a 429, a sign-in check
                failed.append(f"{video_id}: {_reason(exc)}")
                continue
            when = _published(info)
            if when < since:
                break  # newest first: the rest are older too
            segs = segments(info.get("description") or "", int(info.get("duration") or 0))
            conn.execute("INSERT INTO videos VALUES (?,?,?,?,?,?,?)", (video_id, ch["name"], info["title"],
                         when.date().isoformat(), date.today().isoformat(), len(segs), caps is not None))
            conn.executemany("INSERT OR REPLACE INTO video_segments VALUES (?,?,?,?,?)",
                             [(video_id, s["start"], s["repo"], s["name"], _said(caps or [], s["start"], s["end"])) for s in segs])
            conn.commit()
            read.append({"channel": ch["name"], "title": info["title"], "segments": len(segs), "captions": caps is not None})
    if c["channels"] and not feeds_read:
        raise RuntimeError("no channel feed could be read: " + "; ".join(failed[:3]))
    return {"videos": read, "segments": sum(v["segments"] for v in read), "failed": failed}


def recent(channel_id: str, since: datetime, ipv6: bool = False) -> list[dict]:
    """The channel's long videos published since `since` as feed items (title, url, when, text),
    without captions. Raises OSError with the reason when the channel or a video cannot be read."""
    out = []
    try:
        for video_id in _list(channel_id, ipv6):
            info, _ = _read(video_id, ipv6, captions=False)
            when = _published(info)
            if when < since:
                break
            out.append({"title": info["title"], "url": f"https://www.youtube.com/watch?v={video_id}", "when": when,
                        "text": " ".join((info.get("description") or "").split())})
    except Exception as exc:  # noqa: BLE001  yt-dlp's DownloadError
        raise OSError(_reason(exc)) from exc
    return out


def _reason(exc: Exception) -> str:
    return " ".join(str(exc).split())[:80] or type(exc).__name__


def featured(conn: sqlite3.Connection, repos: list[str]) -> dict[str, dict]:
    """The latest segment per repo (owner/name, any case): {repo: {channel, title, published, url, text}}."""
    found = {repo: about(conn, repo.split("/")[-1], repo, 1) for repo in dict.fromkeys(repos)}
    return {repo: segs[0] for repo, segs in found.items() if segs}


def about(conn: sqlite3.Connection, name: str, repo: str | None = None, limit: int = 3) -> list[dict]:
    """Segments about a name, newest first: the repo's own when it is known, otherwise any
    segment whose repo name or line reads as `name` ("free my tv" for simbasjungles/free-my-tv)."""
    want = repo_term(name)
    rows = conn.execute("SELECT s.video_id, s.start, s.repo, s.name, s.text, v.channel, v.title, v.published FROM video_segments s "
                        "JOIN videos v USING (video_id) ORDER BY v.published DESC, s.start").fetchall()
    out = []
    for vid, start, seg_repo, seg_name, text, channel, title, published in rows:
        if repo.lower() == seg_repo.lower() if repo else want in (repo_term(seg_repo.split("/")[1]), repo_term(seg_name)):
            out.append({"repo": seg_repo, "channel": channel, "title": title, "published": published, "text": text,
                        "url": f"https://youtu.be/{vid}?t={start}"})
    return out[:limit]
