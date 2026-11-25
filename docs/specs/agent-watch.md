# Agent watch: what every agent session, subagent and background command is doing, and which have stalled

Status: draft, 2026-11-23, revised the same day (`core` group, model per agent, one-day retention). Triage: ready-for-agent. No issue tracker is configured for this repo yet, so this file is the spec of record; move it to the tracker once `/setup-matt-pocock-skills` has run.

## Problem Statement

Vince runs many agent sessions at once. On 2026-11-23 this machine had eleven live Claude Code sessions in eight working folders, the oldest nearly eleven days old. Each of them can start subagents, background shell commands and other agent CLIs: two of the three Codex sessions on record were `codex exec` runs started from inside a Claude Code session's scratch folder. The only way to know what any of them is doing is to visit each terminal or editor pane in turn.

The worse problem is trust. A session shows a spinner and says it is working while nothing is happening: the model call hung, a tool call never returned, a subagent went quiet, a background build is waiting on something that will never arrive, or a permission prompt is sitting unanswered in a pane nobody is looking at. "In progress" is the session's own claim, and nothing checks that claim against evidence. Vince finds out minutes or hours later, by accident. And once a session's terminal is closed, what it did is gone from view.

## Solution

One new read-only tool, `agent-watch`, in the `core` group so that it is on every device, built the way every other shed tool is: one definition, exposed as CLI flags and as an MCP tool, returning structured data that `shed cron` can record and the dashboard can show.

A run takes one snapshot of the machine: every live Claude Code and Codex session, the subagents each session started, the background commands each session started, and agent CLIs that one session launched from another. For each of those it reports what it is doing now, which model it runs on, how far the current turn has got, and a state that is checked against evidence instead of taken on trust: a session that claims to be busy but has written nothing, produced no output and used no CPU for longer than a threshold is reported as stalled, with one line saying what it was last doing and for how long. Sessions that ended in the last day stay on the list with their final details.

Scheduled every minute, it gives the dashboard an Agents page: what needs attention first (stalled, waiting on Vince, gone), then every live session as a tree with its subagents and commands, then the state changes of the last day, then the sessions that ended in the last day. The same answer is available on demand from the terminal with `shed agent-watch` and to any agent over MCP.

It only observes. It never stops, restarts, resumes or messages anything.

## User Stories

