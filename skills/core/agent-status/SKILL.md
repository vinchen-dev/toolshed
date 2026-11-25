---
name: agent-status
description: Use when the user asks what their agents are doing, whether anything is stuck, hung, stalled or waiting on them, why a session looks frozen, which subagents or background commands are still running, or what a session that has ended did — e.g. "check my agents", "is anything stuck", "which sessions are waiting on me", "what is that session doing", "is the build still running", "what ran overnight". Runs the shed agent-watch tool, which covers Claude Code and Codex sessions on this machine with their subagents and background commands, and reports each state with its evidence. Read-only.
---

# Checking on agent sessions

`agent-watch` takes one snapshot of the machine: every live Claude Code and Codex session, the subagents and background commands each started, the model each runs on, and the sessions that ended in the last day. Its states are checked against evidence (transcript writes, output files, CPU, listening ports), so "stalled" means a session says it is busy and shows no sign of progress. It only observes.

## The order

1. If a scheduled job already runs `agent-watch` (`shed cron` lists the jobs), read its latest record first: `shed runs <job> --json`, or the `job-result` MCP tool. A snapshot is at most a minute old, and reading it costs nothing.
2. Run `shed agent-watch` yourself when there is no such job, when the user wants this very moment, or when they ask for something the job's arguments leave out. Add `--json` to read the result as data.
3. Narrow before you report. `--state stalled --state needs-input --state gone` answers "is anything stuck"; `--project <folder>` and `--session <name or id prefix>` answer questions about one piece of work; `--agent codex` or `--agent claude` picks one CLI. A state filter keeps the parents of what matches, so a stalled subagent still shows its session.
4. For "what did that finished session do", look at the ended sessions. They stay for a day by default; `--retain` changes that.
5. Only when the user asks to see a command's output, add `--tail <lines>`.

To check on your own subagents and background commands, pass your own session's name or id to `--session`.

## How to report

- Lead with the answer to what was asked: nothing needs attention, or what does, by name.
- For each stalled, needs-input or gone item, give the session it belongs to, its state, and the reason line the tool returns. That line is the evidence; do not replace it with a guess about the cause.
- Say which sessions are waiting on the user before the ones that are stalled when both exist, because the user can clear those at once.
- Mention the snapshot's time when it comes from a recorded run.
- Pass on the tool's notes when there are any. A note means a source was missing or unreadable, so the picture is partial.

## Gotchas

- Never stop, signal, restart or message a session, subagent or command on your own initiative. Report the pid from the reason line and let the user decide. If they ask you to stop something, confirm which item first.
- A session reported as stalled may be in a long model call, and one with a pending tool call and no process may be waiting for the user's approval; the reason line says so. Report it as what the evidence shows, not as a crash.
- A background command that listens on a port is `serving` and is not a problem, however quiet it is.
- The five-minute threshold is a default. If the user says a session is known to think for long stretches, re-run with a longer `--stall-after` rather than arguing with the result.
- Prompts and command lines appear in the result. Quote them only as far as the answer needs.
