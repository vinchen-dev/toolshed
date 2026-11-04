# Design

The decisions behind tool-shed and why they were made, so later changes can tell deliberate choices from accidents.

## Goal

One place for small tools that AI coding agents use during development, and for research tools an agent runs on Vince's behalf (marketplace prices, what people post about a topic). An agent must be able to discover a tool, learn its inputs, run it and parse the result without a human in the loop.

## 1. Define once, expose as CLI and MCP

Each tool is a single `Tool(...)`: name, description, pydantic input model, `run`, optional `render` and `examples`. The CLI derives flags from the model; the MCP server derives a JSON Schema from it. Two surfaces, one definition, so they cannot drift.

- CLI first, because Claude Code's Bash tool, shell scripts, cron and CI can all use it with zero configuration.
- MCP second, for schema-aware tool calls when the agent supports it.

Not every project should see every tool: the BDO checks belong to the auto-deposit project, the Lark and SLS tools to the PMS repos, `api-call` anywhere. The grouping is the folder tree (section 3): `shed mcp --group PMS` serves the tools directly inside `tools/PMS/`, `--group PMS/autorunner` the ones below it, and the reserved `jobs` group adds the scheduler's tools, which live in `src/shed/job_tools.py` rather than under `tools/`. `shed update` registers every group present on the machine as its own user-scope MCP server (Vince chose user scope for all of them on 2026-11-04: which groups a machine has is already decided by which folders it keeps, so a second per-project layer was one more thing to maintain). A per-project local-scope entry still works for a repo that wants a narrower set. Rejected: a `group` attribute on `Tool` (would force every tool to guess where it is wanted), a groups file in `~/.config/shed` (a second place to keep in step with the folders) and one `.mcp.json` per repo listing tool names (the same list would live in several repos and drift).

Rejected: Claude Code skills as the primary format (prose, not executable, single-agent); a local HTTP service as a way to *call* tools (needs a running process and brings nothing extra for local dev). The dashboard in section 10 serves recorded results, not tool calls.

## 2. Python 3.12 with uv

- The first real tool (SLS log search) and the likely next ones are Python, and the AI and data ecosystem the shed will lean on is strongest there.
- uv gives one lockfile, one `.venv`, `uv add` for dependencies and `uv tool install --editable .` for a global `shed` that tracks the working tree.
- pydantic is the single schema language: validation, defaults, descriptions, JSON Schema for MCP and the field metadata the CLI turns into flags.
- TypeScript on Node was the first build and was replaced on day two before any tool existed. It would have matched the Express and Next.js work, but every tool would have been written against the weaker ecosystem for this job.

## 3. One folder per tool, discovered by convention

`tools/<name>/tool.py` assigns `TOOL` and the registry walks the `tools/` tree. There is no central list to maintain. The folder name must equal the tool name (enforced on load), and folders starting with `_` are ignored, which is how `_template` is kept out of the catalogue. Each folder is imported as the package `shed_tools.<name>`, so a tool can split into modules and use relative imports. Helpers, fixtures and wrapped scripts live inside the tool's folder.

A folder without a `tool.py` is a group: the registry searches it further and remembers it as the tool's group (`base`, `PMS`, `PMS/autorunner`). Groups exist so a project can be given only the tools that concern it (section 1), and so related tools and skills sit together. Tool names stay unique across the whole tree, because the CLI, MCP and cron address a tool by name alone; the group never becomes part of the name. A tool's own subfolders are not searched, so fixtures and helpers cannot be mistaken for tools, and a tool cannot nest inside another.

The repo is cloned on every machine Vince works on, but only `base` is wanted everywhere; the work groups belong on the work machine. So `base` is the one group the host and its tests rely on, and every other group is a self-contained, per-device option: `tools/<group>` and `skills/<group>` go together, nothing outside them imports them, and deleting both keeps `pytest` green. Rejected: a per-device "disabled groups" file (one more thing to keep in step; leaving a group unregistered already makes it inert, and deleting the folder is one command).

## 4. Inputs are named flags derived from the pydantic model

Allowed field types: str, int, float, bool, Literal of str, str Enum, and lists of those, each optionally `| None` or with a default. There are no positional arguments: named flags are self-documenting, agents produce them more reliably, and they map one-to-one onto MCP arguments. Both `--max-size` and `--max_size` are accepted so an agent that copies the schema's field names still works. Validation failures exit with code 2 and list the offending flags.

## 5. Output contract

