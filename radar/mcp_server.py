"""The radar as an MCP server, for clients such as Claude Code, Cursor or Claude Desktop.

  pip install mcp            # the only extra dependency, needed for this file alone
  python -m radar.mcp_server

Three tools:
  recent_leads   what the radar has kept, with review verdicts and hit rates. Free, instant.
  keyword_table  the generic terms behind names you give it, sized, with a decision each.
                 Paid (about $0.05 a name plus $0.09 a call) and slow: 40 to 90 seconds a name.
  run_radar      one `sources` or `daily` run, returning its report. Paid, and takes minutes:
                 meant for a first try, the schedule belongs in cron or GitHub Actions.

Where it looks: RADAR_CONFIG (default ./config.json) and RADAR_STATE (default ./state), the
same files the command line uses, so the server sees what the scheduled runs kept. Keys come
from the environment: AISA_API_KEY, and optionally GOANYAPI_API_KEY.

The transport is stdio, so nothing here may print to stdout: the tools run the radar with
its output captured.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import os
import tempfile
from pathlib import Path

from . import __main__ as cli
from . import config

try:                                   # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:                    # mcp 1.x called it FastMCP
    from mcp.server.fastmcp import FastMCP as MCPServer

server = MCPServer(
    "new-term-radar",
    instructions="Names that are starting to surge in AI, and the generic search terms behind them. "
                 "Start with recent_leads (free). keyword_table and run_radar call paid APIs and are slow; "
                 "say so before using them, and keep keyword_table to one or two names a call.")


def _paths() -> tuple[Path, Path]:
    return Path(os.environ.get("RADAR_CONFIG", "config.json")), Path(os.environ.get("RADAR_STATE", "state"))


def _quiet(fn, *args):
    """Run a radar function with stdout captured: on stdio, a stray print breaks the protocol."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


@server.tool()
def recent_leads(days: int = 7) -> dict:
    """Leads the radar kept in the last `days` days (newest first), each with its signals, name
    score and, once reviewed, whether it rose in Google Trends; plus the hit rate per signal.
    Reads the local database only: free and instant. Empty until `sources` or `daily` has run."""
    return cli.kept_leads(_paths()[1], days)


def _keyword_table(names: list[str], repos: dict[str, str]) -> dict:
    cfg_path, state = _paths()
    uc, text = _quiet(cli.keyword_table, config.load(cfg_path), state, names, repos)
    return {"table_markdown": text, "rows": uc.get("rows", []), "incomplete": cli.usecases.degraded(uc),
            "cost_usd": round(uc.get("cost_micros", 0) / 1e6, 3)}


@server.tool()
async def keyword_table(names: list[str], repos: dict[str, str] | None = None) -> dict:
    """The keyword table for new names (models, products, repositories), read now: each name and
    the generic search terms behind it, with estimated searches a day, when it started rising,
    what it is doing now, and prioritize / watch / skip.

    names: one or two is best, eight at most. repos: optional {"name": "owner/repo"} so the
    README is read as evidence. Paid: about $0.05 a name plus $0.09 a call. Slow: 40 to 90
    seconds a name. `incomplete` is true when a data source failed and the table holds less
    than it should; the lines under the table say which."""
    return await asyncio.to_thread(_keyword_table, names, repos or {})


def _run(command: str, skip: list[str]) -> dict:
    cfg_path, state = _paths()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.md"
        args = [command, "--config", str(cfg_path), "--state", str(state), "--out", str(out)]
        _quiet(cli.main, args + (["--skip", ",".join(skip)] if skip else []))
        return {"report_markdown": out.read_text() if out.exists() else "", "wrote_report": out.exists()}


@server.tool()
async def run_radar(command: str = "sources", skip: list[str] | None = None) -> dict:
    """Run the radar once and return its report. command: "sources" (X and Hugging Face only;
    no report when nothing is new) or "daily" (every signal, the keyword table, the 14-day
    review). skip: any of x, hf, hn, reddit, breakout, sitemaps, newsites, gsc, review, judge, usecases.

    Paid and slow: `sources` reads every X account in config.json (about 10 seconds and $0.003
    each); `daily` takes 5 to 15 minutes. It updates the same state the scheduled runs use, so
    do not call it as a substitute for the schedule."""
    if command not in ("sources", "daily"):
        raise ValueError('command must be "sources" or "daily"')
    return await asyncio.to_thread(_run, command, skip or [])


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