1. As a developer, I want one list of every agent session running on my machine, so that I do not have to visit each terminal and editor pane to see what exists.
2. As a developer, I want each session shown with its name, its project folder and which agent CLI it is, so that I can tell eleven sessions apart at a glance.
3. As a developer, I want each session's current turn summarised by the prompt that started it, so that I remember what I asked that session to do.
4. As a developer, I want to see what a session is doing right now (which tool call is in flight, or that it is waiting for the model), so that "busy" means something concrete.
5. As a developer, I want to see how long the current turn has been running and how many tool calls it has made, so that I can judge progress without opening the session.
6. As a developer, I want every subagent of a session listed under it with its type and description, so that I know what was delegated.
7. As a developer, I want to see how many of a session's subagents have finished and how many are still running, so that I can see fan-out work converge.
8. As a developer, I want each running subagent to show its own latest activity and tool-call count, so that I can tell a subagent that is working from one that is not.
9. As a developer, I want nested subagents shown under the subagent that started them, so that the tree matches how the work was delegated.
10. As a developer, I want every background command a session started listed under it with the command text, so that I know which dev servers, builds and test runs my agents left running.
11. As a developer, I want each background command to show how long it has run, how long since it last produced output and whether it is using CPU, so that I can tell a long build from a hung one.
12. As a developer, I want a background command that is listening on a port shown as serving, with the port, so that a dev server sitting quietly is not reported as a problem.
13. As a developer, I want a `codex exec` or `claude` run that one of my sessions launched shown under the session that launched it, so that I can follow work across agent CLIs.
14. As a developer, I want Codex sessions I started myself listed beside the Claude Code ones, so that one page covers both CLIs.
15. As a developer, I want a session that claims to be busy but shows no sign of progress for five minutes flagged as stalled, so that I stop trusting a spinner.
16. As a developer, I want the stalled flag to come with one line of evidence (what it was last doing, how long it has been quiet, whether its process is alive and using CPU), so that I can decide what to do without investigating.
17. As a developer, I want a tool call that has outlived its own time limit flagged, so that a shell command the harness should have ended does not hide behind "running".
18. As a developer, I want a shell command that is still inside its time limit left alone even when it is silent, so that a two-minute test run is not a false alarm.
19. As a developer, I want a session that is waiting for my answer to a question or an approval shown as needing input, so that I can tell "blocked on me" from "stuck".
20. As a developer, I want a stalled subagent to mark the session waiting on it, with a pointer to the subagent, so that I see both the symptom and the cause.
21. As a developer, I want a session whose subagent is healthy to count as working, so that delegation is not mistaken for a stall.
22. As a developer, I want a background command that has no output, no CPU and no listening port past the threshold flagged as stalled, so that I catch a build waiting on something that will never happen.
23. As a developer, I want a session that is idle between turns but still owns a stalled background command to be flagged through that command, so that "I'll wait for the build" does not wait forever unnoticed.
24. As a developer, I want a session whose process died in the middle of a turn shown as gone, so that I learn about a crashed or closed terminal instead of assuming the work finished.
25. As a developer, I want a background command whose process vanished without a completion notice shown as gone, so that I know it did not finish cleanly.
26. As a developer, I want idle sessions listed but kept out of the way, so that ten parked sessions do not bury the one that needs me.
27. As a developer, I want subagents and commands that finished recently kept on the list for a while with their outcome, so that I can see what just completed or failed.
28. As a developer, I want every item to show how long it has been quiet even before it crosses the threshold, so that I can see a stall coming.
29. As a developer, I want to change the stall threshold with a flag, so that I can loosen it for long-thinking sessions or tighten it for a quick check.
30. As a developer, I want to narrow the result to one project, one session, one agent CLI or a set of states, so that a busy machine stays readable.
31. As a developer, I want a filter by state to keep the parents of matching items, so that a stalled subagent still shows which session it belongs to.
32. As a developer, I want an Agents page on the dashboard that refreshes itself, so that I can keep one tab open instead of polling terminals.
33. As a developer, I want that page to put what needs attention at the top, so that the answer to "is anything stuck" takes one glance.
34. As a developer, I want the page to say how long an item has been stalled, across snapshots, so that I can tell a new stall from an hour-old one.
35. As a developer, I want the page to list recent state changes (went stalled, recovered, finished, gone), so that I can see what happened while I was away.
36. As a developer, I want the page to say how old its snapshot is, so that I never read stale data as live.
37. As a developer, I want the page to fall back to the last good snapshot and say so when the latest run failed, so that one bad run does not blank the page.
38. As a developer, I want the Agents page linked from the dashboard nav as soon as the job exists, so that I do not have to remember its address.
39. As a developer, I want `shed agent-watch` in the terminal to print the same tree as text, so that I can check without the dashboard.
40. As a coding agent, I want `--json` output with a stable shape, so that I can answer "what are my agents doing" without screen-scraping.
41. As a coding agent, I want the tool served over MCP with the other core tools, so that I can call it with a schema instead of a shell string.
42. As a coding agent, I want to read the scheduled job's latest record instead of re-scanning, so that I follow the shed's rule of reading recorded runs first.
43. As a coding agent that orchestrates subagents, I want to ask which of my own session's subagents and background commands have stalled, so that I can stop waiting on work that is not progressing.
44. As a coding agent, I want a skill that tells me when to run the tool and how to report it, so that I report stalls with their evidence and never stop a process on my own initiative.
45. As a developer, I want the tool to stay read-only, so that looking at my agents can never disturb them.
46. As a developer, I want the tool to keep working when one source is missing or has changed shape (no Codex folder, an unreadable registry file, an unknown status value), reported as a note on the result, so that one CLI's update does not blind me to the other.
47. As a developer, I want the result to carry the agent CLI versions it saw, so that a format change after an update is easy to pin down.
48. As a developer, I want a snapshot of eleven sessions with multi-megabyte transcripts to take about a second, so that running it every minute costs nothing I notice.
49. As a developer, I want prompt and command text cut to a short length and command output left out unless I ask for it, so that the page does not become a copy of my transcripts.
50. As a developer, I want every time shown in Manila time, so that it lines up with the rest of the shed.
51. As a developer on a device that keeps only the `core` group, I want agent-watch to work there, so that every machine I use can watch its agents.
52. As a maintainer, I want the stall rule exercised by tests that need no live agent, no real process table and no real clock, so that the gate stays offline and fast.
53. As a maintainer, I want the design record, README and changelog to describe the tool and why the stall rule is what it is, so that a later change does not reopen these decisions.
54. As a developer, I want each session to show the model it is running on, so that I can tell which model is doing which work at a glance.
55. As a developer, I want each subagent to show its own model, so that I can see which work was delegated to a smaller model.
56. As a developer, I want a session that ended in the last day to stay on the list with its last prompt, model, final state and counts, so that I can still see what it did after its terminal is closed.
57. As a developer, I want ended sessions kept apart from live ones, so that a day of history does not bury what is running now.
58. As a developer, I want the recent changes to span a day, so that I can see what happened overnight.
59. As a developer, I want to change how long ended sessions are retained with a flag, so that a quick check can leave history out.

