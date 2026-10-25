import json
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from http.server import ThreadingHTTPServer

import pytest

from radar import http_api, store
from test_radar import _lead

TOKEN = "t" * 24


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("RADAR_STATE", str(tmp_path / "state"))
    jobs = http_api.Jobs(tmp_path / "state" / "reports")
    ran = []

    def fake_run(argv):                       # a real run would call paid APIs
        ran.append(argv)
        return {"status": "done", "exit_code": 0, "stderr": "", "output": {"rows": [{"phrase": "photocraft"}]}}
    monkeypatch.setattr(jobs, "_run", fake_run)
    threading.Thread(target=jobs.work, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", 0), http_api.handler(jobs, TOKEN))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", tmp_path, ran
    server.shutdown()


def call(url, path, body=None, token=TOKEN):
    req = urllib.request.Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health_is_open_and_everything_else_needs_the_token(api):
    url, _, _ = api
    assert call(url, "/health", token=None) == (200, {"ok": True})
    assert call(url, "/leads", token=None)[0] == 401
    assert call(url, "/leads", token="wrong" * 5)[0] == 401
    assert call(url, "/jobs", {"command": "sources"}, token=None)[0] == 401


def test_leads_reads_what_the_radar_kept(api):
    url, tmp_path, _ = api
    store.record(store.connect(tmp_path / "state"), [_lead("photocraft", ["breakout"], named=0.95)], date.today())
    code, body = call(url, "/leads?days=7")
    assert code == 200 and [r["term"] for r in body["leads"]] == ["photocraft"]


def test_a_keywords_job_runs_the_command_line_and_returns_its_output(api):
    url, _, ran = api
    code, queued = call(url, "/jobs", {"command": "keywords", "names": ["photocraft"], "repos": {"photocraft": "storytold/photocraft"}})
    assert code == 202
    for _ in range(50):
        code, job = call(url, f"/jobs/{queued['id']}")
        if job["status"] == "done":
            break
        threading.Event().wait(0.05)
    assert job["output"] == {"rows": [{"phrase": "photocraft"}]}
    assert ran[0][:5] == ["keywords", "photocraft", "--repo", "photocraft=storytold/photocraft", "--json"]
    assert [j["id"] for j in call(url, "/jobs")[1]["jobs"]] == [queued["id"]]


@pytest.mark.parametrize("body", [
    {"command": "rm -rf"},
    {"command": "keywords", "names": []},
    {"command": "keywords", "names": [f"n{i}" for i in range(9)]},
    {"command": "daily", "skip": ["everything"]},
    {"command": "sources", "weekly": ["asks"]},
    {"command": "pains"},
    ["not", "an", "object"],
])
def test_bad_job_bodies_are_refused_and_nothing_runs(api, body):
    url, _, ran = api
    code, out = call(url, "/jobs", body)
    assert code == 400 and out["error"] and ran == []


def test_daily_flags_map_to_the_command_line(tmp_path):
    argv = http_api.argv_for({"command": "daily", "skip": ["x", "hf"], "weekly": ["asks"]}, tmp_path / "r.md")
    assert argv[0] == "daily" and "--skip" in argv and argv[argv.index("--skip") + 1] == "x,hf" and "--asks" in argv


def test_schedule_fires_each_slot_once_and_not_after_a_late_restart():
    fired = set()
    at = lambda h, m: datetime(2026, 10, 25, h, m, tzinfo=timezone.utc)
    assert http_api.due(at(7, 59), fired) == []
    assert http_api.due(at(8, 0), fired) == [("sources", [])]
    assert http_api.due(at(8, 5), fired) == []                  # already fired
    assert http_api.due(at(1, 31), set()) == [("daily", ["x", "hf"])]
    assert http_api.due(at(9, 0), set()) == []                  # a restart at 09:00 does not replay 08:00


def test_feed_lists_reports_newest_first_and_takes_the_token_in_the_query(api, capsys):
    import os
    import xml.etree.ElementTree as ET
    url, tmp_path, _ = api
    reports = tmp_path / "state" / "reports"
    reports.mkdir(parents=True)
    (reports / "daily-aaa.md").write_text("### Candidate new terms\n\n| Term | Signals |\n|---|---|\n| **gridcraft** | GitHub breakout |\n| **artex** | GitHub breakout |\n")
    (reports / "sources-bbb.md").write_text("| **qwen image 2.1** | Hugging Face |\n")
    (reports / "keywords-ccc.md").write_text("not a report")
    os.utime(reports / "daily-aaa.md", (1_000_000_000, 1_000_000_000))

    assert urllib.request.urlopen(f"{url}/health").status == 200
    with pytest.raises(urllib.error.HTTPError) as no_token:
        urllib.request.urlopen(f"{url}/feed.xml")
    assert no_token.value.code == 401
    with urllib.request.urlopen(f"{url}/feed.xml?token={TOKEN}") as r:
        assert r.headers["Content-Type"].startswith("application/rss+xml")
        channel = ET.fromstring(r.read()).find("channel")
    titles = [i.findtext("title") for i in channel.findall("item")]
    assert len(titles) == 2 and titles[0].startswith("sources") and titles[1].endswith("gridcraft, artex")
    assert "<table" in channel.findall("item")[1].findtext("description")
    assert TOKEN not in capsys.readouterr().err        # the request log must not keep the token
