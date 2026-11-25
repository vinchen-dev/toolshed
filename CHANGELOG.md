# Changelog

All notable changes to tool-shed. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the
version is the latest `v*` git tag (see "Versioning" in README.md). Each tool also carries its own `version`
in its `TOOL` definition; bump it here when its inputs or result shape change.

## [Unreleased]

## [0.9.0] - 2026-11-25

### Added
- `agent-watch` 0.2.0: each session in the result carries `started`, the time its process began, or its first
  record once no process holds it.
- Dashboard: an open page shows a newly recorded run within about three seconds instead of waiting for its
  30-second refresh. Each page carries the stamp of the runs it was built from and polls `/api/stamp`.

### Changed
- Agents page: a session shows its folder, what runs it and when it started, and every subagent and command
  shows its start time in a new Started column. The Agent column is gone; its content moved under the name.
- Agents page: each session is a block with the subagents, commands and sessions it started listed under it, and
  a block folds. The separate "Needs attention" table is gone: sessions that need attention, or have something
  under them that does, come first and carry a marker, and every row shows how long it has held its state.

### Fixed
- Scheduled `agent-watch` snapshots had no listening-port information: the dashboard's launchd agent runs
  without `/usr/sbin` on its PATH, so `lsof` was not found and a command that only serves a port could not read
  `serving` on the Agents page. The tool now finds `lsof` where the system keeps it.
- `agent-watch` called a subagent "unreadable" when a snapshot ran in the instant it was started. It is now
  left out of that snapshot without a note and appears in the next one.

## [0.8.0] - 2026-11-25

### Added
- `sls-login` (SN): sign in to Alibaba Cloud SLS through the company single sign-on. It opens a browser window
  where you sign in, exchanges the SAML response for temporary credentials (STS AssumeRoleWithSAML) and saves
  them, owner-only, in `~/.config/aliyun-sls-session.json`. Needs `SLS_LOGIN_URL` in `~/.config/aliyun-sls.env`
  or `--url`. Never scheduled: it needs a person at the screen. The sign-in's cookies are kept, owner-only, in
  `~/.config/shed/browser/sls-login/state.json`, so the single sign-on stays signed in between runs.

### Changed
- `sls-logs`, and with it `error-trace`, `deployed-version` and `bdo-incidents`, use the `sls-login` session
  instead of an AccessKey pair, because Alibaba Cloud access moved to single sign-on and the key no longer
  exists. When the session runs out (the role allows 60 minutes) the tool renews it by itself with a windowless
  browser, for as long as the single sign-on keeps you signed in; a failed renewal is not tried again for ten
  minutes. Without a session, or when it cannot be renewed, they stop with a message that says to run
  `shed sls-login`. The AccessKey route is kept and switched off; `SLS_AUTH=key` in `~/.config/aliyun-sls.env`
  turns it back on. Inputs and results are unchanged.
- Tunnels stay on until you stop them. cloudflared now runs detached from the dashboard, so `shed start` and
  `shed update` leave a tunnel running with the same public URL and the new dashboard picks it up; before, every
  restart stopped it. A tunnel that was on is started again, with a new URL, when the dashboard starts and its
  process is gone, as after a reboot. `shed start` names the tunnels that kept running, and `shed cron
  uninstall` stops them.

### Fixed
- `shed update` also installs Chromium for the global `shed`, through that install's own Playwright. Before, only
  the project venv got it, so a global `shed` that had resolved a newer Playwright could not open a browser
  ("Executable doesn't exist"), which stopped `sls-login` and the research tools when run as `shed`.

## [0.7.0] - 2026-11-25

### Added
- `agent-watch` (core): one snapshot of every Claude Code and Codex session on the machine with its subagents and
  background commands, the model each runs on, and a state checked against evidence (transcript writes, output
  files, CPU, listening ports), so a session that says it is busy but has stalled is flagged, one waiting on you
  reads `needs-input`, and one whose process died mid-turn reads `gone`. Sessions that ended stay listed for a
  day. Ships a dashboard page, Agents, for jobs that run it. Read-only. `--tail` adds the last lines of a
  background command's output to the result and the text output, with lines that set or send a credential
  withheld.
- `agent-status` skill (core): when to run `agent-watch` and how to report stalled and waiting sessions.

### Changed
- The Claude Code transcript reader moved from `session-stats` into `src/shed/lib/transcripts.py`, so that a `core`
  tool can use it. `session-stats` and `transcript-digest` take the same inputs and return the same results. The
  reader can now read only the tail of a transcript, and also reports turn ends, each reply's stop reason and
  model, background task ids, task notifications and the session title.