## Implementation Decisions

### Tool, group and surfaces

- One new tool named `agent-watch` in the `core` group, the group every device keeps. It is served by the existing core MCP entry with no registration change.
- A `core` tool cannot import an optional group, and the Claude Code transcript reader lives inside `session-stats` in the `agent` group. The reader therefore moves into the shed's shared library, which is the design record's rule once a second tool needs a helper. `session-stats` and `transcript-digest` import it from the library; their inputs and results do not change.
- The tool is read-only. It reads files and the process table and returns data. It has no flag that stops, signals, resumes or messages anything.
- A run that finds stalled items still succeeds. A finding is data, not a tool failure, and a failed run would cost the dashboard page its snapshot.
- A `view.py` beside the tool gives jobs of this tool a dashboard page titled Agents, reached and linked from the nav through the existing per-tool view mechanism. The dashboard and the scheduler are not changed.
- One prose skill, `agent-status`, in a new `core` skills folder: when to run the tool (asked what the agents are doing, whether anything is stuck, why a session looks hung), to read the scheduled job's latest record before scanning again, to report stalled and needs-input items with their evidence line, and never to stop or signal a process unless Vince asks. Prose only; it points at the tool.
- The scheduled job is declared by Vince in the cron file like any other: this tool, every minute, keeping 1,440 runs, which is one day. The README gives the lines to add. The scheduler already ticks once a minute.

### Vocabulary

Items form a tree. Every item has a kind, a state, a one-line reason and the time since its last sign of progress.

- Session: one agent CLI process with its transcript. Claude Code or Codex.
- Subagent: an agent a Claude Code session delegated to, with its own transcript. Its parent is the session or the subagent that started it.
- Background command: a shell command a Claude Code session started in the background, or one that outlived its time limit and was moved there.
- Child session: a session whose process descends from another session's process (for example `codex exec` started by a Claude Code tool call). It is listed under the session that launched it.

States, shared by all kinds:

| State | Meaning | Applies to |
| --- | --- | --- |
| working | In progress, with a sign of progress inside the stall threshold | all |
| needs-input | In progress but blocked on Vince: an unanswered question or approval | session, subagent |
| stalled | Claims to be in progress, process alive, no sign of progress for longer than the threshold | all |
| idle | Process alive, no turn open; waiting for the next prompt | session |
| serving | Alive and holding a listening TCP port; never counted as stalled | background command |
| done, failed | Finished, kept on the list for the look-back window | subagent, background command |
| gone | Was in progress and its process is no longer there | all |
| ended | Process no longer there and the last turn completed; kept for the retention period | session |

Stalled, needs-input and gone are the attention states. The result counts them and both renderings list them first. Gone sessions are kept for the retention period, like ended ones.

### Sources (read on 2026-11-23 from Claude Code 2.1.322 and Codex CLI 0.162.1 on this machine)

Claude Code:

