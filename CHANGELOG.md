# Changelog

All notable changes to tool-shed. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the
version is the latest `v*` git tag (see "Versioning" in README.md). Each tool also carries its own `version`
in its `TOOL` definition; bump it here when its inputs or result shape change.

## [Unreleased]

## [0.4.0] - 2026-11-06

### Added
- Tunnels page: an unreachable row says why. It reports how long the Mac slept since the tunnel was last
  connected, detected from a wall-clock jump between two health checks, and shows cloudflared's latest
  error. A restart button makes a new quick tunnel in one click. A sleeping Mac drops the connection, and
  after a long sleep Cloudflare may refuse to take the quick tunnel back.

### Changed
- Dashboard: the shed version is a badge beside the "tool-shed" name instead of grey text at the far right,
  and the page's auto-refresh keeps it current after `shed start`.

## [0.3.0] - 2026-11-06

### Added
- `shed start`: starts the dashboard and scheduler, or restarts them so they run the code `shed update`
  installed. It waits for a running job to finish, waits until the new dashboard answers, prints its version,
  and names the tunnels the restart stopped. `shed update` now ends by pointing at it.
- The dashboard's `/health` also reports its process id and shed version.

### Fixed
- `shed cron install` failed now and then with "Bootstrap failed: 5: Input/output error" because launchd
  was still shutting down the old agent. It now waits for the old agent to exit and retries the bootstrap.

## [0.2.0] - 2026-11-06

### Added
- Dashboard `/tunnels` page: Cloudflare quick tunnels to local URLs, started and stopped from the UI, with
  live status and a one-click copy of the public address; installs `cloudflared` via Homebrew when missing
  (`shed update` does too). The status comes from cloudflared's own ready endpoint on a local metrics port: a
  tunnel reads `running` only while Cloudflare holds its connection and `unreachable` (red, with the time it
  dropped) when the connection is lost while the process is still alive.

### Changed
- `bdo-incidents` 0.2.0: each environment reports the session state (`state` up/down/unknown, `state_since`,
  `session_created`) from the last event that settles it: a saved login, a retry recovery or a page visit
  means up; an invalidation or a scraper stop means down; a failed relogin counts only while no session is
  alive. Every header line starts with it (`Down since …` with the duration, or `Logged in since …, session
  life …`), and events-only mode prints it after the events. The dashboard card is red only while the
  session is down (and says for how long), green with the session's life while it is logged in, and labels
  the window totals as totals.

### Fixed
- `bdo-incidents`: an incident whose retry recovery was followed by more invalidations or failed relogins
  was closed as "retry fixed it" with the outage cut at the retry (QAT showed 0m11s down and `ok` during a
  day-long outage). It now stays open with retry `Yes, then failed again`, and when a later retry does
  recover it the row keeps its failed attempts.
- `shed cron install` registers the global `shed`, never the repo's `.venv` copy that a shell inside the repo
  puts first on PATH; launchd cannot read a venv under Desktop or Documents, so that copy failed to start.

## [0.1.0] - 2026-11-04

### Added
- `research` group: `browser-fetch` (per-site browser sessions, one-time manual login), `product-search`
  (Lazada anonymous, Shopee with a saved login, price-history dashboard page) and `social-search` (YouTube,
  Threads, Bluesky, Hacker News, Google News, DuckDuckGo, TikTok trends; Facebook, Instagram, TikTok, X and
  Reddit with a saved login; watch mode by URL; cursor for "only what is new"). Skills `product-research` and
  `social-research`.
- `shed update`: one idempotent command for venv sync, global reinstall, Chromium, skills and MCP registration
  in Claude Code and Codex.
- Versioning: the version comes from git tags (hatchling + hatch-vcs); `shed --version`; every tool has a
  `version`; run records and the dashboard carry the shed and tool versions.
- `AGENTS.md` holds the repo's agent instructions; `CLAUDE.md` imports it.

### Changed
- Every tool group found on a machine is registered as its own user-scope MCP server (previously only `base`
  at user scope, the rest per project).
- Build backend is hatchling (was uv_build).

## Tool versions

| Tool | Version | Since |
|---|---|---|
| api-call, git-context, log-watch, sls-logs, sls-csv, lark-approval, lark-attachments, lark-doc, lark-files | 0.1.0 | first version |
| bdo-incidents | 0.2.0 | session state in the result |
| browser-fetch, product-search, social-search | 0.1.0 | first version |
