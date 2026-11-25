# Coding standards

These standards define what good code looks like in this repo. They are used when writing code (`dev-plan`, which carries them into the plan that `dev-build` implements) and when reviewing it (`code-review` and `dev-reviewer`).

- **Must** rules are required. Code review reports violations as must-fix.
- **Prefer** rules are the default choice. Code review reports deviations as suggestions.
- These rules override generic best practices. When the code and a rule disagree, follow the rule and flag the conflict.

## Linting and formatting

- Must: Ruff (lint and format) and pyright, configured in `pyproject.toml`, are the authority on anything they enforce. Run them using the commands in AGENTS.md; don't hand-apply or re-review their rules. <!-- src: pyproject.toml -->
- Must: New code introduces no new lint errors or type errors. <!-- src: default -->
- Must: Don't disable a lint rule or silence the type checker inline without a comment explaining why. <!-- src: default -->

## Naming

- Must: A tool's folder and its `name` are the same kebab-case string (`tools/core/log-watch/` holds `log-watch`). Python modules, functions and variables are snake_case, classes PascalCase, module constants UPPER_CASE. <!-- src: code 29/29 tools, 978/978 functions, 228/228 classes -->
- Must: `Input` fields and JSON result keys are snake_case. <!-- src: code 231/231 fields -->
- Prefer: Boolean inputs are bare flag names (`dry_run`, `strict`, `headed`), because each becomes a CLI flag. <!-- src: code 40/40 boolean fields -->
- Prefer: Names that describe intent over names that describe implementation. Avoid abbreviations except widely known ones (id, url, http, db). <!-- src: default -->
- Must: Use the project's terms consistently (tool, group, skill, job, run record, view, seam); don't introduce synonyms for existing concepts. <!-- src: docs/DESIGN.md -->

## File and folder structure

`src/shed/` is the host (CLI, MCP server, registry, scheduler, dashboard) and `src/shed/lib/` its small shared library. `tools/<group>/<name>/` holds one tool per folder: `tool.py`, `test_tool.py`, an optional `view.py`, and that tool's helpers and fixtures. `skills/<group>/<name>/SKILL.md` holds prose skills, `tests/` the host tests, `docs/` the design record and specs. <!-- src: AGENTS.md -->

- Must: Put new code where similar code already lives. Don't create new top-level folders or new structural patterns without a reason stated in the plan. <!-- src: default -->
- Must: Host code and host tests (`src/`, `tests/`) depend on the `core` group only. A tool imports only tools in its own group tree (a parent group counts), never a sibling subtree. <!-- src: AGENTS.md -->
- Must: Code only one tool needs stays in that tool's folder. A helper moves into `src/shed/lib/` only once a second tool needs it. <!-- src: docs/DESIGN.md section 6 -->
- Must: Reach another tool's module through `shed.registry` (`load_tool`, `load_tool_module`), not by file path. <!-- src: docs/DESIGN.md section 11 -->
- Must: Tests live beside what they test: `tools/<group>/<name>/test_tool.py` (and `test_view.py`) for a tool, `tests/test_<module>.py` for the host. Every tool folder has a `test_tool.py`. <!-- src: code 29/29 tools -->
- Must: A skill holds prose only: no code and no copy of a tool's output format. <!-- src: AGENTS.md -->
- Prefer: One main concept per file. Split a file when it serves unrelated concerns. <!-- src: default -->

## Code design

- Must: A tool is one `TOOL = Tool(...)`. Its `run(params, ctx)` returns data (a pydantic model) and never prints; progress goes through `ctx.log()`. <!-- src: AGENTS.md; code 29/29 tools, 0 print calls -->
- Must: Every `Input` field has a `description`, and its type is one of str, int, float, bool, Literal of str, str Enum, lists of those, optionally `| None`. <!-- src: AGENTS.md; code 231/231 fields -->
- Must: Run external commands with `sh()` from `shed` (a program plus an argument list), never `subprocess` directly and never a shell string. <!-- src: AGENTS.md; code 26 sh() calls, 0 direct subprocess calls in tools -->
- Must: Read credentials with `require_env` or `load_env_file` from `shed.lib.env`. Direct `os.environ` reads are for optional, non-secret overrides only (paths, endpoints, switches). <!-- src: code 9 tool files use the helpers; the 9 direct reads are all non-secret -->
- Must: Import heavy SDKs inside `run()` behind a seam (a `make_client` function or a module-level `transport`, `driver` or `probe` object), never at a tool module's top level. <!-- src: AGENTS.md -->
- Must: Resolve relative paths against `ctx.cwd`. <!-- src: AGENTS.md -->
- Must: Don't duplicate logic that already exists. Reuse it, or extract it if it's needed in more places. <!-- src: default -->
- Must: No dead code, commented-out code, or leftover debug output. <!-- src: default -->
- Prefer: Times shown to a person are Manila time, through `shed.lib.manila`. <!-- src: docs/DESIGN.md section 6; code 8 tool files -->
- Prefer: Small functions that do one thing, with early returns over deep nesting. <!-- src: default -->
- Prefer: Clear, explicit code over clever code. <!-- src: default -->
- Prefer: No abstractions, options or extension points that nothing uses yet. <!-- src: default -->
- Prefer: Existing dependencies and the standard library over a new dependency. A new one is added with `uv add`. <!-- src: default -->

## Size and complexity

Limits apply to new and changed code. Touching an oversized legacy file means not making it bigger, not refactoring it.