stdout carries only the result; stderr carries logs and errors. `--json` prints compact JSON to save tokens; otherwise the tool's `render()` or pretty JSON is used. Exit codes are 0 (ok), 1 (tool failed) and 2 (bad usage). Tools return data rather than printing, which is what lets the MCP server reuse them unchanged. Results are serialised with pydantic, so models, dataclasses, paths and datetimes all work.

## 6. A deliberately small shared library

`sh()` (subprocess without a shell), the `ShedError` hierarchy, result rendering, the `~/.config/*.env` credential loader and the Manila time parser and formatter. A helper moves into `src/shed/lib/` only once a second tool needs it: the last two moved in when `lark-approval` and `bdo-incidents` needed what `sls-logs` already had.

## 7. Dependencies live in the project, imported lazily

Tools share the project's `.venv`; `uv add` declares what a tool needs. Heavy SDKs are imported inside `run()` behind a small seam function (`make_client`) so `shed list` and `shed mcp` start fast and tests can replace the client. A standalone script with its own dependencies can still be a tool: wrap it with `sh("uv", ["run", script, ...])`.

## 8. Tests next to tools

Each tool has `test_tool.py` calling `run()` directly with a stub context and loading the tool through the registry. The host has its own tests for the flag parser, the CLI (spawned as a subprocess) and the MCP server (driven by the MCP client over stdio), so both surfaces are covered end to end. `pytest`, `pyright` and `ruff` are the gate.

## 9. Skills ride along, as prose only

A skill is the judgment around a tool: when to run it, which flags to use, how to read the output and how to report. That is Claude Code specific prose, not something an agent can execute, so section 1 still stands and a skill is never the primary format. It belongs next to the tool it describes, though, so the two change together. `skills/<group>/<name>/SKILL.md` holds Markdown only, in the same group folder as the tool it describes; any code the skill needs is a tool it calls by name (`bdo-incident-report` calls `shed bdo-incidents`).

The repo's instructions for agents live in `AGENTS.md`, the file Codex and the other agent CLIs read; `CLAUDE.md` holds one line, `@AGENTS.md`, so Claude Code reads the same copy and nothing is maintained twice (Vince's call, 2026-11-04, once Codex joined Claude Code as a consumer). `shed skills link` symlinks each skill folder into `~/.claude/skills`; `--group <folder>` links only that folder's skills, into a project's own `.claude/skills` if wanted. That is the same editable idea as `uv tool install --editable .`: edits in the repo are live, nothing is copied, and a real folder already at the target is reported and left alone. A link left dangling by a skill that moved folders is replaced.

Rejected: copying skill files (they drift from the repo); a skills-only design with the logic in the Markdown (the first BDO incident report kept miscounting until the grouping moved into code, which is now `tools/bdo-incidents/incidents.py` with tests).

## 10. Scheduled runs are recorded, and viewed in a local dashboard

Some tools are checks that want running every few minutes (the BDO incident check ran 467 times by hand through a Claude loop in one session, with every result living only in the chat). `shed cron` runs jobs declared in `~/.config/shed/cron.toml` and writes each run as a JSON record (rendered text, result data, log, error) under `~/.local/state/shed/runs/`. An agent then reads `shed runs <job> --json`, or the `jobs-status` and `job-result` MCP tools, instead of re-running the tool, and a human reads the table in the dashboard.

- Scheduling is a due-check, not a daemon: `run_due` runs a job when its interval has passed since the last recorded start, with half a minute of slack for the ticker. The ticker is `shed ui --cron`, kept alive by a launchd agent (`shed cron install`), and `shed cron run` does the same tick by hand. A file lock keeps two tickers from overlapping.
- The dashboard is stdlib `http.server` with inline CSS, one self-hosted font file (`src/shed/static/inter.woff2`, Inter latin subset, OFL) and a short script that refreshes the page body, ticks the countdowns and switches the theme: no framework, no build step, no dependency, nothing fetched from the network. Its visual system is recorded in the root `DESIGN.md` (written by Impeccable); this file stays the engineering record. It binds to localhost and has no auth, so it is a viewer for one machine, not a service.
- The generic result view (facts and tables from the JSON) is enough for most tools. A tool that wants a real page ships `view.py` beside `tool.py` with `TITLE` and `view(job, runs, now, query)`, and the dashboard serves it at `/view/<job>` with every kept run of that job, so the page can stitch slices into a continuous timeline. The view imports the tool's own models and the dashboard's HTML helpers; the dashboard never imports a tool at module level, so a broken view only breaks its own page. `bdo-incidents` has one.
- Rejected: a static HTML report rewritten after each run (Vince views results often enough to want run-now and history pages); a scheduler library or a daemon with its own clock (launchd already restarts a crashed process, and a due-check survives sleep and reboots without state).

