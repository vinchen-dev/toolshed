---
name: sn-investigate
description: Use when the user asks to investigate, debug or explain an error, failure, exception or wrong behaviour in PMS (PMS2.0_PH, PMS_TS), finance-ai or the autorunner (PMS-Auto-Deposit) — e.g. "why is this failing", "investigate this error on prod", "check the logs for X", "what caused this trace", "is this still happening on QAT", or a pasted error message, stack trace or trace id from one of those systems. Walks the fixed order vault, deployed version, logs (shed error-trace), code (graphify first), data, recent commits, and reports a root cause with its evidence. Read-only. For BDO session hangs and relogin outages use bdo-incident-report instead.
---

# Investigating an error in PMS, finance-ai or the autorunner

An investigation here always reads both the logs and the code: the logs say what happened and when, the code says why. Neither alone is a root cause. Everything below is read-only: no fix, no write to a database, no restart, until the user asks for one.

## Where each project lives

| Project | Repo | Vault project | SLS container | Graph |
|---|---|---|---|---|
| PMS | `~/Desktop/Projects/PMS2.0_PH` (and `PMS_TS`) | `PMS2.0_PH` | many (`pay-scheduler-*`, API pods); leave `--container` off first and read the container list | PMS2.0_PH has `graphify-out/`, PMS_TS does not |
| finance-ai | `~/Desktop/Projects/new finance/finance-ai` | `finance-ai` | `finance-ai` (JSON lines with `msg` and a trace id) | has `graphify-out/` |
| autorunner | `~/Desktop/Projects/PMS-Auto-Deposit` | `PMS-Auto-Deposit` | `pms-auto-deposit`, `pms-session-manager-bdo` | has `graphify-out/` |

All three log to the same SLS logstores: `--env qat` and `--env prod`. If the user did not say which environment, ask; default to QAT only for "is it still happening" checks. Paths can move, so confirm the repo folder exists before relying on it.

## The order

Do the steps in order and stop early only when a step settles the question.

1. **Vault first.** `shed vault-recall --project <vault project> --query "<error words>" --type investigations`, then the same without `--type` if nothing matches. If a hook matches, open that one note with `--open <slug>` before anything else: it may already hold the root cause. Say in the report whether a past note matched.
2. **What is deployed.** `shed deployed-version --env <env> --repo <repo>` for PMS, so the code you read is the code that ran. A `MIXED` line means a rollout was in progress, which is itself a suspect. For the other two projects compare the time of the error with `git log` dates instead.
3. **Logs: the timeline.** `shed error-trace --match "<exact error text or trace id>" --env <env> [--container <name>] --hours 24`. Read, in this order: first seen (did it start with a deploy?), the count per hour (constant, a burst, or periodic like a scheduler), the containers, the signatures, the code frames.
   - Repeat `--match` to require several texts at once, for example a trace id and a message.
   - A note saying the pull stopped at `--limit` means the count and last seen are too low: narrow the window or add `--container` before quoting numbers.
   - Add `--around 30` to see every line of that container 30 seconds either side of the latest occurrence. This is how you find what the process was doing just before it failed.
   - For one finance-ai request, match its trace id with `--container finance-ai`.
   - Need raw lines or a custom SLS query? `shed sls-logs --query '<query>' --project ... --logstore ...`. A console CSV export from the user goes through `shed sls-csv`.
4. **Code: why.** Start from what the logs gave you: a frame (`/app/src/...js:102` maps to `server/src/...` or `src/...` in the repo) or the message text.
   - If the repo has `graphify-out/`, use the graph before searching: `shed graph-query --repo <repo> --question "<what the failing path does>"`, then `--explain <function>` for its neighbours and `--affected <function>` for what else a fix would touch. Open source files only once the graph has pointed at them.
   - No graph (PMS_TS): search for the exact message text, then read the function that logs it and its callers.
   - Read the code at the deployed version when it differs from the checkout: `git show <sha>:<path>`.
   - Explain the failure from the code: which input or state makes this line throw or log, and whether the log timeline agrees.
5. **Data, only if the code points at it.** `shed mongo-query --profile <profile> --db <db> --collection <c> --filter '<json>'` or `--count`, and `shed redis-get --profile <profile> --key <k>`. Both are read-only by construction. Profiles are in `~/.config/mongo.toml` and `~/.config/redis.toml`; a missing profile is something to tell the user, not to work around.
6. **What changed.** `shed branch-diff` between the last good and the deployed ref, or `git log -S"<message text>"` and `git log --since` on the files from step 4. A first-seen time right after a commit or deploy is the strongest lead there is.

## How to report

1. Summary first, 2–3 lines: what fails, the cause, what it means or what to do next.
2. Then the evidence, each item with its source: the timeline numbers from `error-trace`, the file and line, the record or key, the commit.
3. Label every statement as verified (you saw the log line or read the code) or as a hypothesis, and say what would confirm a hypothesis.
4. Say what you did not check: the other environment, data you had no profile for, a time window you did not pull.
5. Propose the fix in a sentence, but do not apply it until asked. Suggest `/obsidian-audit` afterwards so the finding lands in the vault.

## Gotchas

- One SLS record holds many log lines. `error-trace` splits them, so its count is log lines, not records, and unrelated neighbours do not pollute the signatures.
- Lines that mention passwords, OTPs, secrets or tokens are cut or withheld by `error-trace`. Never work around that with raw `sls-logs` output, and never print OTP digits, reference numbers or credentials.
- Times are Manila time everywhere. Pass `--start` and `--end` as `"YYYY-MM-DD HH:MM"`.
- "No matches" proves nothing until the wording is right: match a short, stable fragment of the message, not a whole line with ids in it.
- A searched word such as `error` matches healthy lines too (`"errorPercentage: 0"`). Prefer the specific message.
- QAT MongoDB may be unreachable from this Mac (the cluster's whitelist). Report that instead of guessing at the data.
