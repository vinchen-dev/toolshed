from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from shed.args import describe_flags, format_flags, parse_tool_args
from shed.cron import (
    AGENT_PLIST,
    CRON_FILE,
    STATE_DIR,
    RunStore,
    agent_installed,
    append_log,
    cron_lock,
    install_agent,
    is_due,
    load_jobs,
    local_now,
    next_due,
    run_due,
    uninstall_agent,
)
from shed.define import Tool, ToolContext
from shed.job_tools import JOB_TOOLS, JOBS_GROUP
from shed.lib.errors import EXIT_FAILURE, EXIT_OK, ShedError, ToolError, UsageError
from shed.lib.output import render_result, to_json
from shed.registry import list_groups, list_tool_names, load_all_tools, load_tool
from shed.scaffold import scaffold_tool
from shed.skills import CLAUDE_SKILLS_DIR, link_skills, list_skills
from shed.ui import DEFAULT_HOST, DEFAULT_PORT, serve_ui

USAGE = """\
shed - tools for AI coding agents

Usage:
  shed list [--json]              List available tools (--json includes input schemas)
  shed help <tool>                Show a tool's flags and examples
  shed <tool> [--flag value ...]  Run a tool; add --json for machine-readable output
  shed mcp [--group FOLDER ...]   Serve every tool over MCP (stdio), or only the tools in those tools/ folders
  shed mcp groups                 List the tools/ folders and what each holds ("jobs" is the job tools)
  shed new [<folder>/]<name>      Scaffold a new tool from tools/_template, inside a group folder if given
  shed skills                     List the skills in skills/ (prose guides for agents)
  shed skills link [--group FOLDER ...] [<dir>]  Symlink each skill (or a folder's) into ~/.claude/skills
  shed cron [--json]              Scheduled jobs (~/.config/shed/cron.toml): last run, next due, agent state
  shed cron run [job ...] [--force]  Run the jobs that are due now; --force ignores the interval
  shed cron install | uninstall   Register or remove the launchd agent that keeps `shed ui --cron` running
  shed runs [job] [--last|--json] [--limit N]  Run history; --last prints the latest output, --json the record
  shed ui [--port N] [--cron] [--open]  Serve the dashboard on localhost; --cron also runs due jobs
  shed update [--dry-run]         Update this machine: venv, global shed, Chromium, skills, MCP servers
  shed --version                  Print the shed's version (latest v* git tag, or the installed package's)

Output contract: results on stdout, logs and errors on stderr.
Exit codes: 0 ok, 1 tool failed, 2 bad usage."""


def _out(text: str) -> None:
    if text:
        sys.stdout.write(text if text.endswith("\n") else f"{text}\n")


def _err(text: str) -> None:
    sys.stderr.write(text if text.endswith("\n") else f"{text}\n")


def help_text(tool: Tool[Any, Any]) -> str:
    lines = [
        f"shed {tool.name}  (v{tool.version})",
        "",
        tool.description,
        "",
        "Flags:",
        format_flags(describe_flags(tool.input)),
    ]
    lines += ["", "  --json", "  --help, -h"]
    if tool.examples:
        lines += ["", "Examples:", *(f"  {example}" for example in tool.examples)]
    return "\n".join(lines)


def _cmd_list(json_mode: bool) -> None:
    tools = load_all_tools()
    group = {name: folder for folder, names in list_groups().items() for name in names}
    if json_mode:
        catalogue = [
            {
                "name": t.name,
                "group": group[t.name],
                "version": t.version,
                "description": t.description,
                "input": t.input.model_json_schema(),
            }
            for t in tools
        ]
        _out(to_json(catalogue, compact=True))
        return
    if not tools:
        _out("No tools yet. Run `shed new <name>` to create one.")
        return
    width = max(len(t.name) for t in tools)
    gwidth = max(len(group[t.name]) for t in tools)
    vwidth = max(len(t.version) for t in tools)
    _out(
        "\n".join(
            f"{t.name.ljust(width)}  {group[t.name].ljust(gwidth)}  {t.version.ljust(vwidth)}  "
            f"{t.description}"
            for t in tools
        )
    )