## [0.6.0] - 2026-11-11

### Added
- `error-trace` (SN): one error text or trace id traced through SLS for PMS, finance-ai or the autorunner: count,
  first and last seen, count per hour and per container, distinct messages with ids masked, the source files and
  lines named in stack traces, and optionally every line around the latest occurrence (`--around`). Read-only;
  lines that mention credentials are cut or withheld.
- `sn-investigate` skill (SN): the fixed order for investigating an error in those three projects (vault, deployed
  version, logs, code through graphify when the repo has a graph, data, recent commits) and how to report it.

## [0.5.0] - 2026-11-11

### Added
- `lark-bitable` (SN/lark): read records from a Lark Base table by link or token with a view, a filter formula and a
  field list, list its tables, or add/update one record behind the explicit `--create` / `--update` flags.
- `lark-app-access` (SN/lark): report the Lark app's token, name and status plus one read probe per capability
  (approval, docx, Drive, wiki, Base), each classified as ok, scope-missing or error.
- `mongo-query` (core/database): read-only find/count/aggregate on one collection, or list collections, with
  Extended JSON in and out, from a `~/.config/mongo.toml` profile or a repo's `.env` (`--env-file`, `--url-var`).
- `mongo-index-diff` (core/database): per-collection index comparison between two databases, or against a JSON
  snapshot (`--snapshot-out` / `--snapshot-in`).
- `redis-get` (core/database): type-aware read of one key with its TTL, or a key scan with types and TTLs, from a
  `~/.config/redis.toml` profile or a repo's `.env`; never writes.
- `session-stats` (agent): token and tool usage per Claude Code session from `~/.claude/projects`, with `--since`,
  `--session` and `--all-projects` filters.
- `transcript-digest` (agent): one session condensed into prompts, replies, tools, files touched, commands and
  errors, with Manila times, as input for note-taking.
- `graph-query` (agent): graphify query/explain/path/affected and the graph summary for the nearest `graphify-out/`.
- `vault-recall` (agent): Obsidian hub-based recall for a project: rank the hub's note hooks by keywords and open
  one note, following the vault's protocol.
- `LarkClient` gained `post` and `put` for the Base writes (no tool contract change).
- `meegle-search` (SN): search Meegle work items in a space or across spaces by name, id, type, state, person,
  dates, priority or tag, or list spaces and work item types. Read-only, as the plug-in user (`x-auth-mode: 1`).
- `meegle-item` (SN): one Meegle work item with its fields, state history, nodes, owner, description, comments and
  optional change log, user_keys resolved to names. Read-only.
- `deployed-version` (SN): the PMS release running on QAT and PROD from the start-up lines in SLS, with Node
  version, container count, a mixed-version warning and a comparison with the local checkout.
- `branch-diff` (core): commits, changed files with line counts, ahead/behind and authors between two refs, for PR
  descriptions and release notes.
- `version-stamp` (core): bumps package.json/package-lock.json, a static pyproject.toml version or the latest `v*`
  tag, rolls CHANGELOG.md's Unreleased section into a dated release, optional git tag, never commits.
- `npm-deps-check` (core): outdated packages and audit findings by severity through npm or pnpm, read-only.

### Changed
- `mongo-query`, `mongo-index-diff` and `redis-get` reach their drivers only through read-only views
  (`tools/core/database/mongo-query/readonly.py`): any operation outside the read allow-list, and any `$out` or
  `$merge` stage, fails before a command is sent, so the tools cannot write even by mistake.
- Dashboard: the Tools page is sectioned by group folder, each heading naming the group's MCP server, its
  `shed mcp --group` command and its tool count (core first, then the other groups alphabetically).
- Tool groups are themed (DESIGN section 14): `base` is now `core` (its MCP server is still `tool-shed`) and holds
  `core/database`; `PMS` is now `SN` (`tool-shed-sn`), with the four Lark tools under `SN/lark` (`tool-shed-lark`);
  `agent` is a new optional group (`tool-shed-agent`). Tool names and `shed <tool>` are unchanged. After pulling, run
  `shed update` and remove the stale registration once: `claude mcp remove --scope user tool-shed-pms` (and
  `codex mcp remove tool-shed-pms`).

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
| lark-bitable, lark-app-access, mongo-query, mongo-index-diff, redis-get, session-stats, transcript-digest, graph-query, vault-recall, deployed-version, branch-diff, version-stamp, npm-deps-check, meegle-search, meegle-item | 0.1.0 | first version |
| error-trace | 0.1.0 | first version |
| agent-watch | 0.2.0 | `started` on each session (0.1.0 was the first version) |
| sls-login | 0.1.0 | first version |