- Live session registry. The sessions folder of the Claude Code home holds one JSON file per running process, named by pid: pid, session id, working folder, session name, kind, entrypoint (terminal or editor extension), version, process start time, a status, and the times the record and the status last changed. Three status values were seen: busy, idle and waiting. While a question dialog was open the record read waiting, with a waiting-for value of "input needed"; the value written for a permission prompt has not been seen. Key files sit in the same folder and are never opened.
- A registry record is trusted only while its pid is alive and that process started when the record says it did. The record stores the start time as UTC text and `ps` prints local time, so the two are compared as instants. A record that fails the check belongs to a dead session.
- Transcript. One JSONL file per session under the projects folder, which the shared transcript reader already parses. This tool additionally uses the system record that marks the end of a turn, tool results that carry a background task id, the injected task notifications (task id, status, summary), tool calls that have no result yet, and the generated title record. Assistant records name the model that produced them.
- Subagents. A folder named after the session, beside its transcript, holds one transcript and one small metadata file per subagent, all in the same flat folder. The metadata gives the agent type, its description, the id of the tool call that started it, its spawn depth, whether it was started in the foreground or background, the id of its parent agent when another subagent started it, and a finished flag once it has ended (current versions; older files lack it).
- Background commands. The result of a background shell call carries a task id and names an output file in a per-session tasks folder under the per-user Claude temp folder. The command runs as a direct child of the session's process: a shell wrapper whose command line contains the command text, with the command itself below it in the same process group. A shell call that outlives its time limit is moved to the background the same way. Completion arrives in the transcript as a task notification with a status.
- Other children. MCP servers and helper processes start with the session (ten MCP servers and a keep-awake helper under one session here). They are counted on the session, not listed.

Codex:

- Rollout files. One JSONL file per session under the sessions folder of the Codex home, in year, month and day folders, named with the start time and the session id. The first record is session metadata: id, working folder, originator (the desktop app or `codex exec`), source and CLI version. Turn start and turn completion are event records; completion carries the duration. Each turn's context record names the model. Tool calls and their outputs are separate records paired by call id. A session index file maps session ids to thread names.
- No live registry with a status was found in the Codex home, so a Codex session's liveness comes from the process table, and "claims to be in progress" means a turn start with no completion.
- Codex items in this version are sessions only: no subagent or background-command items.

### Evidence and the stall rule

- A sign of progress is, strongest first: a new record in the item's own transcript; growth of a background command's output file; CPU use above an idle floor by the processes attributed to the item's open tool call.
- A parent's last sign of progress is the newest among its own and those of the items its open turn is waiting on. A session waiting on a healthy subagent is working. When the subagent stalls, both stall, and the session's reason names the subagent it is waiting on.
- "Claims to be in progress" is the registry's busy status for a Claude Code session, falling back to a turn with no end marker when the registry is unreadable; a turn start with no completion for Codex; no finished flag, result or completion notice for a subagent; a live process with no completion notice for a background command.
- An item that claims to be in progress and whose last sign of progress is older than the stall threshold is stalled. The default threshold is five minutes.
- A pending shell tool call gets its own declared time limit, or the harness default of two minutes when none is declared, before the stall clock starts. Once it has outlived that limit with a grace period, it is stalled whatever the threshold says.
- A registry status of waiting makes the session needs-input, with the waiting-for text in the reason. A pending question tool call does the same when the registry is unreadable. A pending tool call with no attributed process and no progress past the threshold, on a session the registry still calls busy, is stalled, and its reason says it may be waiting for approval.
- An idle session is never stalled, but its background commands are judged on their own. That is how "waiting for the build" shows up: session idle, command stalled.
- A background command that holds a listening TCP port is serving and is never stalled. Without that rule every dev server would be a false alarm.
- A session with no live process whose last turn completed is ended; with its last turn open it is gone. Both stay on the list for the retention period, found by the transcript's last write time. An ended session is one row: its last prompt, model, when it ended and how many tool calls, subagents and background commands its last turn had. Its subagents and commands are not listed.
- A Codex session is classified from its rollout first: an open turn written to inside the threshold is working with or without a paired process. Pairing a rollout to a process (by working folder and start time) decides only between stalled and gone, and between idle and ended.
- Processes are attributed to a tool call when they descend from the session's process and started at or after the call. Processes that started with the session are the counted helpers.
- A session's model is the model named by the newest assistant record in its transcript, and a subagent's is the same from its own transcript. A Codex session's is the model in its newest turn context. An item with no such record yet has no model.
- Every item carries its reason as one plain sentence built from the evidence: what it was last doing, how long it has been quiet, whether its process is alive and what CPU it is using, and any time limit it has passed.

### Inputs

