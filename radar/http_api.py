"""The radar over HTTP, for agents that cannot start a local process (an OpenClaw instance,
a container on another host). Standard library only, like the radar.

  RADAR_API_TOKEN=... python -m radar.http_api      # listens on RADAR_HTTP_PORT (default 8787)

Every request but /health needs `Authorization: Bearer $RADAR_API_TOKEN`.

  GET  /health               {"ok": true}
  GET  /leads?days=7         what the radar kept, with review verdicts. Free, instant.
  POST /jobs                 queue a paid run; returns 202 {"id": ...}. Body is one of
                               {"command": "keywords", "names": [...], "repos": {"name": "owner/repo"}}
                               {"command": "pains", "topic": "ai headshot"}
                               {"command": "sources"}
                               {"command": "daily", "skip": ["x", "hf"], "weekly": ["asks", "sellers"]}
  GET  /jobs                 the jobs this process has run, newest first
  GET  /jobs/<id>            one job: status queued|running|done|failed, and its output when done
  GET  /jobs/<id>/report.json  the full report.json of a `daily` job
  GET  /feed.xml             RSS 2.0 of the last 30 `daily` and `sources` reports. Feed readers
                             rarely send headers, so this one also takes ?token=...

Jobs run one at a time, in a subprocess of the command line, so two runs never write the
same state at once. With RADAR_SCHEDULE=on the same queue also takes the schedule of
.github/workflows/radar.yml: `sources` at 00:00, 08:00 and 16:00 UTC, `daily --skip x,hf`
at 01:30 UTC. RADAR_DAILY_AT=HH:MM replaces that with one full `daily` run a day at that
time in the container's TZ. The job list lives in memory; reports stay in RADAR_STATE/reports.
"""
from __future__ import annotations

import hmac
import json
import re
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

from . import __main__ as cli
from . import notify

WEEKLY = {"asks": "--asks", "sellers": "--sellers", "new-sites": "--new-sites", "revenue": "--revenue"}
TIMEOUT_S = 45 * 60
GRACE = timedelta(minutes=10)
FEED_ITEMS = 30
LEAD_ROW = re.compile(r"^\| \*\*([^*]+)\*\*", re.M)
TOKEN_IN_QUERY = re.compile(r"token=[^&\s]+")
SCHEDULE = [("sources", (0, 0), []), ("sources", (8, 0), []), ("sources", (16, 0), []), ("daily", (1, 30), ["x", "hf"])]


