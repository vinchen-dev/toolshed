---
name: social-research
description: Use when the user wants to know what people are posting about a topic, brand or product across social platforms, news and blogs — e.g. "what are people saying about X", "find posts about Y", "any news on Z this week", "watch mentions of our brand", "what's trending on TikTok PH", "latest posts from this Facebook page". Runs `shed social-search` and summarizes; never browses the platforms by hand.
---

# Social research

`shed social-search` searches YouTube, Threads, Bluesky, Hacker News, Google News and the web (DuckDuckGo)
without any account, and Facebook, Instagram, TikTok, X and Reddit with a saved login. Run it; do not open the
platforms yourself.

## Which flags

- Topic sweep: `shed social-search --query "<words>" --since 7d --json`. The default platforms are the
  anonymous ones. Add `--platform news --platform youtube` to narrow, `--limit 50` for more per platform.
- Facebook, TikTok, X or Reddit crumbs without a login: `--platform web --site facebook.com` (what search
  engines indexed; partial and days behind).
- TikTok trends in the Philippines: `--platform tiktok-trends`.
- A known page or account: `--url https://www.facebook.com/<page>` (no login needed for a public Page), or a
  YouTube channel, Bluesky or Threads profile URL.
- Logged-in platforms: ask the user first, then add `--use-login --platform facebook` (or instagram, tiktok, x,
  reddit). A note saying "no saved session" means the user must run `shed browser-fetch --login <site>` in a
  terminal, sign in, close the window, and run again. Never sign in for them; never schedule `--use-login`.
- Repeated checks: `--cursor <file>` makes each run return only posts not seen before.

## How to report

- Lead with the picture: how much is being said, the dominant sentiment or theme, then the notable posts.
- Group by platform, newest first, each as: time or "when" label, author, one line of text, link.
- The same story often appears on several platforms (a news article, its YouTube version, the Threads post
  linking it). Mention it once and list the other links under it instead of counting it again.
- Posts with no time are not undated news; many platforms hide the timestamp from logged-out visitors.
- Say which platforms were skipped or failed, in one line each, with the command that fixes it.
- Do not quote more than a sentence of a private person's post; link instead.

## Watching a topic

Add a job to `~/.config/shed/cron.toml` with `tool = "social-search"`, `args = { query = "...", cursor = "~/.local/state/shed/<name>.cursor" }`
and an hourly `every`. Keep `use_login` out of it. `shed runs <job> --last` shows the newest posts; read it
before pulling live when a job already covers the topic.