- `project`: a folder path or an encoded project name. Default is every project on the machine, because the tool is a machine-wide watch; this differs from `session-stats`, which defaults to the current folder.
- `agent`: repeatable, `claude` or `codex`. Default both.
- `session`: a session id prefix or a session name, to show one session's tree.
- `state`: repeatable state filter. Matching items keep their ancestors.
- `stall_after`: the stall threshold as a duration such as `90s`, `5m` or `1h`. Default `5m`.
- `window`: how long finished and gone subagents and commands stay on the list. Default `30m`.
- `retain`: how long ended and gone sessions stay on the list. Default `1d`.
- `max_chars`: characters kept per prompt, command and activity line. Default 120.
- `tail`: lines of each background command's output to include. Default 0, so output is never copied unless asked for.
- Source folders: the Claude Code transcripts root keeps the flag and environment variable `session-stats` and `transcript-digest` use. The session registry folder, the task-output root and the Codex home each get a flag with the real location as the default, so tests and unusual installs can point elsewhere.

### Output

- The report holds: the time taken (Manila), the thresholds used, the agent CLI versions seen, a count per state, the attention count, the sessions, and a list of notes for any source that was missing, unreadable or of an unknown shape.
- A session holds: id, agent CLI, name, title when the CLI recorded one, project folder, pid, kind and entrypoint, model, state, reason, seconds quiet, the open turn (start, elapsed, starting prompt as one line, tool calls so far), the current activity as one line, the id of the item it is waiting on, the count of helper processes, its subagents, its background commands and its child sessions. An ended or gone session also holds when it was last written to and the counts of its last turn.
- A subagent holds: id, type, description, model, foreground or background, depth, state, reason, start, seconds quiet, tool calls, current activity and its own subagents.
- A background command holds: task id, command as one line, the description given with it, pid, start, runtime, state, reason, seconds quiet, output size, listening ports and, when asked for, the output tail.
- Text rendering: one summary line with the counts and the snapshot time, then the attention items, then one line per live session with its subagents and commands indented below it, idle sessions after the busy ones, then the ended sessions under their own heading, one line each. The model is a column on session and subagent lines.
- A missing source never fails the run. No sessions folder, no Codex home or an unreadable file becomes a note. The run fails only when it can read nothing at all.

### Reading cost

- The job runs every minute against transcripts that reach 20 MB on this machine, so transcripts are read from the end and only as far back as the open turn, with a bound. An idle session with no running background command needs only its registry record, file times and a short tail for its model and last prompt.
- The tail read and the extra record kinds are added to the shared transcript reader instead of writing a second parser.
- Ended sessions are found by transcript write time inside the retention period. A transcript that has not changed since it was last read is not read again while the same process keeps running, which is the case for the scheduled job inside the dashboard.
- The process table and the listening ports are each read once per run, not once per session.

### Dashboard page

- Built from the job's stored run records only. The view never scans the machine, which keeps it a pure function of its inputs.
- Top: the snapshot's age and the counts per state. Then the attention list with each item's reason and how long it has been in that state. Then every live session as a tree, with a model column. Then recent state changes. Then the sessions that ended in the retention period, folded away until opened.
- "Stalled since" and the list of state changes come from comparing kept runs: an item's current state is traced back to the first run of its unbroken streak, and transitions between consecutive runs become change lines. With 1,440 kept runs the changes span a day.
- Query parameters filter by state and by project, as the incidents page filters by environment and window.
- When the latest run failed, the page shows the last good snapshot and says so. With no good run yet it says that instead of showing an empty tree.
- It uses the dashboard's own HTML helpers and the visual system recorded in the root design file. It adds no script, font or network request.
- The dashboard parses every kept run on each page load, so a day of snapshots makes this the heaviest page. That cost is accepted for now and measured during the build.

### Conventions and records

- The tool starts at version 0.1.0 with a changelog line under Unreleased. The reader's move is listed there too; `session-stats` and `transcript-digest` keep their versions because their inputs and results do not change.
- The README gains usage lines, the cron lines for the job with the one-day keep, and a note that the page shows prompt and command text. The design record gains a section: the tool and why it is in `core`, the reader's move into the shared library, the evidence-over-claim stall rule, the serving exception, one-day retention, why push alerts were left out and why the page is built from records.
- No new dependency. Files, JSON and the process table are standard library plus the shed's `sh()` helper.
- No change to the dashboard, the scheduler, the tool template or the `ToolContext`. The shared library gains the transcript reader. One host test that lists the `core` tools gains the new name.

