# github-spy

**Archive GitHub users' ephemeral public activity into durable, queryable local history.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

<p align="center">
  <img src="assets/demo.gif" alt="github-spy demo: snapshot, stats, inspect, and diff" width="800">
</p>

---

GitHub's public data is ephemeral. Events disappear after 90 days. Stars, followers, and repos are mutable with no built-in changelog. If someone unstars a repo, unfollows you, or deletes a project, that information is gone.

**github-spy** polls GitHub's REST API on a schedule and stores everything locally in SQLite, creating a persistent, queryable archive of what changed and when.

## What it tracks

| Data | What's detected |
|------|----------------|
| **Events** | Push, PR, issue, star, fork, and all other public event types — each row gets a derived `url` (compare URL for pushes, PR URL, issue URL, etc.) and a `details` one-liner (e.g. `"pushed 3 commits to main — fix flaky test"`) so the archive is actionable, not just a dump |
| **Stars** | Which repos a user has starred, plus star/unstar changes over time |
| **Followers** | Who follows the user, plus follow/unfollow changes |
| **Following** | Who the user follows, plus follow/unfollow changes |
| **Repos** | Public repositories owned by the user, plus creation/deletion |
| **Profile** | Field-level change tracking (bio, company, location, follower counts, etc.) |

## Install

```bash
uv tool install github-spy
# or
pipx install github-spy
```

Or clone and run directly:

```bash
git clone https://github.com/ajmeese7/github-spy.git
cd github-spy
uv sync
uv run github-spy --help
```

## Quick start

```bash
# Set your token (optional but recommended for higher rate limits)
export GH_TOKEN=ghp_...

# Take a snapshot of a user's public activity
github-spy snapshot ajmeese7

# Monitor multiple users continuously (every 15 minutes)
github-spy watch ajmeese7 torvalds --interval 900

# View what you've collected
github-spy stats ajmeese7
```

## Example output

### Snapshot

```
╭──────────────────────── ajmeese7 (Aaron Meese) ──────────────────────────────╮
│ 2026-04-17T14:21:31Z                                                         │
│   111 repos / 217 followers / 146 following                                  │
│   profile:                                                                   │
│   events: +198 new                                                           │
│     + 2026-04-17T02:28:47Z PushEvent meese-enterprises/uptime-monitor        │
│     + 2026-04-17T02:01:08Z PushEvent meese-enterprises/uptime-monitor        │
│     + 2026-04-16T23:33:59Z PushEvent meese-enterprises/uptime-monitor        │
│     + 2026-04-16T21:28:54Z WatchEvent Hona/temple-oc                         │
│     + 2026-04-16T15:02:14Z WatchEvent zonelessdev/zoneless                   │
│     ... and 193 more                                                         │
│   stars: 1000 total 1000 changes                                             │
│   followers: 217 total 217 changes                                           │
│   following: 146 total 146 changes                                           │
│   repos: 111 total 111 changes                                               │
│   API quota: 4985/5000 remaining                                             │
╰──────────────────────────────────────────────────────────────────────────────╯
```

### Stats

```
Stats for ajmeese7

Event Types
  PushEvent          ████████████████████████████████████████ 95
  WatchEvent         ███████████████ 36
  IssuesEvent        ████████████ 30
  IssueCommentEvent  ██████ 15
  PullRequestEvent   █████ 14
  CreateEvent        ██ 6
  ForkEvent           2

Starred Repos by Language
  Python      ████████████████████████████████████████ 290
  TypeScript  ██████████████████ 137
  Go          █████████ 70
  JavaScript  █████████ 67
  C           ███████ 55
  Shell       ██████ 47
  Rust        ██████ 45
  C++         ██████ 44
  HTML        ████ 36
  Java        ██ 19

              Most Active Repos
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Repository                       ┃ Events ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ meese-enterprises/uptime-monitor │    120 │
│ ajmeese7/hermes-agent            │      8 │
│ ajmeese7/fog-libvirt             │      7 │
│ LetsFG/LetsFG                    │      6 │
│ ajmeese7/summarize               │      6 │
│ neo4j-labs/neo4rs                │      5 │
│ NousResearch/hermes-agent        │      4 │
└──────────────────────────────────┴────────┘

Daily Activity (last 30 days)
  2026-03-30 ▃▆▆▅▂▁▁▂▄▁▃▅█▁▁▁▄▆  2026-04-17
  198 events total, peak 24/day
```

### Users

```
                  Tracked Users
┏━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┓
┃ Username ┃ Events ┃ Stars ┃ Followers ┃ Repos ┃
┡━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━╇━━━━━━━┩
│ ajmeese7 │    198 │  1000 │       217 │   111 │
└──────────┴────────┴───────┴───────────┴───────┘
```

## Commands

| Command | Description |
|---------|-------------|
| `snapshot <users...>` | One-time collection of public activity |
| `watch <users...>` | Continuous polling on an interval |
| `inspect <user>` | View locally stored data (no API calls) |
| `show <event_id>` | Print a single event's full detail (URL, summary, raw payload) |
| `stats <user>` | Terminal-rendered analytics and charts |
| `export <user>` | Export data as CSV, JSON, or JSONL |
| `diff <user>` | Show changes between two points in time (includes events) |
| `users` | List all tracked users in the database |

All commands support `--json` for machine-readable output.

