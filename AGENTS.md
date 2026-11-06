# tool-shed

Collection of CLI + MCP tools for AI coding agents. Python 3.12, uv, pydantic, src layout.
Design decisions and their reasons: `docs/DESIGN.md`. Usage and conventions: `README.md`.

## Commands

- `uv run pytest` tests, `uv run pyright` types, `uv run ruff check . && uv run ruff format .` lint/format. All three must pass before staging.
- `uv run shed <args>` runs the CLI from this folder (`shed` once installed with `uv tool install --editable .`).
- `uv run shed new [<group>/]<kebab-name>` scaffolds `tools/<group>/<name>/` from `tools/_template/`. Group folders (`base`, `PMS`, `PMS/autorunner`, `research`) are what `shed mcp --group` serves; a tool name must be unique across the tree.
- `uv add <package>` adds a dependency; commit `uv.lock`.
- `uv run shed skills link [--group <folder>]` symlinks every skill (or one folder's) into `~/.claude/skills`; `uv run shed skills` lists them. Skills sit in the same group folders as their tools.
- `shed start` (re)starts the launchd agent that runs the dashboard and scheduler; run it after every `shed update`, because the running dashboard keeps the code it started with.
- `shed cron` / `shed runs <job> --json` / `shed ui`: scheduled jobs from `~/.config/shed/cron.toml`, their recorded results, and the localhost dashboard. Set `SHED_CRON_FILE` and `SHED_STATE_DIR` to keep tests away from the real files (`SHED_BROWSER_DIR` does the same for the research group's browser profiles). Over MCP the same records are the `jobs-status`, `job-runs`, `job-result` and `job-run` tools (`src/shed/job_tools.py`).
- `shed mcp --group <folder>` serves only the tools directly inside that `tools/` folder (repeatable; `jobs` is the reserved name for the job tools); `shed mcp groups` prints them. `shed update` registers every group present on the machine (plus `jobs`) in Claude Code at user scope and in Codex, after reinstalling the global `shed`, installing Chromium and linking skills; rerun it after a pull or `uv add`.

## Adding or changing a tool

1. Scaffold with `shed new <group>/<name>`, then edit `tools/<group>/<name>/tool.py`.
2. `TOOL = Tool(...)` with `name` equal to the folder name and a description that says what it does and when to use it.
3. Every `Input` field gets a `description`. Allowed types: str, int, float, bool, Literal of str, str Enum, lists of those, optionally `| None`.
4. `run(params, ctx)` returns data (pydantic models are ideal). It never prints; progress goes through `ctx.log()`.
5. Add or update `test_tool.py` beside it, loading the tool with `shed.registry.load_tool`.
6. Optional: `view.py` with `TITLE` and `view(job, runs, now, query) -> str` (HTML body) gives every job of that tool its own dashboard page at `/view/<job>`, linked from the nav. `bdo-incidents` has one.

## Adding or changing a skill

1. `skills/<group>/<name>/SKILL.md` (same group folder as the tool it describes), frontmatter `name` equal to the folder, `description` listing the trigger phrases.
2. Prose only: when to run which tool, with which flags, and how to report. Code the skill needs becomes a tool it calls as `shed <tool>`.
3. `shed skills link` once per machine; edits are live after that.

## Rules

- Run commands with `sh()` from `shed`, never a shell string.
- Raise `ToolError` for expected failures. Resolve paths against `ctx.cwd`.
- Import heavy SDKs lazily inside `run()` behind a seam function so tests can patch it and `shed list` stays fast.
- Keep `src/shed/lib/` small. Code only one tool needs stays in that tool's folder.
- `tools/base/` is on every device; every other group folder is optional and removable per machine. Host code and host tests (`src/`, `tests/`) therefore depend on `base` only, and a tool imports only tools in its own group tree. `SHED_SKILLS_DIR` overrides the skills folder for tests.
- A skill never holds code or copies of a tool's output format; it points at the tool.
- Changing a convention means updating `docs/DESIGN.md` and the template in the same change.
- Versioning: the shed's version is the latest `v*` git tag (`shed --version`); release by tagging. Each `TOOL` has a `version`; bump it and add a line under `## [Unreleased]` in `CHANGELOG.md` whenever a tool's inputs or result shape change, and list every user-visible change there.

## Using the shed from an agent

- Discover tools with `shed list --json` (name, description, input schema) and run one with `shed <tool> --flags --json`; results are on stdout, logs on stderr, exit 1 means the tool failed, 2 bad usage. Over MCP the same tools are served by `shed mcp --group <folder>` and registered by `shed update`.
- Judgment about when to run which tool and how to report lives in `skills/<group>/<name>/SKILL.md`; read the matching one before using a tool it covers (`shed skills` lists them). Claude Code loads them automatically once linked; other agents read the file.
- The research tools (`product-search`, `social-search`, `browser-fetch`) are anonymous by default. Platforms that need a login are skipped unless the run has `--use-login`; never add that flag to a scheduled job, never try to sign in on the user's behalf, and when a note says "no saved session" tell the user to run `shed browser-fetch --login <site>` themselves.
- Before pulling live data for a topic a cron job already covers, read `shed runs <job> --last` (or the `job-result` MCP tool).
