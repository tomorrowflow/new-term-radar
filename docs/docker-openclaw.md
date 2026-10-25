# Run the radar in Docker and use it from OpenClaw

The container runs the radar behind a small HTTP API (`radar/http_api.py`). The OpenClaw skill in `openclaw/skills/new-term-radar-remote` calls that API with `curl`. The API runs one job at a time, so a scheduled run and a run that OpenClaw asks for never write the same state at once.

## 1. Start the container

```bash
mkdir -p data                  # create it yourself, so that it belongs to you and not to root
cp .env.example .env
openssl rand -hex 24           # paste the result into RADAR_API_TOKEN in .env
$EDITOR .env                   # add AISA_API_KEY, and the optional keys if you have them
docker compose up -d --build
curl -fsS http://127.0.0.1:8787/health          # {"ok": true}
```

On the first start the container copies `config.example.json` to `data/config.json`. Edit that file to set your X accounts, sitemaps and topics, then run `docker compose restart`.

Everything that must persist is in `data/`: `config.json`, `state/radar.db`, the snapshots and `state/reports/`.

## 2. Choose the address OpenClaw uses

| Where OpenClaw runs | `RADAR_URL` | Change in `docker-compose.yml` |
|---|---|---|
| On this machine, not in Docker | `http://127.0.0.1:8787` | None |
| In Docker on this machine | `http://radar:8787` | Attach both containers to one network (below) |
| On a different machine | `http://<this-host>:8787` | Change the port line to `"8787:8787"`. Use a private network such as Tailscale or a firewall rule. The bearer token is the only protection, and the traffic is plain HTTP |

For OpenClaw in Docker, add the following to `docker-compose.yml`, and attach the OpenClaw container to the same network with `docker network connect radar-net <openclaw-container>`:

```yaml
services:
  radar:
    networks: [radar-net]
networks:
  radar-net:
    name: radar-net
```

## 3. Install the skill in OpenClaw

Copy the skill folder to a skill directory of your OpenClaw instance. The workspace directory has the highest precedence:

```bash
cp -r openclaw/skills/new-term-radar-remote ~/.openclaw/workspace/skills/
```

Then add its settings to `~/.openclaw/openclaw.json`. `apiKey` fills `RADAR_API_TOKEN`, because the skill names that variable as its `primaryEnv`:

```json5
{
  skills: {
    entries: {
      "new-term-radar-remote": {
        apiKey: "<the RADAR_API_TOKEN from .env>",
        env: { RADAR_URL: "http://127.0.0.1:8787" }
      }
    }
  }
}
```

Start a new OpenClaw session so that it loads the skill. The skill requires `curl` on the gateway host.

If your agents run in an OpenClaw sandbox, OpenClaw does not inject `env` and `apiKey` into the sandbox. In that case, set `RADAR_URL` and `RADAR_API_TOKEN` in the sandbox environment, make sure that `curl` exists in the sandbox image, and make sure that the sandbox can reach `RADAR_URL` on the network.

## 4. Try it

Ask OpenClaw "What has the radar kept this week?". This calls the free `/leads` endpoint. The list is empty until the radar has run at least once.

Then ask for a paid run, for example "Keyword research on PhotoCraft". The skill states the cost before it queues the job.

## 5. Optional: the schedule

The radar finds new names only by comparing today with an earlier run, so it is most useful when it runs on a schedule. Set `RADAR_SCHEDULE=on` in `.env` and run `docker compose up -d`. The container then runs `sources` at 00:00, 08:00 and 16:00 UTC, and `daily --skip x,hf` at 01:30 UTC. This is the same schedule as `.github/workflows/radar.yml`. With the example config, the cost is about $15 a month.

## 6. Optional: an RSS feed

The container serves an RSS 2.0 feed of the last 30 `daily` and `sources` reports, newest first. Each item title starts with the first names that the report found, and the item body is the full report as HTML. Most feed readers cannot send an `Authorization` header, so this endpoint also accepts the token in the URL:

```
http://127.0.0.1:8787/feed.xml?token=<RADAR_API_TOKEN>
```

Add this URL to your aggregator, with the host from step 2 if the aggregator runs somewhere else. The container's request log hides the token, but your aggregator stores the URL. Use this token only for the radar. The feed fills from scheduled runs (step 5) and from runs that OpenClaw starts. `sources` writes a report only when it finds a new name, so a quiet day adds no item.

## Troubleshooting

- `401` means that the token in OpenClaw is different from `RADAR_API_TOKEN` in `.env`.
- `docker compose logs radar` shows each request and the start-up messages.
- A failed job has the error in `stderr`. For example, `no AIsa key` means that `AISA_API_KEY` is missing from `.env`.
- `PermissionError` on `/data` means that Docker created `data/` as root. Run `sudo chown -R 1000 data`.
