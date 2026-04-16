# GitHub User Monitor

A small CLI tool for collecting and storing a GitHub user's public activity over time.

It monitors:

- public user events
- current starred repositories

It stores state locally in SQLite so you can build your own historical dataset instead of relying on GitHub's short public activity windows.

## Features

- `snapshot` mode for one-off collection runs
- `watch` mode for continuous polling
- SQLite-backed storage
- conditional requests using `ETag` and `Last-Modified`
- `.env` support for `GH_TOKEN`
- `inspect` mode to review local state without calling GitHub
- JSON output option for automation

## Why this exists

GitHub exposes useful public activity data, but the public feeds are not a complete long-term archive. If you want durable history for an account, you need to poll periodically and store what you observe.

## Requirements

- Python 3.10+
- a GitHub token is recommended

## Setup

### Virtual environment

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

### Token

Create a fine-grained personal access token in GitHub and make it available as `GH_TOKEN`.

GitHub path:

1. **Settings**
2. **Developer settings**
3. **Personal access tokens**
4. **Fine-grained tokens**
5. **Generate new token**

Recommended setup for this tool:

- name: `gh-user-monitor`
- owner: your personal account
- expiration: set one
- repository access: **Public repositories** is enough for this script's current use case
- permissions: keep them minimal

Example `.env`:

```dotenv
GH_TOKEN=your_token_here
```

The script will load `.env` by default. You can also point it at a different file with `--env-file`.

## Usage

### Snapshot

Collect one pass of public data for a user:

```bash
python gh_user_monitor.py snapshot torvalds
```

Collect only events:

```bash
python gh_user_monitor.py snapshot torvalds --mode events
```

Collect only stars:

```bash
python gh_user_monitor.py snapshot torvalds --mode stars
```

Write state somewhere specific:

```bash
python gh_user_monitor.py snapshot torvalds --state-dir ./state/torvalds
```

Emit machine-readable output:

```bash
python gh_user_monitor.py snapshot torvalds --json
```

### Watch

Poll continuously every 15 minutes:

```bash
python gh_user_monitor.py watch torvalds --interval 900
```

Poll into a specific state directory:

```bash
python gh_user_monitor.py watch torvalds --interval 900 --state-dir ./state/torvalds
```

### Inspect

View recently collected local data without calling GitHub:

```bash
python gh_user_monitor.py inspect torvalds
```

Show more rows:

```bash
python gh_user_monitor.py inspect torvalds --limit 25
```

Emit JSON:

```bash
python gh_user_monitor.py inspect torvalds --json
```

## CLI reference

### Global options

- `--token`
  GitHub token passed directly on the command line

- `--token-env`
  Environment variable name to read the token from
  Default: `GH_TOKEN`

- `--env-file`
  Path to a `.env`-style file
  Default: `.env`

- `--state-dir`
  Directory used for local state
  Default: `./gh_monitor_state`

- `--db-name`
  SQLite filename inside `--state-dir`
  Default: `monitor.db`

- `--events-pages`
  Number of pages to request from `/users/\{username\}/events/public`
  Default: `3`

- `--stars-pages`
  Number of pages to request from `/users/\{username\}/starred`
  Default: `10`

- `--user-agent`
  HTTP `User-Agent` value
  Default: `gh-user-monitor/2.0`

- `--timeout`
  HTTP timeout in seconds
  Default: `30`

- `--json`
  Emit JSON summaries instead of text output

### Subcommands

#### `snapshot`

Run one collection pass.

```bash
python gh_user_monitor.py snapshot USERNAME [--mode events|stars|all] [--quiet]
```

#### `watch`

Run continuous polling.

```bash
python gh_user_monitor.py watch USERNAME [--mode events|stars|all] [--interval SECONDS] [--quiet]
```

#### `inspect`

Read local SQLite state without hitting GitHub.

```bash
python gh_user_monitor.py inspect USERNAME [--limit N]
```

## Storage layout

By default the tool writes a SQLite database here:

```text
./gh_monitor_state/monitor.db
```

The database contains:

- cached profile data
- normalized public events
- current starred repos
- historical star add/remove detections
- API cache metadata for conditional requests

## Notes and limitations

- this only sees **public** activity
- event history is limited by what GitHub still exposes when you poll
- if activity happens between polls and disappears before the next run, you can miss it
- star removals are detected by diffing snapshots over time
- this tool does not yet do deep repo-level enrichment for issues and PRs

## Typical workflow

1. create a `.env` with `GH_TOKEN`
2. run an initial `snapshot`
3. run `watch` on an interval for ongoing collection
4. use `inspect` or your own SQLite queries for analysis

## License

[GPLv3](./LICENSE)