def _cmd_run(name: str, argv: Sequence[str]) -> None:
    tool = load_tool(name)
    invocation = parse_tool_args(tool, argv)
    if invocation.help or invocation.params is None:
        _out(help_text(tool))
        return
    ctx = ToolContext(cwd=Path.cwd(), log=_err)
    result = tool.run(invocation.params, ctx)
    _out(render_result(tool, result, json_mode=invocation.json_mode))


def _group_flags(args: Sequence[str], usage: str) -> tuple[list[str], list[str]]:
    """Split repeated --group values from the remaining positional arguments."""
    groups: list[str] = []
    rest: list[str] = []
    it = iter(args)
    for arg in it:
        if arg == "--group":
            value = next(it, None)
            if value is None or value.startswith("-"):
                raise UsageError("--group needs a folder name")
            groups.append(value)
        elif arg.startswith("-"):
            raise UsageError(usage)
        else:
            rest.append(arg)
    return groups, rest


def _cmd_skills(rest: Sequence[str]) -> None:
    if not rest:
        skills = list_skills()
        if not skills:
            _out("No skills yet. Add skills/<name>/SKILL.md.")
            return
        width = max(len(s.name) for s in skills)
        gwidth = max(len(s.group) for s in skills)
        _out("\n".join(f"{s.name.ljust(width)}  {s.group.ljust(gwidth)}  {s.description}" for s in skills))
        return
    usage = "Usage: shed skills [link [--group FOLDER ...] [<dir>]]"
    if rest[0] != "link":
        raise UsageError(usage)
    groups, positional = _group_flags(rest[1:], usage)
    if len(positional) > 1:
        raise UsageError(usage)
    target = Path(positional[0]).expanduser() if positional else CLAUDE_SKILLS_DIR
    results = link_skills(target_dir=target, groups=groups or None)
    if not results:
        _out("No skills to link.")
        return
    _out("\n".join(f"{r.status:<8} {r.name} -> {r.target}" for r in results))
    conflicts = [r.name for r in results if r.status == "conflict"]
    if conflicts:
        raise ToolError(
            f"not linked: {', '.join(conflicts)}. Something that is not our link already exists at "
            "that path; move it aside and rerun `shed skills link`."
        )


def _dashboard_url() -> str:
    return f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"


def _dashboard_up() -> bool:
    try:
        with urllib.request.urlopen(f"{_dashboard_url()}/health", timeout=1) as resp:
            return resp.status == 200
    except (OSError, ValueError):
        return False


def _cron_status(jobs: Sequence[Any], store: RunStore, json_mode: bool) -> None:
    now = local_now()
    rows: list[dict[str, Any]] = []
    for job in jobs:
        latest = store.latest(job.name)
        rows.append(
            {
                "name": job.name,
                "tool": job.tool,
                "every": job.every,
                "enabled": job.enabled,
                "last": latest.started if latest else None,
                "status": ("ok" if latest.ok else "FAILED") if latest else "never run",
                "took_ms": latest.duration_ms if latest else None,
                "due": is_due(job, latest, now),
                "next": next_due(job, latest),
                "summary": latest.summary if latest else "",
            }
        )
    installed, up = agent_installed() is not None, _dashboard_up()
    if json_mode:
        payload = {
            "config": str(CRON_FILE),
            "agent_installed": installed,
            "dashboard": _dashboard_url() if up else None,
            "jobs": rows,
        }
        _out(to_json(payload, compact=True))
        return
    if rows:
        head = ["job", "tool", "every", "status", "last run", "took", "next", "summary"]
        body = [
            [
                r["name"] + ("" if r["enabled"] else " (off)"),
                r["tool"],
                r["every"],
                r["status"],
                r["last"].strftime("%Y-%m-%d %H:%M:%S") if r["last"] else "-",
                f"{r['took_ms'] / 1000:.1f}s" if r["took_ms"] is not None else "-",
                "due now" if r["due"] else (r["next"].strftime("%H:%M:%S") if r["next"] else "-"),
                r["summary"][:60],
            ]
            for r in rows
        ]
        widths = [max(len(h), *(len(b[i]) for b in body)) for i, h in enumerate(head)]
        _out("  ".join(h.ljust(widths[i]) for i, h in enumerate(head)).rstrip())
        for b in body:
            _out("  ".join(c.ljust(widths[i]) for i, c in enumerate(b)).rstrip())
    else:
        _out(f"No jobs. Add [jobs.<name>] tables to {CRON_FILE} (see the README).")
    agent = "installed" if installed else "not installed (run `shed cron install`)"
    _out(f"agent: {agent}; dashboard {_dashboard_url()} is {'up' if up else 'down'}")
    _out(f"config: {CRON_FILE}")


