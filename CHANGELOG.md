# Changelog

All notable changes to tool-shed. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the
version is the latest `v*` git tag (see "Versioning" in README.md). Each tool also carries its own `version`
in its `TOOL` definition; bump it here when its inputs or result shape change.

## [Unreleased]

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
| api-call, git-context, log-watch, sls-logs, sls-csv, bdo-incidents, lark-approval, lark-attachments, lark-doc, lark-files | 0.1.0 | first version |
| browser-fetch, product-search, social-search | 0.1.0 | first version |