- Line length (110) is enforced by ruff; don't hand-review it. <!-- src: pyproject.toml -->
- Prefer: Files under 400 lines; split by responsibility when exceeded. <!-- src: user -->
- Must: No new file over 800 lines, and don't grow existing files past it. `src/shed/ui.py` is exempt, because the design record keeps the dashboard in one file. <!-- src: user -->
- Prefer: Functions under 50 lines, nesting at most 3 levels deep. <!-- src: default -->
- Excluded from limits: test fixtures (`tools/*/fixtures/`), `uv.lock`, data files. <!-- src: default -->

## Error handling

- Must: Raise `ToolError` for expected failures, with a message that says what went wrong and what to do next. <!-- src: AGENTS.md; code 122 of 163 raises in tools -->
- Must: Never swallow errors silently. `except Exception` appears only at an isolation boundary (one source of a multi-source tool, the CLI, the MCP server, the scheduler), with the failure recorded or reported. <!-- src: code 7 sites, 0 bare except -->
- Must: Validate input at system boundaries: tool inputs through the pydantic `Input` model, and files, environment values and external responses before use. <!-- src: default -->
- Must: Handle the failure paths of I/O (network, subprocess, filesystem), not only the success path. <!-- src: default -->
- Prefer: A tool that spans several sources reports a result per source (ok, note, items) instead of failing the whole run, with a strict flag to restore fail-fast. <!-- src: docs/DESIGN.md section 11 -->
- Prefer: Error messages that include enough context to debug, and never include secrets or personal data. <!-- src: default -->

## Testing

- Must: Add or update tests for every change in behavior. <!-- src: default -->
- Must: Every bug fix includes a test that fails without the fix. <!-- src: default -->
- Must: A tool test loads the tool through `shed.registry` (`load_tool`, and `load_tool_module` for its module) and drives `run()` with a stub `ToolContext`. <!-- src: AGENTS.md; code 31/31 tool test files -->
- Must: Tests run offline: no network, no real home folder, no real credentials, no browser. Replace the tool's seam with `monkeypatch`, use `tmp_path`, and point state at temporary folders with the environment overrides (`SHED_CRON_FILE`, `SHED_STATE_DIR`, `SHED_SKILLS_DIR`, `SHED_BROWSER_DIR`, `CLAUDE_PROJECTS_DIR`). <!-- src: AGENTS.md; code 91 monkeypatch sites -->
- Must: Tests are deterministic. Time comes from the tool's own seam (a `now` argument or a patched `now_manila`), never the real clock. <!-- src: default; code 5 files patch now_manila -->
- Must: Host tests pass with every optional group folder deleted. <!-- src: AGENTS.md -->
- Prefer: Assert on returned data and rendered text, not on how a helper parsed or classified something. <!-- src: docs/DESIGN.md section 8 -->
- Prefer: Test names are sentences that describe the behavior (`test_groups_an_error_into_a_timeline`). <!-- src: code -->

## Performance and scalability

Expected scale: one developer on one Mac. The dashboard is a localhost viewer, not a service. Tools read logs, transcripts and API responses up to tens of megabytes, and a scheduled job may run as often as once a minute. <!-- src: PRODUCT.md -->

- Must: No database queries or network calls inside loops when they can be batched. <!-- src: default -->
- Must: Bound unbounded work. A tool that lists things takes a limit, and external calls and subprocesses get a timeout. <!-- src: default -->
- Must: Don't load large files or datasets fully into memory when they can be streamed or read in part. <!-- src: default -->
- Must: Release what you acquire, on the error path too: files, clients, browser contexts, child processes. Give every cache a size or time bound. <!-- src: default -->
- Prefer: Cache only with a clear invalidation strategy. <!-- src: default -->

## Security

- Must: No secrets, keys or credentials in code or fixtures. Credentials live in `~/.config/*.env` files read through `shed.lib.env`. <!-- src: docs/DESIGN.md section 6 -->
- Must: Never build shell commands, queries or file paths by concatenating untrusted input. `sh()` takes an argument list. <!-- src: default; code 0 uses of shell=True, eval or exec -->
- Must: Don't log, return or record secrets or tokens. A tool that returns log lines or other free text cuts or withholds lines that carry credentials. <!-- src: code (log-watch redaction, error-trace withheld lines) -->
- Must: The research tools never sign in on the user's behalf, never store or type a password, and never use a logged-in profile from a scheduled job. <!-- src: AGENTS.md -->
- Must: The dashboard binds to localhost only. <!-- src: docs/DESIGN.md section 10 -->

## Comments and documentation

- Prefer: Comments explain why, not what. <!-- src: default -->
- Must: A change to a tool's inputs or result shape bumps that tool's `version` and adds a line under `## [Unreleased]` in `CHANGELOG.md`. Every user-visible change is listed there. <!-- src: AGENTS.md -->
- Must: Changing a convention updates `docs/DESIGN.md` and `tools/_template/` in the same change. <!-- src: AGENTS.md -->
- Must: Update `README.md` when a change affects how something is used or configured, including the group table when a tool is added or moved. <!-- src: default -->
- Prefer: A module docstring says what the tool does; functions get a one-line docstring when the name does not say enough. <!-- src: code 223 one-line docstrings against 12 multi-line -->
- Prefer: A new tool sets its `version` explicitly. <!-- src: code 17/29 tools -->

## Repo decisions

- File limits are 400 lines preferred and 800 required, with `src/shed/ui.py` exempt. (decided 2026-11-23) <!-- src: user -->
- Protected paths are `uv.lock`, the root `DESIGN.md` and `src/shed/static/inter.woff2`; AGENTS.md lists them with the reason for each. (decided 2026-11-23) <!-- src: user -->