def _cmd_cron(rest: Sequence[str]) -> int:
    jobs = load_jobs(CRON_FILE)
    store = RunStore(STATE_DIR / "runs")
    sub = rest[0] if rest and not rest[0].startswith("-") else None
    if sub is None:
        _cron_status(jobs, store, json_mode="--json" in rest)
        return EXIT_OK
    if sub == "run":
        names = [a for a in rest[1:] if not a.startswith("-")]
        if not jobs:
            _out(f"No jobs. Add [jobs.<name>] tables to {CRON_FILE} (see the README).")
            return EXIT_OK
        with cron_lock(STATE_DIR / "cron.lock"):
            records = run_due(jobs, store, force="--force" in rest, only=names)
        append_log(STATE_DIR / "cron.log", records)
        if not records:
            _out("nothing due")
            return EXIT_OK
        for r in records:
            _out(f"{r.job}: {'ok' if r.ok else 'FAILED'} in {r.duration_ms} ms  {r.summary}")
        return EXIT_FAILURE if any(not r.ok for r in records) else EXIT_OK
    if sub == "install":
        path, permanent = install_agent(DEFAULT_PORT)
        _out(
            f"Installed {path}\nIt keeps `shed ui --cron` running: dashboard at {_dashboard_url()}, "
            "due jobs every minute."
        )
        if not permanent:
            _err(
                f"warning: {AGENT_PLIST.parent} is not writable (owned by root), so the agent was registered "
                "from "
                f"the shed's state folder and will not start again after a reboot. To make it permanent:\n"
                f"  sudo chown $(whoami) {AGENT_PLIST.parent} && shed cron install"
            )
        return EXIT_OK
    if sub == "uninstall":
        _out("Removed the launchd agent." if uninstall_agent() else "No agent installed.")
        return EXIT_OK
    raise UsageError(
        "Usage: shed cron [--json] | shed cron run [job ...] [--force] | shed cron install | uninstall"
    )


def _cmd_runs(rest: Sequence[str]) -> None:
    store = RunStore(STATE_DIR / "runs")
    job: str | None = None
    limit, json_mode, last = 20, False, False
    args = iter(rest)
    for arg in args:
        if arg == "--json":
            json_mode = True
        elif arg == "--last":
            last = True
        elif arg == "--limit":
            try:
                limit = int(next(args))
            except (StopIteration, ValueError) as exc:
                raise UsageError("--limit needs a number") from exc
        elif arg.startswith("-") or job is not None:
            raise UsageError("Usage: shed runs [job] [--last] [--json] [--limit N]")
        else:
            job = arg
    if job is None:
        names = store.jobs()
        if not names:
            _out("No runs recorded yet. `shed cron run` records one per job.")
            return
        summaries = [{"job": n, "runs": store.count(n), "latest": store.latest(n)} for n in names]
        if json_mode:
            _out(
                to_json(
                    [{**s, "latest": s["latest"] and s["latest"].summary} for s in summaries], compact=True
                )
            )
            return
        width = max(len(n) for n in names)
        for s in summaries:
            latest = s["latest"]
            state = (
                f"{'ok' if latest.ok else 'FAILED'} {latest.started:%Y-%m-%d %H:%M:%S}  {latest.summary[:70]}"
                if latest
                else "-"
            )
            _out(f"{s['job'].ljust(width)}  {s['runs']:>4} runs  {state}")
        return
    if json_mode or last:
        record = store.latest(job)
        if record is None:
            raise ToolError(f"no runs recorded for {job}")
        _out(
            to_json(record, compact=True)
            if json_mode
            else (record.rendered if record.ok else f"error: {record.error}")
        )
        return
    entries = store.entries(job, limit)
    if not entries:
        raise ToolError(f"no runs recorded for {job}")
    for stem, r in entries:
        state = "ok    " if r.ok else "FAILED"
        _out(
            f"{r.started:%Y-%m-%d %H:%M:%S}  {state}  {r.duration_ms / 1000:>6.1f}s  {stem}  {r.summary[:80]}"
        )