## Testing Decisions

- A good test drives the tool through its `run` function, loaded through the registry, with a stub context, and asserts on the returned states, reasons and rendered text. It never asserts on how a record was parsed or which helper classified an item.
- Two seams, both existing shapes, agreed with Vince on 2026-11-23:
  - The tool seam: `run` against a fake home tree built under a temporary folder (registry files, transcripts, subagent files, task-output files, Codex rollouts) through the source-folder inputs, plus one module-level `probe` object that supplies the process table, the listening ports and the clock. Tests replace `probe` with a fake. Nothing else is patched.
  - The view seam: the view function called with built run records, a fixed time and a query, asserting on the returned HTML.
- No separate classifier seam and no live smoke test. The stall rule is covered through `run`.
- Tool tests, one behaviour each:
  - a busy session with a fresh transcript write is working, and its reason names the pending tool call;
  - the same session quiet past the threshold is stalled, with the quiet time and the pending call in the reason;
  - a shell call inside its declared time limit is working while silent, and stalled once past the limit and grace;
  - a registry status of waiting is needs-input with the waiting-for text in the reason, and a pending question call is needs-input when the registry is unreadable;
  - a pending call with no process past the threshold is stalled with the approval hint;
  - a session waiting on a subagent with fresh writes is working; with a quiet subagent both are stalled and the session points at the subagent;
  - a subagent with a finished flag or a result in its parent's transcript is done, with an error result failed, and drops off after the window;
  - a subagent whose metadata names a parent agent hangs under that subagent;
  - a background command with growing output is working, with CPU and no output working, with neither stalled, with a listening port serving, with a completion notice done or failed, and with no process and no notice gone;
  - an idle session with a stalled background command is idle and the command is stalled;
  - a registry record whose pid is dead, or whose start time does not match the process, gives gone when the turn was open and ended when it had completed, including the UTC against local comparison;
  - an ended session stays on the list inside the retention period with its last prompt, model and counts, and drops off after it;
  - a session and a subagent each report the model of their newest assistant record, and an item with none reports no model;
  - a process that started with the session is counted as a helper, one that started after a tool call is attributed to it;
  - a Codex rollout with an open turn and fresh writes is working, quiet with a Codex process alive stalled, quiet with none gone, completed with a process idle and without one ended, and it reports the model of its turn context;
  - a `codex exec` process below a Claude Code session is listed as that session's child;
  - the state, project, session and agent filters, with ancestors kept;
  - a missing Codex home, a missing registry folder and a malformed registry file each become a note and the rest of the report stands;
  - an unknown registry status is passed through and does not fail the run;
  - a large transcript is read from the end only (asserted by behaviour: an old turn far back in the file does not appear);
  - text longer than `max_chars` is cut, and output is absent unless `tail` is set;
  - the text rendering lists attention items first, idle sessions after busy ones and ended sessions last, with the model column.
- View tests: the page lists attention items first; "stalled since" spans several runs; a state change between two runs appears in the change list; the ended sessions are in their own folded section; filters by state and project; the failed-latest fallback; the empty state; a day of kept runs renders.
- Reader tests: the moved reader keeps the behaviour `session-stats` and `transcript-digest` already test, and its new parts (the tail read, turn ends, background task ids, task notifications) get host tests, because the library is host code.
- The host test that lists the `core` tools gains the new name. Deleting the `agent` group must still keep pytest, pyright and ruff green.
- Two checks need a live agent and are done by hand during the build, agreed on 2026-11-23: one `codex exec` run to confirm how a rollout pairs with its process, and one pending permission prompt to record what the registry writes. A third records how long the Agents page takes with a day of kept runs.
- Prior art: `session-stats` (a fake transcript tree with controlled file times), `social-search` (one module-level object swapped with monkeypatch, frozen now), `log-watch` (frozen clock, `ToolError` paths), `browser-fetch` (the driver seam that keeps a heavy dependency out of tests), `npm-deps-check` (a faked `sh()`), and `bdo-incidents` view tests (a page built from several stored runs, with the failed-latest fallback).

