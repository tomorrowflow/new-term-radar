---
name: new-term-radar-remote
description: Names of new AI models, products and repositories seen in the last 24 hours (X, Hugging Face, Hacker News, Reddit, AI blogs, newsletters, YouTube channels and AI builders' X posts, GitHub breakouts overall and per category such as ai and self-hosted, sitemaps), and a keyword priority table for them sized against Google Ads volume and Google Trends, with prioritize, watch or skip. Use when the user wants today's new AI keywords, an AI trend radar, keyword research on names they already have, or what people complain about for a kind of AI tool. Talks to a new-term-radar container over HTTP with curl.
metadata: {"openclaw": {"emoji": "📡", "requires": {"bins": ["curl"], "env": ["RADAR_URL", "RADAR_API_TOKEN"]}, "primaryEnv": "RADAR_API_TOKEN"}}
---

# New-term radar (container)

The radar runs in a Docker container that exposes an HTTP API at `$RADAR_URL`, for example `http://127.0.0.1:8787`. Every call except `/health` sends the token:

```bash
AUTH="Authorization: Bearer $RADAR_API_TOKEN"
curl -fsS "$RADAR_URL/health"
```

If `/health` fails, stop and tell the user the container is not reachable at `$RADAR_URL`. A 401 means the token in OpenClaw does not match `RADAR_API_TOKEN` in the container's `.env`.

## Always read fresh

The radar runs on its own every morning, so an answer from an earlier turn is stale by the next day. For any question about what the radar found, call the API in this turn. Never repeat names, tables or caveats from earlier messages, and say the run date of what you report.

## Free: today's scheduled report

For "what did the radar find today" and similar, start here. The scheduled daily run is a job like any other:

```bash
ID=$(curl -fsS -H "$AUTH" "$RADAR_URL/jobs" | grep -oE '"id": "[^"]+", "command": "daily", "by": "[^"]+", "status": "done"' | head -1 | cut -d'"' -f4)
curl -fsS -H "$AUTH" "$RADAR_URL/jobs/$ID"
```

`GET /jobs` lists newest first. Take the first `daily` job with `"status": "done"`, and check its `finished` date. If it is not today, say when the last run was. `output.report_markdown` holds the report and `GET /jobs/$ID/report.json` the full data.

## Free: what the radar already kept

Leads across several days. It reads the database that the scheduled runs fill. It costs nothing and returns at once.

```bash
curl -fsS -H "$AUTH" "$RADAR_URL/leads?days=7"
```

The result has `leads` (each with `term`, `first_seen`, `signals`, `verdict`, `days_ahead`, `note`) and `hit_rates`. If it is empty, the radar has not run yet. Say so and offer a run.

## Free: what a name is

When the user asks about a name, or before you explain a lead, read what the radar kept about it. It costs nothing and returns at once. Give up to 8 names, each as its own `name` parameter (URL-encode spaces as `%20`):

```bash
curl -fsS -H "$AUTH" "$RADAR_URL/context?name=free%20my%20tv&name=tstack"
```

Each entry in `names` has `lead` and `videos`. `lead` is null when the radar never reported the name; otherwise it has `signals`, `notes`, `first_seen`, `verdict` and `links` (the posts that carried it: `by`, `text`, `url`). `videos` lists what a YouTube channel such as GitHub Awesome said about it: `text` is the narrator's words and `url` opens the video at that second. Explain the name from these words and links, cite one of them, and do not guess beyond them. Empty `videos` and a null `lead` mean the radar knows nothing about the name. Say so.

## Paid: queue a job, then poll it

Paid work runs as a job. A job takes minutes, and the container runs one job at a time. Tell the user the cost and the time before you queue one.

| Body for `POST /jobs` | What it does | Cost and time |
|---|---|---|
| `{"command": "keywords", "names": ["photocraft"], "repos": {"photocraft": "storytold/photocraft"}}` | The keyword table for names the user gives. `repos` is optional. Use at most 8 names in one call | About $0.05 a name plus $0.09 a call. 40 to 90 seconds a name |
| `{"command": "pains", "topic": "ai headshot"}` | What people complain about for a kind of tool | About $0.05. 1 to 2 minutes |
| `{"command": "sources"}` | X and Hugging Face only | $0.003 an X account. About 10 seconds each |
| `{"command": "daily", "skip": ["x", "hf"]}` | Every other signal, then the keyword table for up to 5 names. Leave out `skip` to include X and Hugging Face. `"weekly": ["asks", "sellers"]` adds the Reddit asks and new sellers today | About $0.30. 10 to 15 minutes |

```bash
ID=$(curl -fsS -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"command": "keywords", "names": ["photocraft", "synthid detector"]}' \
  "$RADAR_URL/jobs" | sed -E 's/.*"id": *"([^"]+)".*/\1/')
until curl -fsS -H "$AUTH" "$RADAR_URL/jobs/$ID" | grep -qE '"status": *"(done|failed)"'; do sleep 20; done
curl -fsS -H "$AUTH" "$RADAR_URL/jobs/$ID"
```

`GET /jobs` lists recent jobs. Before you queue `daily` or `sources`, check that one is not already queued or running, because the schedule may have started it. Do not run `daily` twice in a row. The second run reports only what changed since the first.

A job with `"status": "failed"` has the error in `stderr`. Report that error. Do not retry a paid job without asking.

## Read the output

- `keywords` and `pains` put the radar's JSON in `output`.
- `daily` and `sources` put the Markdown report in `output.report_markdown`. When `wrote_report` is false, `sources` found no new name. That is a result, not an error. For a `daily` job, `GET /jobs/<id>/report.json` returns the full data, with `leads` and `keywords.rows`.

A keyword table row has `phrase`, `kind` (`name` or `generic`), `names`, `new`, `per_day`, `started`, `momentum`, `now`, `decision`, `brand`, `source`, `trend_geo` and `competition`.

- `per_day` is an estimate, good to an order of magnitude.
- A row without `trend_geo` was read from a worldwide series whose last two days are discarded. For such a row, say "no rise seen", never "did not rise".
- `brand` true means the keyword carries another company's brand. Flag it. Whether to use it is the user's call.
- `source` "searched" means people really searched it together with the name. That is stronger than a phrase the model proposed.
- A row with `demoted` was rising, but page one is crowded, so it moved to watch.
- If `incomplete`, `posts_error`, `related_errors` or `country_errors` is set, or a signal's block holds `error`, a source failed. Name it. A thin table is not a quiet market.
- The first `daily` run in a new container only builds snapshots, so it reports GitHub breakouts and little else. Say so.

## Deliver

1. Today's new names, strongest first: what each is, where it was seen (X, Hugging Face, HN, Reddit, a feed such as Simon Willison, a YouTube channel or a builder's post such as @swyx, a GitHub breakout and its category, a sitemap) and how strongly, and one link.
   Give the GitHub breakout tables by category (general, ai, self-hosted) with each repo's stars a day and gain since yesterday, and a "What it is" column: one short line per repo, in the user's language, saying what the repo does. Write it from the repo's `description` in `report.json` (`breakout.repos` and `breakout.categories.<name>.repos`; the Markdown cuts it at 60 characters) and, for a repo with 📺, from what the channel said. Translate a description in another language. A repo with neither gets "no description", never a guess from its name. A 📺 after a repo links to the second a YouTube channel (GitHub Awesome) showed it; the report quotes what the channel said under the tables.
2. The priority table, sorted prioritize, then watch, then skip, with one short reason per row from the fields above.
3. The long-tails, the co-searched names and every caveat that applies.
4. Two or three lines of advice. Search volume data runs about two months behind.

Do not add names, rows, volumes or dates from your own knowledge. The output is what the radar saw.