```bash
# Snapshot only specific data types
github-spy snapshot ajmeese7 --collect events,stars

# Export events as CSV for spreadsheet analysis — includes url, details, payload_json
github-spy export ajmeese7 --type events --format csv --output events.csv

# See what changed since last week (stars, followers, repos, profile, AND events)
github-spy diff ajmeese7 --since 2026-04-10

# Inspect recent follower changes
github-spy inspect ajmeese7 --type followers --limit 50

# Filter events by type, repo, or date range
github-spy inspect ajmeese7 --type events --event-type PullRequestEvent,IssuesEvent
github-spy inspect ajmeese7 --type events --repo meese-enterprises/uptime-monitor
github-spy inspect ajmeese7 --type events --since 2026-04-10 --until 2026-04-17

# Drill into a single event — renders URL, summary, and the full payload JSON
github-spy show 52345678901
```

Run `github-spy <command> --help` for full options.

## Use cases

- **Track star/unstar patterns** as a signal for repo interest or hiring activity
- **Monitor open source maintainers** to understand contribution patterns
- **Build a personal activity archive** before GitHub's 90-day event window expires
- **Detect bulk follow/unfollow** activity (bot detection, growth tracking)
- **Track when repos appear or disappear** from a user's profile
- **Feed data into external tools** via CSV/JSON export for custom analysis

## Storage

Data lives in a single SQLite database at `~/.local/share/github-spy/github-spy.db` by default. Override with `--db /path/to/file.db`.

## Poking at the SQLite directly

The `export` and `inspect` commands cover the common paths, but the DB is the authoritative artifact. Everything is plain SQLite. Open it in [TablePlus](https://tableplus.com/), [DB Browser for SQLite](https://sqlitebrowser.org/), [DBeaver](https://dbeaver.io/), or [sqlite-utils](https://sqlite-utils.datasette.io/), or just shell out:

```bash
sqlite3 ~/.local/share/github-spy/github-spy.db
```

### Table map

| Table | What it holds |
|---|---|
| `events` | Every ingested public event with `url`, `details`, and the full `payload_json` |
| `stars_current` / `star_history` | Current starred repos / add + remove history |
| `followers_current` / `follower_history` | Current followers / follow + unfollow history |
| `following_current` / `following_history` | Current followees / follow + unfollow history |
| `repos_current` / `repo_history` | Current public repos / create + delete history |
| `profiles` / `profile_history` | Latest profile JSON / field-level change log |
| `api_cache` | ETag / Last-Modified caches (only used for events now) |
| `metadata` | Schema and migration bookkeeping |

### Useful queries

```sql
-- Most recent PR activity with URLs you can paste into a browser
SELECT created_at, action, repo_name, details, url
FROM events
WHERE username = 'ajmeese7'
  AND event_type = 'PullRequestEvent'
ORDER BY created_at DESC
LIMIT 20;

-- Breakdown of event volume by type
SELECT event_type, COUNT(*) AS n
FROM events
WHERE username = 'ajmeese7'
GROUP BY event_type
ORDER BY n DESC;

-- Stars gained / lost in the last 30 days
SELECT event_kind, full_name, detected_at
FROM star_history
WHERE username = 'ajmeese7'
  AND detected_at > DATE('now', '-30 days')
ORDER BY detected_at DESC;

-- Follower churn summary
SELECT event_kind, COUNT(*) AS n
FROM follower_history
WHERE username = 'ajmeese7'
GROUP BY event_kind;

-- Profile field changes over time
SELECT field_name, old_value, new_value, detected_at
FROM profile_history
WHERE username = 'ajmeese7'
ORDER BY detected_at DESC;

-- Daily event velocity for the last 30 days
SELECT DATE(created_at) AS day, COUNT(*) AS n
FROM events
WHERE username = 'ajmeese7'
  AND created_at > DATE('now', '-30 days')
GROUP BY day
ORDER BY day DESC;

-- Commit messages extracted from the raw payload
SELECT
  created_at,
  repo_name,
  json_extract(payload_json, '$.payload.commits[0].message') AS first_commit_message
FROM events
WHERE username = 'ajmeese7'
  AND event_type = 'PushEvent'
ORDER BY created_at DESC
LIMIT 10;
```

## Token setup

A GitHub personal access token is optional but recommended. Without one, you're limited to 60 API requests/hour. With a token, you get 5,000/hour.

Create a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new) with **Public Repositories (read-only)** access. No other permissions needed.

```bash
# Option 1: Environment variable
export GH_TOKEN=ghp_...

# Option 2: .env file
echo "GH_TOKEN=ghp_..." > .env

# Option 3: Direct flag (not recommended for scripts)
github-spy snapshot torvalds --token ghp_...
```

## Rate limiting

github-spy automatically tracks your API quota and:
- Warns when requests are running low
- Sleeps with a progress bar when rate limited
- Extends polling intervals in watch mode when quota is low
- Retries transient server errors with exponential backoff
- Uses HTTP conditional requests (ETag/Last-Modified) on the events feed to skip unchanged pages. Full-state endpoints (stars, followers, following, repos) deliberately bypass the cache: a partial 304 would leave the collector with an incomplete list and fabricate phantom unfollow / delete events. The full sweep for a typical user is ~30 requests against a 5,000/hour quota, so the tradeoff is trivial.

## License

[GPLv3](./LICENSE)