## Out of Scope

- Stopping, signalling, restarting, resuming or messaging a session, subagent or command. The tool is read-only; the reason line gives the pid for Vince to act on.
- Push alerts: desktop notifications and Lark messages. Decided 2026-11-23: the dashboard page and the CLI are the alert, so the threshold can be tuned on recorded snapshots first.
- Agent CLIs other than Claude Code and Codex, cloud or remote sessions, and sessions on other machines.
- Codex subagents and Codex background commands, and reading Codex's SQLite state.
- Reasoning effort beside the model.
- The subagents and commands of ended sessions, beyond their counts.
- A history file kept by the tool so that the page need not parse a day of snapshots. It is the follow-up if the page proves slow.
- Task-list progress. No todo or task-list records exist in the sixty most recent transcripts on this machine, so progress is the turn's elapsed time, tool calls, current activity and subagent counts.
- Token and cost accounting (`session-stats`) and full session write-ups (`transcript-digest`).
- Redacting prompt or command text. Text is cut to a length; it is not scanned for secrets.
- Diagnosing why something stalled.
- Process probing on anything but macOS, the only platform the scheduler agent already targets.
- Changes to the dashboard itself: the home page's health strip, a live-streaming page, refresh faster than the one-minute scheduler tick.
- A pluggable source interface for further agent CLIs.

## Further Notes

- Everything under Sources was read from this machine on 2026-11-23. The background-command process shape was confirmed by starting one and reading the process table, and the waiting status by sampling the registry while a question dialog was open. Two things are still not verified and are checked by hand during the build: what Claude Code writes to the registry for a permission prompt, and how to pair a Codex rollout with its live process (no Codex process was running; working folder plus start time is the proposed rule).
- These files are internal to the two CLIs and undocumented. Any release can change them. The readers ignore unknown fields, turn a missing or malformed source into a note, and record the CLI versions seen, so a break shows up as a note beside a version change instead of a wrong state.
- The five-minute default is a starting guess. A model call with long thinking can legitimately write nothing for minutes, and the session's own CPU does not separate the two cases, because the terminal keeps redrawing either way (idle sessions sat at 0.0 to 0.4 percent and a busy one at 12.4 in one sample). After a week of one-minute snapshots, the kept runs show how often a stalled item recovered by itself; tune the default from that.
- The page shows prompt and command text on a dashboard that binds to localhost without auth. Tunnelling the dashboard exposes that text, as it already does for every recorded run; the README says so.
- The session index in the Codex home gives thread names, and Claude Code transcripts carry a generated title record; both are optional labels, and a session without one falls back to its registry name or its first prompt.
- Suggested build order: move the reader; Claude Code sessions from the registry and the tail read, with working, idle, stalled, needs-input, gone and ended; then subagents; then background commands with the probe's ports and CPU; then Codex sessions and child sessions; then the view; then the skill and the docs.
- Decisions taken in the 2026-11-23 sessions (see also "Decisions during build" at the end): both Claude Code and Codex in the first version; the dashboard page plus CLI and MCP as the only surfaces, no push alerts; tests through `run` on a fake tree with one `probe` object, and through the view function; spec kept in the repo until a tracker exists. Then, the same day: the tool is a core function and lives in `core`, with the reader moved into the shared library; each session and subagent shows its model; ended sessions are retained for a day and the job keeps a day of snapshots; the two live checks are done by hand during the build.

## Decisions during build