def _cmd_ui(rest: Sequence[str]) -> None:
    host, port, schedule, open_browser = DEFAULT_HOST, DEFAULT_PORT, False, False
    args = iter(rest)
    for arg in args:
        if arg == "--cron":
            schedule = True
        elif arg == "--open":
            open_browser = True
        elif arg in ("--port", "--host"):
            try:
                value = next(args)
                if arg == "--port":
                    port = int(value)
                else:
                    host = value
            except (StopIteration, ValueError) as exc:
                raise UsageError(f"{arg} needs a value") from exc
        else:
            raise UsageError("Usage: shed ui [--port N] [--host H] [--cron] [--open]")
    serve_ui(host, port, schedule=schedule, open_browser=open_browser)


def _cmd_mcp(rest: Sequence[str]) -> None:
    if list(rest) == ["groups"]:
        for folder, names in list_groups().items():
            _out(f"{folder or '(top level)'}: {', '.join(names)}")
        _out(f"{JOBS_GROUP}: {', '.join(tool.name for tool in JOB_TOOLS)}")
        return
    groups, positional = _group_flags(rest, "Usage: shed mcp [--group FOLDER ...] | shed mcp groups")
    if positional:
        raise UsageError("Usage: shed mcp [--group FOLDER ...] | shed mcp groups")
    from shed.mcp_server import serve_mcp

    asyncio.run(serve_mcp(groups))


def _dispatch(argv: list[str]) -> int:
    command = argv[0] if argv else None
    rest = argv[1:]
    match command:
        case None | "--help" | "-h":
            _out(USAGE)
        case "--version" | "-V" | "version":
            from shed.version import current

            _out(f"shed {current()}")
        case "help":
            _out(help_text(load_tool(rest[0])) if rest else USAGE)
        case "list":
            _cmd_list("--json" in rest)
        case "mcp":
            _cmd_mcp(rest)
        case "new":
            if not rest:
                raise UsageError("Usage: shed new <name>")
            dest = scaffold_tool(rest[0])
            _out(f"Created {dest}\nEdit tool.py, then run: shed {dest.name} --help")
        case "skills":
            _cmd_skills(rest)
        case "cron":
            return _cmd_cron(rest)
        case "runs":
            _cmd_runs(rest)
        case "ui":
            _cmd_ui(rest)
        case "update":
            if set(rest) - {"--dry-run"}:
                raise UsageError("Usage: shed update [--dry-run]")
            from shed.update import run_update

            return run_update(dry_run="--dry-run" in rest, out=_out)
        case _:
            if command.startswith("-"):
                raise UsageError(f'Unknown option "{command}".\n\n{USAGE}')
            if command not in list_tool_names():
                raise UsageError(f'Unknown command or tool "{command}". Run `shed list`.')
            _cmd_run(command, rest)
    return EXIT_OK


def main(argv: Sequence[str]) -> int:
    """Run the CLI and return the exit code instead of exiting, so it is testable."""
    debug = bool(os.environ.get("SHED_DEBUG"))
    try:
        return _dispatch(list(argv))
    except ShedError as exc:
        _err(f"error: {exc}")
        if debug and exc.__cause__ is not None:
            _err("".join(traceback.format_exception(exc.__cause__)))
        return exc.exit_code
    except Exception as exc:  # process boundary: report any crash cleanly, with a trace on demand
        _err("".join(traceback.format_exception(exc)) if debug else f"error: {exc}")
        return EXIT_FAILURE


def entry() -> None:
    """Console-script entry point."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if os.environ.get("SHED_DEBUG") else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    entry()