def _paths() -> tuple[Path, Path]:
    return Path(os.environ.get("RADAR_CONFIG", "config.json")), Path(os.environ.get("RADAR_STATE", "state"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def argv_for(body: dict, report: Path) -> list[str]:
    """The command line a job body stands for. Raises ValueError on anything else: this is the boundary."""
    cfg, state = _paths()
    common = ["--config", str(cfg), "--state", str(state)]
    command = body.get("command")
    if command == "keywords":
        names = body.get("names")
        if not isinstance(names, list) or not 1 <= len(names) <= 8 or not all(isinstance(n, str) and n.strip() for n in names):
            raise ValueError("keywords: `names` is a list of 1 to 8 names")
        repos = body.get("repos") or {}
        if not isinstance(repos, dict):
            raise ValueError('keywords: `repos` is {"name": "owner/repo"}')
        return ["keywords", *names, *[a for n, r in repos.items() for a in ("--repo", f"{n}={r}")], "--json", *common]
    if command == "pains":
        topic = body.get("topic")
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("pains: `topic` is the kind of tool, e.g. \"ai headshot\"")
        return ["pains", topic, "--json", *common]
    if command in ("sources", "daily"):
        skip, weekly = body.get("skip") or [], body.get("weekly") or []
        if not set(skip) <= cli.SKIPPABLE:
            raise ValueError(f"skip: any of {sorted(cli.SKIPPABLE)}")
        if command == "sources" and weekly or not set(weekly) <= WEEKLY.keys():
            raise ValueError(f"weekly: `daily` only, any of {sorted(WEEKLY)}")
        return [command, *common, "--out", str(report), *(["--skip", ",".join(skip)] if skip else []), *[WEEKLY[w] for w in weekly]]
    raise ValueError("command: keywords, pains, sources or daily")


class Jobs:
    def __init__(self, reports: Path):
        self.reports = reports
        self.all: dict[str, dict] = {}
        self.todo: queue.Queue[str] = queue.Queue()
        self.lock = threading.Lock()

    def submit(self, body: dict, by: str = "api") -> dict:
        job_id = uuid.uuid4().hex[:12]
        argv = argv_for(body, self.reports / f"{body.get('command')}-{job_id}.md")
        job = {"id": job_id, "command": body["command"], "argv": argv, "by": by, "status": "queued", "queued": _now()}
        with self.lock:
            self.all[job_id] = job
        self.todo.put(job_id)
        return job

    def view(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.all.get(job_id)
            return {k: v for k, v in job.items() if k != "argv"} if job else None

    def listing(self) -> list[dict]:
        with self.lock:
            ids = list(self.all)
        return [{k: v for k, v in self.view(i).items() if k not in ("output", "stderr")} for i in reversed(ids)]

    def work(self) -> None:
        while True:
            job_id = self.todo.get()
            with self.lock:
                job = self.all[job_id]
                job.update(status="running", started=_now())
            result = self._run(job["argv"])
            with self.lock:
                job.update(result, finished=_now())

    def _run(self, argv: list[str]) -> dict:
        self.reports.mkdir(parents=True, exist_ok=True)
        try:
            p = subprocess.run([sys.executable, "-m", "radar", *argv], capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return {"status": "failed", "stderr": f"timed out after {TIMEOUT_S // 60} minutes"}
        out: dict = {"status": "done" if p.returncode == 0 else "failed", "exit_code": p.returncode, "stderr": p.stderr[-4000:]}
        if "--json" in argv:
            try:
                out["output"] = json.loads(p.stdout)
            except json.JSONDecodeError:
                out["output"] = p.stdout[-20000:]
        else:
            md = Path(argv[argv.index("--out") + 1])
            # `sources` writes no report when nothing is new; that is a result, not a failure.
            out["output"] = {"report_markdown": md.read_text() if md.exists() else "", "wrote_report": md.exists(),
                             "has_report_json": md.with_suffix(".json").exists()}
        return out

    def report_json(self, job_id: str) -> Path | None:
        with self.lock:
            job = self.all.get(job_id)
        if not job or "--out" not in job["argv"]:
            return None
        path = Path(job["argv"][job["argv"].index("--out") + 1]).with_suffix(".json")
        return path if path.exists() else None


def schedule_from_env() -> tuple[list, timezone | None]:
    """The slots and the zone they are in. RADAR_DAILY_AT is local time (TZ), the default is UTC."""
    at = os.environ.get("RADAR_DAILY_AT", "").strip()
    if not at:
        return SCHEDULE, timezone.utc
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", at):
        raise SystemExit(f"RADAR_DAILY_AT must be HH:MM, got {at!r}.")
    h, m = (int(x) for x in at.split(":"))
    return [("daily", (h, m), [])], None


def due(now: datetime, fired: set[tuple], slots: list = SCHEDULE) -> list[tuple[str, list[str]]]:
    """Scheduled runs whose slot came up in the last GRACE and has not fired yet in this process.
    The grace keeps a restart at 09:00 from replaying the 08:00 run."""
    runs = []
    for command, (h, m), skip in slots:
        slot = (now.date(), h, m)
        if (now.hour, now.minute) >= (h, m) and now - now.replace(hour=h, minute=m, second=0, microsecond=0) < GRACE and slot not in fired:
            fired.add(slot)
            runs.append((command, skip))
    return runs



def _report_html(md: str) -> str:
    try:
        return notify.html(md)
    except ImportError:                # the markdown package is optional outside the image
        return f"<pre>{escape(md)}</pre>"


def feed(reports: Path, base_url: str) -> str:
    """RSS 2.0, one item per report file, newest first. A report's title carries its first
    names, so the reader's list says what came in without opening it."""
    files = sorted((f for f in reports.glob("*.md") if f.name.startswith(("daily-", "sources-"))),
                   key=lambda f: f.stat().st_mtime, reverse=True)[:FEED_ITEMS]
    items = []
    for f in files:
        md = f.read_text()
        when = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)
        names = LEAD_ROW.findall(md)[:3]
        title = f"{f.name.split('-')[0]} · {when:%Y-%m-%d %H:%M} UTC" + (f" · {', '.join(names)}" if names else "")
        items.append(f"<item><title>{escape(title)}</title><guid isPermaLink=\"false\">{escape(f.stem)}</guid>"
                     f"<pubDate>{format_datetime(when)}</pubDate><description>{escape(_report_html(md))}</description></item>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
            f"<title>new-term-radar</title><link>{escape(base_url)}</link>"
            "<description>New AI names and the search terms behind them</description>"
            f"{''.join(items)}</channel></rss>\n")


def schedule(jobs: Jobs, slots: list, tz: timezone | None) -> None:
    fired: set[tuple] = set()
    while True:
        for command, skip in due(datetime.now(tz), fired, slots):
            jobs.submit({"command": command, "skip": skip}, by="schedule")
        time.sleep(30)


def handler(jobs: Jobs, token: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body, content_type: str = "application/json; charset=utf-8") -> None:
            data = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self, query_token: str = "") -> bool:
            given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip() or query_token
            if hmac.compare_digest(given.encode(), token.encode()):
                return True
            self._send(401, {"error": "missing or wrong bearer token"})
            return False

        def do_GET(self) -> None:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            if parts == ["health"]:
                return self._send(200, {"ok": True})
            if parts == ["feed.xml"]:
                if not self._authorized(parse_qs(url.query).get("token", [""])[0]):
                    return
                base = f"http://{self.headers.get('Host', 'localhost')}/"
                return self._send(200, feed(jobs.reports, base), "application/rss+xml; charset=utf-8")
            if not self._authorized():
                return
            if parts == ["leads"]:
                try:
                    days = int(parse_qs(url.query).get("days", ["7"])[0])
                except ValueError:
                    return self._send(400, {"error": "days is a number"})
                return self._send(200, cli.kept_leads(_paths()[1], days))
            if parts == ["jobs"]:
                return self._send(200, {"jobs": jobs.listing()})
            if len(parts) == 2 and parts[0] == "jobs":
                job = jobs.view(parts[1])
                return self._send(200, job) if job else self._send(404, {"error": "no such job"})
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "report.json":
                path = jobs.report_json(parts[1])
                return self._send(200, json.loads(path.read_text())) if path else self._send(404, {"error": "no report.json for this job"})
            self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if urlparse(self.path).path.rstrip("/") != "/jobs":
                return self._send(404, {"error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("the body is a JSON object")
                job = jobs.submit(body)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._send(400, {"error": str(exc)})
            self._send(202, {"id": job["id"], "status": job["status"], "poll": f"/jobs/{job['id']}"})

        def log_message(self, fmt, *args) -> None:
            sys.stderr.write(f"{_now()} {self.address_string()} {TOKEN_IN_QUERY.sub('token=…', fmt % args)}\n")

    return Handler


def main() -> None:
    token = os.environ.get("RADAR_API_TOKEN", "").strip()
    if len(token) < 16:
        raise SystemExit("Set RADAR_API_TOKEN to a random string of 16 characters or more (openssl rand -hex 24).")
    cfg, state = _paths()
    if not cfg.exists():
        shutil.copy(Path(__file__).resolve().parent.parent / "config.example.json", cfg)
        print(f"No config at {cfg}: copied config.example.json there. Edit it for your own X accounts and sitemaps.", file=sys.stderr)
    jobs = Jobs(state / "reports")
    threading.Thread(target=jobs.work, daemon=True).start()
    if os.environ.get("RADAR_SCHEDULE", "").lower() in ("1", "on", "true", "yes"):
        slots, tz = schedule_from_env()
        threading.Thread(target=schedule, args=(jobs, slots, tz), daemon=True).start()
        if tz is None:
            print(f"Schedule on: daily at {os.environ['RADAR_DAILY_AT'].strip()} {time.strftime('%Z')}.", file=sys.stderr)
        else:
            print("Schedule on: sources at 00:00, 08:00, 16:00 UTC; daily at 01:30 UTC.", file=sys.stderr)
    port = int(os.environ.get("RADAR_HTTP_PORT", "8787"))
    print(f"new-term-radar HTTP API on :{port}", file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", port), handler(jobs, token)).serve_forever()


if __name__ == "__main__":
    main()
