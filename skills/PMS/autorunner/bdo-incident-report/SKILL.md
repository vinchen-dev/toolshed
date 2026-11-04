---
name: bdo-incident-report
description: Use when the user asks to check, monitor or summarize BDO session hangs, relogin outages or session-refresh activity from SLS logs (QAT and/or PROD) in the PMS-Auto-Deposit project — e.g. "check BDO relogin", "give me the outage table", "top 6 outages", "check both QAT and prod", "any new hangs since the last check". Runs the shed bdo-incidents tool and reports its per-incident table (flag, life before flag, hang location, retry recovered, relogin attempts, saved, outage) or a cron-style "what is new" check. Read-only.
---

# BDO incident report (SLS)

The numbers come from `shed bdo-incidents`, a tool-shed tool that pulls the BDO session-manager and auto-deposit logs from SLS and groups them into incidents. This skill is only the judgment around it: which flags to use and how to report. Read-only: nothing here changes the repo, MongoDB or any service.

## Run it

```bash
shed bdo-incidents --env both --hours 24 --sort outage --top 6        # longest outages, QAT and PROD
shed bdo-incidents --env qat --since "2026-10-26 09:00" --sort time   # every incident since a time, in order
shed bdo-incidents --env both --events-since "2026-10-26 16:30"       # cron mode: only what is new
shed bdo-incidents --help                                             # every flag with its default
shed bdo-incidents --env prod --raw prod=~/Downloads/50a50-abc.csv --since "2026-10-16 03:00"   # a console export
```

- `--env qat|prod|both`. QAT is the default. Use PROD only when the user asks for it or says "both".
- `--hours N` (default 24) or `--since "YYYY-MM-DD HH:MM"` (Manila time) sets the window.
- `--format box` (default) prints one boxed table per environment inside a code block, with identical column widths across QAT and PROD. Outages of 10 minutes or more carry a `*`. `--format side` pairs QAT and PROD on one row by start time (within 150 seconds) with a `PROD-QAT` column; `--top N` then keeps the N longest pairs. `--format plain` is a compact table with short step labels, `--format md` a markdown table. Use the default unless the user asks for another layout.
- `--sort outage` is longest first; `--sort time` is chronological. `--top N` keeps the first N rows after sorting.
- `--events-since "YYYY-MM-DD HH:MM"` or `--events-hours N` lists visits, hangs, retries, relogin failures and saves newer than that time. Each "session invalidated" line shows the session's age and flags it near the 2h mark.
- Always pass Manila times. The output starts with `now ... Manila` so you can check the clock; `TZ=Asia/Manila date -v-1H "+%Y-%m-%d %H:%M"` turns "an hour ago" into one.
- `--json` returns the same data as structured JSON (incidents, totals, events) when you need to compute rather than read.
- `--raw ENV=FILE` reads a saved pull instead of SLS: either `shed sls-logs` text or an SLS console `.csv` export (the `50a50-*.csv` downloads). When the user hands you several exports for one environment, merge them first: `shed sls-csv --path a.csv --path b.csv > pull.txt`, then `--raw prod=pull.txt`.
- Credentials come from `~/.config/aliyun-sls.env` (see the tool-shed README). A 24-hour pull takes a few seconds per environment, up to a minute on a slow day. macOS has no `timeout` command, so do not wrap it in one.
- Exit code 1 with "no matching events after 3 tries" means SLS returned nothing even after retries. Rerun once, and check the credentials file, before reporting anything. Never report it as "no change". In `--events-since` mode, "no change" means the pull worked and nothing happened in the window.

## Scheduled checks

When a `bdo-session` job is configured in `~/.config/shed/cron.toml` (`shed cron` lists it), the check already runs every 10 minutes. Its page on the dashboard, http://127.0.0.1:8765/view/bdo-session, shows both environments, the incident table of the last 24 hours and a timeline stitched from every kept run. For a "what is new" question read the latest recorded run instead of pulling again: `shed runs bdo-session --last` prints the events, `shed runs bdo-session --json` gives the data, and `shed runs bdo-session` lists earlier runs. The dashboard at http://127.0.0.1:8765 shows the same. Pull live with `shed bdo-incidents` only when the latest run is older than the question needs or the user asks for a different window.

## What the columns mean

| Column | Meaning |
|---|---|
| Flag | First "Invalidated session" time of the incident. |
| Life before flag | Age of the session that died, at the flag. |
| Hang location | First failure: `GET page N, 30s` (curl timeout), `balance fetch`, or `token refresh (session expired)`. |
| Retry recovered? | `Yes` if the one-time retry reactivated the session. `Yes, then failed again` if a later flag still needed a relogin. |
| Relogin attempts / failed (where) | Attempts including the one that saved, failed count, and the step each failure stalled on. |
| Saved | `REPLACED (login saved)` time, `n/a (retry fixed it)`, or `open` when no session has been saved yet. |
| Outage | First flag to the saved session (or retry recovery). Starred at 10 minutes or more. |

The header line above each table is part of the output: incident count, total downtime and share of the window, failed relogin count, the failed-step mix, and the times the scraper gave up ("scraper stops" = it waited 20 minutes for a session and stopped). Quote it in your summary instead of recomputing it. The tool does not show whether the scraper restarted after a stop; check that with `shed sls-logs --grep "identity refreshed"`.

In the events list:
- `stale-grid signal +N` means the balance rose by N but no new credit appeared in the transaction grid that cycle. It is the "possible missed deposit" path, and an on-demand visit normally follows within about 30 seconds.
- `first failure` is where the hang started, and it is followed by the invalidation.

Failure steps are labelled `account selector 25s`, `30s wait`, `login form 30s`, `login page 40s`, `Sign In 5s`, `page crash`, `get_attribute 30s`, `FC9220 goto timeout`.

## How to report

1. Summary first, in 2–3 lines: how many incidents, total downtime, the worst outage, and what it means. Then the table or events.
2. Paste each table exactly as printed, inside its code block, so the columns stay aligned. Show the footnote line once. Keep tables sorted the way the user asked, and keep ties in time order.
3. For cron-style checks, report only what is new since the previous check in 3–8 lines: page visits and whether `expires_at` moved about 2h ahead, relogins and their result, invalidated sessions with their session age, retries, stale-grid signals, and new WARNING/ERROR/CRITICAL. If nothing is new, say "no change" in one line.
4. When both environments are checked, add one sentence comparing them. If incidents start within a couple of minutes of each other, say so, but do not state a cause as fact.
5. Say what you did not check: missed deposits, whether the scraper restarted after a stop, the other environment when only one was pulled.

## Gotchas

- Log records hold many joined lines, so a record that mentions "password" is usually a neighbour of the line you want. The tool parses without dropping them and never includes raw text in its output.
- The pull stops at 20,000 lines, oldest first, so a very long window may truncate the newest events. Narrow the window if the last incident looks cut off.
- An incident with `open` in the table has no saved session yet and is still running.
- Attempt counts come from logs: relogin attempts are failures plus the one that saved, so an attempt interrupted without a failure line is not counted.
- Never print OTP digits, `Ref. No.`, or lines containing credentials or passwords, even if you look at raw `shed sls-logs` output to dig deeper.
