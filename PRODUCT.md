# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Vince, a developer who builds and runs AI coding agents for the PMS-Auto-Deposit project, and those agents themselves through the CLI, MCP server and JSON API. The dashboard is kept open on a second screen next to terminals and editors, opened on demand in a browser tab when something needs checking, and sometimes checked on a phone. (Confirmed 2026-10-27.)

## Product Purpose

tool-shed is a collection of small tools for AI coding agents, each defined once and exposed as a `shed <tool>` command and an MCP tool. `shed cron` runs tools on a schedule and records every result; the dashboard (`shed ui`, http://127.0.0.1:8765) is where a human reads those records: job status, run history, each tool's page and the tools catalogue. Success is that opening it answers "is anything failing, or is everything healthy" in one glance, with the stats behind that answer right there. (Confirmed.)

## Positioning

One tool definition serves the CLI, MCP, scheduled runs and the dashboard, and the dashboard shows recorded results instead of re-running tools, so an agent reading `shed runs <job> --json` and a human reading the page see the same record. (From the code and docs/DESIGN.md.)

## Operating Context

- Runs on one Mac, bound to localhost, no authentication, kept alive by a launchd agent; it is a viewer for one machine, not a service.
- The first job, `bdo-session`, runs `bdo-incidents` every 10 minutes against Alibaba SLS logs for the QAT and PROD environments and records events, incidents and totals. More jobs will follow (inferred).
- Pages refresh themselves every 30 s, every 3 s while a run is in progress; a run can be started from the page.
- The Mac's light/dark setting applies; the terminals around the dashboard are dark (inferred from the scene).

## Capabilities and Constraints

- Server is stdlib `http.server` with inline CSS and a few lines of script: no framework, no build step, no runtime dependency (project convention from docs/DESIGN.md §10; the user did not list it as untouchable, it stays as the engineering rule).
- Dense, scannable tables must stay. (Confirmed as the one thing that must not change.)
- Pages: Jobs (`/`), one page per job (`/jobs/<name>`) with the latest result, history and settings, one page per run, Tools (`/tools`), tool views (`/view/<job>`), and a JSON API (`/api/...`).
- A tool may ship `view.py` to get its own page; `bdo-incidents` does (QAT and PROD status, incident table, stitched timeline).
- Agents reach the same records over MCP through `shed mcp`: `jobs-status`, `job-runs`, `job-result` and `job-run`.
- Terminology: job, run, tool, view, QAT and PROD, incident, flag, relogin, outage, scheduler.

## Brand Commitments

- Name: tool-shed. The house glyph used as the favicon is the brand mark.
- Standing preference (confirmed 2026-10-27): the dashboard should sit alongside Linear, Vercel and Raycast. Their craft level is the bar, executed as the category standard, without irony or added quirk.

## Evidence on Hand

- Real run records under `~/.local/state/shed/runs/<job>/`; synthetic but shape-accurate fixtures in `tools/bdo-incidents/fixtures/`.
- No customers, prices, benchmarks or testimonials exist and none may be invented; the dashboard shows only recorded data.

## Product Principles

1. Health first: the first glance says failing or healthy, the detail follows.
2. Records over re-runs: show what was recorded, when, and how long it took.
3. Scannable density beats decoration.
4. A tool earns its own page by shipping a view; the generic result view must stay good enough for every other tool.
5. One surface that works on the second screen, in a tab, and on a phone.

## Accessibility & Inclusion

Keyboard operable with visible focus, 4.5:1 text contrast in both themes, reduced motion respected. No product-specific requirement beyond that was stated.
