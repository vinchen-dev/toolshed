# tool-shed

A shed of small, well-described tools that AI coding agents call while helping with development. One repo, cloned on every machine: `tools/base/` works for every project and device, and the other group folders (work-only tools such as `tools/PMS/`) are optional and can be dropped on a machine that has no use for them (see [One repo, every device](#one-repo-every-device)).

Every tool is defined once (name, description, typed input, `run`) and is reachable two ways:

- **CLI** - `shed <tool> --flag value --json`, for agents that run shell commands (Claude Code's Bash tool, scripts, CI).
- **MCP** - `shed mcp` serves the same tools over the Model Context Protocol on stdio, with input schemas derived from the same definitions. It also serves `jobs-status`, `job-runs`, `job-result` and `job-run`, which read and trigger the scheduled runs the dashboard shows. Tools can sit in group folders (`tools/base/`, `tools/PMS/`, `tools/PMS/autorunner/`) and `shed mcp --group <folder>` serves only one folder's tools, so each project registers just what it needs.

Skills (prose guides that tell Claude Code when to run a tool and how to report) live in `skills/` and are linked into `~/.claude/skills` with `shed skills link`.

Instructions for agents working in this repo are in `AGENTS.md` (Codex and other agent CLIs read it; `CLAUDE.md` just imports it).

## Setup

Requires [uv](https://docs.astral.sh/uv/) (`brew install uv`; it fetches Python 3.12 itself if needed) and, for the MCP registration, the `claude` CLI. First install and every later update are the same command:

```sh
git clone <this repo> ~/Desktop/Projects/tool-shed && cd ~/Desktop/Projects/tool-shed
uv run shed update             # venv, global `shed`, Chromium, skills, MCP servers; rerun after every pull or `uv add`
shed list
```

`shed update` is idempotent and does, in order: `uv sync`; `uv tool install --editable . --reinstall` (the global `shed` keeps its own environment, so new dependencies need this); `uv run playwright install chromium` when Playwright is a dependency (the research group); `shed skills link`; and one MCP server per tool group found under `tools/` plus the job tools (`tool-shed` for `base`, `tool-shed-pms`, `tool-shed-autorunner`, `tool-shed-research`, `tool-shed-jobs`) in every agent CLI it finds on PATH: Claude Code (`claude mcp add --scope user`) and Codex (`codex mcp add`), removing a stale registration of the same name first. A group deleted from this machine is simply not registered. `shed update --dry-run` prints the plan. An agent CLI that is not on PATH is skipped with a message. Any other agent can use the tools through its shell: `shed list --json`, then `shed <tool> --json`.

Without the global install, run `uv run shed <args>` from this folder.

### Credentials

Tools that talk to external services read their secrets from the environment first, then from a `chmod 600` file under `~/.config/`. Values are never written anywhere by the shed.

| Tool | File | Keys |
|---|---|---|
| `sls-logs`, `bdo-incidents` | `~/.config/aliyun-sls.env` | `ALIBABA_CLOUD_ACCESS_KEY_ID`, `ALIBABA_CLOUD_ACCESS_KEY_SECRET`, optional `SLS_ENDPOINT` |
| `lark-approval` | `~/.config/lark.env` | `LARK_APP_ID`, `LARK_APP_SECRET`, `LARK_APPROVAL_CODE` |
| `lark-attachments` | `~/.config/lark.env` | `LARK_APP_ID`, `LARK_APP_SECRET` |
| `lark-doc` | `~/.config/lark.env` | `LARK_APP_ID`, `LARK_APP_SECRET` (app scopes docx, doc and wiki read) |
| `lark-files` | `~/.config/lark.env` | `LARK_APP_ID`, `LARK_APP_SECRET` (app scopes drive and wiki read) |

| `api-call` | `~/.config/api-call.toml` | one `[profile]` table per API: `base_url`, `headers = { Authorization = "..." }`, optional `timeout`, `verify` |
| `browser-fetch`, `product-search`, `social-search` | `~/.config/shed/browser/<site>/` | no keys: a Chromium profile per site, created by `shed browser-fetch --login <site>` (you sign in by hand once; the session cookies stay there, owner-only). The research tools need `uv run playwright install chromium` once per machine. |

Both Lark tools also take `--env-file <path>` to read the variables from another project's `.env` instead of `~/.config/lark.env`.

## Using the shed

```sh
shed list                       # one line per tool
shed list --json                # name, description and JSON Schema of each tool's input
shed help sls-logs              # flags, defaults, examples
shed sls-logs --minutes 30 --grep relogin
shed bdo-incidents --env both --hours 24 --sort outage --top 6
shed sls-csv --path "~/Downloads/50a50-*.csv" --grep Re-login   # SLS console exports, no query needed
shed bdo-incidents --raw prod=export.csv --since "2026-10-16 03:00"   # --raw takes those exports directly
shed log-watch --path "logs/*_auto_deposit.log" --minutes 15 --grep "token CHANGED"
shed log-watch --path logs/app.log --cursor /tmp/app.cursor   # only what was appended since the last run
shed api-call --profile pms-qat --url /v1/health              # auth from the profile, never in the chat
shed git-context --json         # compact JSON on stdout
shed product-search --query "anker r60i" --source lazada --sort price-asc   # Lazada needs no account
shed product-search --query earbuds --use-login   # Shopee too, after `shed browser-fetch --login shopee`
shed social-search --query "shopee voucher" --since 7d --platform news --platform bluesky --platform threads
shed social-search --url https://www.facebook.com/ShopeePH --limit 5   # a public Page's recent posts, no login
shed social-search --query gcash --cursor ~/.local/state/shed/gcash.cursor --json   # only what is new since last run
shed browser-fetch --login facebook       # one-time sign-in window; --status lists sessions, --forget removes one
shed mcp [--group FOLDER ...]   # MCP server over stdio: every tool, or only those in the named tools/ folders
shed mcp groups                 # the tools/ folders and what each holds ("jobs" is the job tools)
shed skills                     # skills in skills/, with their trigger descriptions
shed skills link                # symlink them into ~/.claude/skills
```

### Output contract

- stdout carries only the result. Progress and errors go to stderr.
- `--json` prints compact JSON. Without it the tool's `render()` is used, falling back to pretty JSON.
- Exit codes: `0` ok, `1` the tool failed, `2` bad usage (unknown tool, unknown flag, validation error).
- All inputs are named flags (`--key value`). Both `--max-size` and `--max_size` work. Booleans: `--flag` or `--no-flag`. Lists: repeat the flag.
- `SHED_DEBUG=1` adds tracebacks to errors.

### Wiring into Claude Code

MCP route (tools show up with schemas, no shell involved). A group is a folder under `tools/`; `--group` names one and can repeat, and the reserved name `jobs` adds the scheduler's `jobs-*` tools. Register the base folder for every project and the others per repo (`--scope local` keeps the entry in `~/.claude.json`, keyed by the folder you run it in; `--scope project` writes `.mcp.json` into the repo instead):

```sh
shed update        # registers every group on this machine in Claude Code (user scope) and Codex; by hand, one group looks like:
claude mcp add --scope user tool-shed-research -- shed mcp --group research
codex mcp add tool-shed-research -- shed mcp --group research
cd ~/Desktop/Projects/PMS-Auto-Deposit && claude mcp add --scope local tool-shed-autorunner -- shed mcp --group PMS/autorunner --group jobs   # per-project variant
```

`shed mcp` with no `--group` still serves everything. `shed mcp groups` prints the folders and their tools. A folder's group holds the tools directly inside it, not those in its subfolders, so `PMS` and `PMS/autorunner` are separate groups.

Bash route: add a line to `~/.claude/CLAUDE.md` such as
"`shed list --json` describes local dev tools; run one with `shed <tool> --flags --json`."

Skills route: `shed skills link` symlinks every `skills/<name>` folder into `~/.claude/skills/<name>`, so Claude Code picks the skill up and edits in the repo are live. A real folder already at the target is left alone and reported; move it aside and rerun.

### One repo, every device

The host (`shed`, cron, the dashboard, `shed mcp`) and `tools/base/` are meant to work on every machine, and the host tests only rely on `base`. Every other group folder is self-contained: its tools, their tests, their fixtures and their skills live under `tools/<group>/` and `skills/<group>/`, and nothing outside those two folders imports them. On a machine that has no use for a group, either leave it unregistered (the tools still show in `shed list`, which is harmless) or remove it:

```sh
rm -r tools/PMS skills/PMS      # the work-only tools and skills are gone; uv run pytest still passes
```

Git will show the deletion; keep it uncommitted on that machine or commit it on a branch of its own. The group's dependencies stay in `pyproject.toml`, which is harmless.

Current groups:

| Group | Tools | Where |
|---|---|---|
| `base` | api-call, git-context, log-watch | every project, every device |
| `PMS` | lark-approval, lark-attachments, lark-doc, lark-files, sls-logs, sls-csv | the work machine, registered in the PMS repos |
| `research` | product-search, social-search, browser-fetch | any machine that wants marketplace and social research; needs Playwright's Chromium |
| `PMS/autorunner` | bdo-incidents (+ `jobs` for its scheduled runs) | the work machine, registered in PMS-Auto-Deposit |

Credentials are per machine as well and never in the repo: `~/.config/lark.env`, `~/.config/aliyun-sls.env`, `~/.config/api-call.toml`. `SHED_SKILLS_DIR` points the skills commands at another folder (the tests use it).

## Research tools and browser sessions

`product-search` (Lazada, Shopee) and `social-search` (YouTube, Threads, Bluesky, Hacker News, Google News, DuckDuckGo, TikTok trends, plus Facebook, Instagram, TikTok, X and Reddit) use no paid API and no developer account. Anonymous routes are the default and are the only ones a cron job should use. Sites that block logged-out visitors (Shopee, Facebook search, Instagram, TikTok search, X, Reddit) work only after `shed browser-fetch --login <site>`: a visible window opens, you sign in by hand, close it, and the session stays in `~/.config/shed/browser/<site>/` until the site ends it. Each run that should use those sessions must say so with `--use-login`; without it those platforms are skipped with a note, so a scheduled job never touches your accounts by accident. When a session dies the tool names the login command. Nothing ever types a password or tries to pass a captcha; a bot-check page is an error.

Both tools report per source or platform (`ok`, `note`, items), so one blocked site does not fail the run; `--strict` makes it fail. `social-search --cursor <file>` returns only posts not seen before, which is what a topic-watch job wants; `product-search` jobs get a `/view/<job>` page with first, lowest, highest and latest price per product. Browser extractors are small JavaScript files next to each tool (`extractors/*.js`); when an extractor throws or comes back empty the run saves the page under `~/.local/state/shed/browser-fetch/` and names it in the source's note, so the fix starts from the real page. `SHED_BROWSER_DIR` moves the profile folder (the tests set it).

## Versioning

`shed --version` prints the shed's version. It is the latest `v*` git tag, with `.postN+g<hash>` past it and `+dirty` for uncommitted changes (hatchling + hatch-vcs compute the same thing at install time, so `uv tool install` metadata agrees). Release by tagging: `git tag v0.2.0 && shed update`. Each tool has its own `version` in its `TOOL` definition, shown by `shed list`, `shed help <tool>` and `shed list --json`; bump it when the tool's inputs or result shape change. Every cron run record stores the shed version and the tool version that produced it, and the dashboard shows both on the run page (and the shed version in the top bar), so a result from before a change is recognisable. `CHANGELOG.md` lists the changes per release and the current tool versions.

## Adding a tool

```sh
shed new my-tool        # copies tools/_template to tools/my-tool
shed new PMS/my-tool    # the same, inside the tools/PMS group folder
```

Then edit `tools/my-tool/tool.py`:

```python
from pydantic import BaseModel, Field
from shed import Tool, ToolContext


class Input(BaseModel):
    path: str = Field(".", description="Where to look")


class Output(BaseModel):
    files: list[str]


def run(params: Input, ctx: ToolContext) -> Output:
    ctx.log("scanning...")  # stderr
    return Output(files=[])  # JSON-serialisable data


TOOL = Tool(
    name="my-tool",  # must equal the folder name
    description="What it does and when an agent should use it.",
    input=Input,
    run=run,
    render=lambda r: f"{len(r.files)} files",  # optional human output
    examples=("shed my-tool --path src",),
)
```

Rules:

- Input fields may be `str`, `int`, `float`, `bool`, `Literal[...]` of strings, a `str` Enum, or `list[...]` of those, each optionally `| None` or with a default. Give every field a `description`.
- Return data (pydantic models, dataclasses, dicts). Never print; use `ctx.log()` for progress.
- Resolve relative paths against `ctx.cwd`.
- Raise `ToolError` for expected failures (exit 1, clean message).
- Run commands with `sh()` from `shed`. It uses `subprocess.run` without a shell, so there is no quoting to get wrong.
- Need a library? `uv add <package>`. Import heavy SDKs lazily inside `run()` so `shed list` stays fast.
- Add `test_tool.py` beside the tool, loading it through `shed.registry.load_tool`. `uv run pytest` must pass.
- A tool can also be a thin wrapper around an existing script in its folder, using `sh()`.

## Scheduled runs and the dashboard

Jobs run tools on an interval and keep every result. Declare them in `~/.config/shed/cron.toml`:

```toml
[jobs.bdo-session]
tool = "bdo-incidents"
every = "10m"                                   # 30s, 10m, 2h, 1d
args = { env = "both", events_hours = 0.25, full = true }    # the tool's flags, snake_case
keep = 300                                      # runs to retain (default 100)
# cwd = "~/Desktop/Projects/PMS-Auto-Deposit"   # optional working directory
# enabled = false
```

```sh
shed cron                      # jobs, last run, next due, agent and dashboard state
shed cron run [--force]        # run what is due now (every job with --force); exit 1 if a job failed
shed runs                      # jobs with recorded runs
shed runs bdo-session           # history: started, status, took, run id, summary
shed runs bdo-session --last    # the latest rendered output (the table)
shed runs bdo-session --json    # the latest record: args, timings, result data, log
shed ui --open                 # dashboard at http://127.0.0.1:8765
shed cron install              # launchd agent that keeps `shed ui --cron` running: dashboard + scheduler
shed cron uninstall
```

Each run is a JSON file under `~/.local/state/shed/runs/<job>/` holding the rendered text, the result data, the tool's log lines and any error, so an agent can read `shed runs <job> --json` instead of re-running the tool. `cron.log` in the same folder has one line per run.

`shed cron install` writes the agent to `~/Library/LaunchAgents`. If that folder is not writable (an installer may have left it owned by root), the agent is registered from `~/.local/state/shed/` instead and runs until the next reboot; `sudo chown $(whoami) ~/Library/LaunchAgents && shed cron install` makes it permanent.

The dashboard (`src/shed/ui.py`, stdlib `http.server`, no dependencies) opens with a health strip (all healthy, or which jobs are failing, with the last run and a per-second countdown to the next) and shows one card per job with its status, last run and a run-now button. A theme button in the top bar switches between following the Mac setting, light and dark. A job's page renders the latest result as facts and tables built from the tool's JSON (nothing tool-specific), with the CLI text, the tool's log and the raw JSON one click away, plus the run history with a page per run and the job's settings. `/tools` opens with a one-line index of every tool; each tool's flags and copyable examples fold out underneath. A tool can ship `view.py` beside its `tool.py` to give its jobs a page of their own at `/view/<job>`; `bdo-incidents` does, so `/view/bdo-session` shows QAT and PROD status, the latest incident table and a timeline stitched from every kept run (window and env pickers in the header). Pages refresh themselves every 30 s, every 3 s while a run is in progress. It binds to localhost only and has no authentication; never expose it on another interface. The JSON API (`/api/jobs`, `/api/jobs/<job>/latest`, `/api/jobs/<job>/runs`) serves the same data. So do the MCP tools `jobs-status`, `job-runs`, `job-result` and `job-run` for agents connected through `shed mcp` (`claude mcp add --scope user tool-shed -- shed mcp --group base`; add `--group jobs` wherever the job tools are wanted).

Override the file locations with `SHED_CRON_FILE` and `SHED_STATE_DIR` (the tests do).

## Adding a skill

A skill is the judgment around a tool: when to run it, which flags, how to read the columns, how to report. Put it in `skills/<name>/SKILL.md` with the usual frontmatter (`name` equal to the folder, a `description` that lists the phrases that should trigger it). Keep it prose only: anything that needs code becomes a tool the skill calls by name. Run `shed skills link` once; after that, editing the file is enough.

## Layout

```
pyproject.toml       deps, scripts, ruff / pyright / pytest config (uv.lock is committed)
src/shed/cli.py      command dispatcher (list, help, new, mcp, <tool>)
src/shed/define.py   Tool and ToolContext
src/shed/registry.py discovers every tool.py under tools/, at any depth
src/shed/args.py     pydantic model -> CLI flags -> validated input
src/shed/mcp_server.py  MCP stdio server over the same tool definitions
src/shed/scaffold.py implements `shed new`
src/shed/skills.py   implements `shed skills` and `shed skills link`
src/shed/cron.py     jobs, run records, due logic, the launchd agent (`shed cron`, `shed runs`)
src/shed/ui.py       the localhost dashboard and JSON API (`shed ui`)
src/shed/lib/        shared helpers: sh(), ToolError, result rendering
tools/<group>/<name>/  one folder per tool: tool.py + test_tool.py + whatever it needs; the group folder
                     (base, PMS, PMS/autorunner) is what `shed mcp --group` serves and can be left out
tools/_template/     starting point copied by `shed new`
skills/<group>/<name>/  SKILL.md only: prose for Claude Code around a tool, linked into ~/.claude/skills
tests/               host tests (args, CLI, MCP, scaffold)
docs/DESIGN.md       the decisions behind all of this
```

## Commands

| Command | What it does |
|---|---|
| `uv run pytest` | tests |
| `uv run pyright` | type check |
| `uv run ruff check . && uv run ruff format .` | lint and format |
| `uv run shed ...` | run the CLI without the global install |