- 2026-11-23: How does agent-watch decide a turn has finished, given that Claude Code writes its turn-end record for only about 70% of finished turns (19 of 28, 31 of 45 and 5 of 6 in three sessions on this machine)? Answer (Vince): also use the stop reason. A turn is finished when the newest assistant message says it ended its turn and nothing follows it, as well as when a turn-end record follows it.
- 2026-11-23: How is a background command shown when the session stopped it with its own stop call, which leaves no completion notice? Answer (Vince): as done, with the reason "stopped by the session". Only a command that vanished with no notice and no stop call is gone.
- 2026-11-23: Finding, no decision needed: the current Claude Code version records task notifications as queue records, and older transcripts as user records. The shared reader reads both.
- 2026-11-24: Should `--tail` scan output lines for secrets, given that the spec says text is not scanned and the coding standards (written after the spec) require returned free text to withhold lines that carry credentials? Answer (Vince): withhold only clear credential shapes: a secret-like name followed by `:` or `=`, Authorization and Bearer headers, `user:pass@` in URLs, and well-known key prefixes. Lines that merely mention such a word stay. Prompt and command text stay unscanned, as the spec says.
- 2026-11-24: A shell tool call has no process and has outlived its own time limit, but `--stall-after` is longer than that limit; the limit rule and the approval rule point different ways. Which applies? Answer (Vince): whichever trips first. A shell call with no process is stalled once it is past its limit plus grace, or quiet past the stall threshold, with the approval hint. A shell call that has a process is judged on its limit, counted from when the process started.
- 2026-11-24: How is a background command shown when it is known only from its process (launched before the part of the transcript that is read) and is quiet, with no CPU and no listening port? Answer (Vince): working, with "output not tracked" in the reason. It is never flagged as stalled without output evidence.
- 2026-11-24: Finding, no decision needed: a background command a subagent started is listed under the session, and the notice that it finished is written to the session's transcript, not the subagent's.
- 2026-11-24: The `--tail` credential pattern was wrong in one direction or the other for three rounds. Which way should it lean? Answer (Vince): toward withholding. A line is withheld when a name containing pass, pwd, secret, token, credential or key is followed by `:` or `=`, and for Authorization or Bearer headers, `user:pass@` in a URL, private-key headers and well-known key prefixes. Counters such as `max_tokens: 4096` are withheld too; prose with no `:` or `=` stays; there is no separate rule for command-line flags. This replaces the narrower wording of the earlier answer.
- 2026-11-24: Does the shell-call rule (stalled when the limit or the stall threshold trips first for a call with no process; limit counted from when the process started) also cover a subagent's open shell calls? Answer (Vince): yes, the same rule.
- 2026-11-24: The review of round 4 left four findings. Which are fixed? Answer (Vince): only the two behaviour bugs. A session that waits on a subagent through an open call follows that subagent's state (working while it works, stalled when it stalls), and a finished command whose shell is still alive is listed once even when a hook rewrote its text. The other two stay as known limits: `--tail` still shows a line where something other than a quote sits between the name and the `:` or `=` (`env[PASSWORD]=...`, `os.environ["API_KEY"] = ...`), and the subagent rule repeats a few lines of the session rule.
- 2026-11-24: Once a session's own turn has ended and the registry still calls it busy, does a background subagent that is working keep it working, although the session has no open call to that subagent? Answer (Vince): yes. It is working while a background subagent works, including one running a quiet command inside its time limit, and it stays on its own clock when they have all stalled.
- 2026-11-24: Does the hint "It may be waiting for your approval" apply to a subagent stalled on a pending call that is not a shell call, such as a Read or an Edit? Answer (Vince): yes, on all calls, the same rule as for a session. Only the reason text is affected, not the state.
- 2026-11-24: When a hook has rewritten a command's text, only the start time can tell whether a live shell belongs to a finished background command or to an open call. Which side wins? Answer (Vince): the open call. Every open call takes its shell before a finished command is matched by start time alone, so a running call is never flagged as waiting for an approval because of it. Known limit: a call that is really waiting on an approval can take a finished command's rewritten, still-running shell; it then reads working with no approval hint until that shell outlives the call's limit, and its own shell can be listed as an extra `pid-<n>` row.
- 2026-11-24: Should the text output show the `--tail` lines, which were in the result data only? Answer (Vince): yes. With `--tail` set, the text output lists each command's last output lines under it, with the same cut and the same withholding as the result; without it nothing is added.
- 2026-11-25: How should the Agents page be laid out? Answer (Vince): a list of sessions with their sub-sessions and agents under them. From two mock-ups he picked session blocks: each session is a block that folds, with the subagents, commands and sessions it started inside. The separate "Needs attention" table is folded into the list: sessions that need attention, or have something under them that does, come first and carry a marker, and every row shows how long it has held its state.
- 2026-11-25: After watching test subagents on the page, Vince asked for two things. New subagents "didn't show until refresh": an open page now shows a newly recorded snapshot within about three seconds. And "add folder and start time": each session shows its folder and when it started, and each subagent and command its start time. The snapshot itself is still taken once a minute, by his earlier choice.