## 11. Research tools: anonymous by default, your own browser login by choice, never on cron

`product-search`, `social-search` and `browser-fetch` form the optional `research` group. Vince set three constraints in one afternoon (2026-11-04): free routes only, then no accounts or payment of any kind, then anonymous routes plus his own one-time browser logins. Every route was tested logged-out that day: Lazada, Threads, Bluesky, Hacker News, Google News, DuckDuckGo (in a real browser), the TikTok Creative Center trends page and public Facebook Pages answer; Shopee, Reddit, TikTok search, X, Facebook search and Instagram block or redirect. yt-dlp covers YouTube without a key.

- One transport seam per tool: a module-level object with the live routes (JSON, text, yt-dlp, a page opened through browser-fetch). Tests swap it for a fake serving recorded fixtures, so no test launches a browser and the gate stays offline. browser-fetch has the same shape one level down (`driver`), so profile handling, wall detection and the saved-page-on-failure path are tested without Chromium.
- Browser extractors are JavaScript files beside the tool, run in the page by `page.evaluate`, and hand back plain records; Python only normalizes. Sites change markup a few times a year; an extractor that throws or returns nothing saves the page and names it in the error, and the fix is a selector in one small file. Helpers two research tools share (count parsing, extractor loading, the site registry) live in browser-fetch and are reached through the registry, the way the Lark tools share one client, because `src/shed/lib/` must stay usable with the group deleted.
- Logged-in platforms are opt-in per run (`--use-login`) and skipped with a note otherwise, so a job declared in cron cannot reach a personal account unless its config says so, and the README and skills say not to. A session lives in a persistent per-site Chromium profile under the config folder (`SHED_BROWSER_DIR` overrides it, like `SHED_STATE_DIR`) with owner-only permissions; the tool never stores or types a password, never attempts a captcha, and treats a bot-check page as an error naming the login command.
- Per-source results (`ok`, `note`, items) instead of one failure: research runs span many sites and one blocked site must not hide the rest. `--strict` restores fail-fast for scheduled checks that should not degrade silently.
- Rejected: Bing as a free search backend (its RSS and result pages return unrelated pages to an automated visitor), paid scrapers and search APIs, developer accounts (YouTube key, Threads or Instagram apps, Shopee affiliate), Reddit's API (closed to new apps since 2026-10-31), and any automation-hiding browser flags (the permission layer treats them as bot-check circumvention, and the design does not want them).

## 12. `shed update` is the one setup command

After a pull or a `uv add`, a machine needs the venv synced, the global `shed` reinstalled (an editable tool install tracks code, not dependencies), Chromium present for the research group, skills linked and MCP servers registered. Five commands that are easy to half-remember became `shed update`: idempotent, in the host, with a `--dry-run` that prints the plan. It shells out through `sh()` for uv, Playwright and the agent CLIs it finds (`claude mcp add --scope user`, `codex mcp add`; each is skipped with a message when absent), links skills in-process, and reports a failed registration without hiding the rest. Agents without a registration still have the shell route, which is why the CLI is the primary surface (section 1). Rejected: a shell script (untested, and a second place to learn the group conventions) and a Makefile (one more dependency).

## 13. Versions come from git tags; tools and run records carry their own

Vince asked for versioning on 2026-11-04 and chose all four layers: a project version, git tags as its source, a version per tool, and run records stamped with both. The project version is the latest `v*` tag read by hatch-vcs at install time and by `git describe` at run time in a checkout, so a dirty working tree or commits past the tag show up (`0.2.0.post3+gabc1234.dirty`) instead of lying. The build backend moved from uv_build to hatchling for that one feature. A tool's `version` is a plain string on its definition, bumped by hand when inputs or the result shape change, because that is what an agent reading `shed list --json` needs to know and no tool can compute it. Run records store the shed and tool versions that produced them so the dashboard can tell results from before a change apart. `CHANGELOG.md` is kept by hand, Keep a Changelog style; releases are tags. Rejected: a version field in `pyproject.toml` bumped by hand (drifts from tags), and per-tool changelog files (one file is enough at this size).

## Non-goals, for now

- No plugin system or remote registry: the repo is cloned and installed as an editable uv tool.
- No build or publish step beyond that.
