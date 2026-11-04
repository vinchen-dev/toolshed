# Research tools: product-search, social-search and browser-fetch

Status: draft, 2026-11-04. Triage: ready-for-agent. No issue tracker is configured for this repo yet, so this file is the spec of record; move it to the tracker once `/setup-matt-pocock-skills` has run.

## Problem Statement

Vince wants two kinds of research from inside Claude Code and on a schedule: find and compare products across the Philippine marketplaces (Shopee, Lazada, and others later), and find what people are posting about a topic across social platforms and blogs. Today both are manual browsing. Results live only in a chat or a browser tab, cannot be re-run, compared over time or read by an agent, and the sites that matter most (Shopee, Facebook, Instagram, TikTok, X, Reddit) block logged-out automation outright.

The constraints are firm: no paid APIs, no scraping subscriptions, and no new accounts or developer sign-ups. The only credential allowed is Vince signing in to a site by hand, once, in a browser the tool keeps.

## Solution

Three tools in a new optional group named `research`, built the way every other shed tool is: one definition, exposed as CLI flags and as MCP tools, returning structured data that `shed cron` can record and the dashboard can show.

- `product-search` searches marketplaces for a query and returns normalized listings (title, price, shop, rating, sold count, link) across sources. Run on a schedule it becomes a price watch, with a dashboard page that shows how each product's price moved across kept runs.
- `social-search` searches platforms and blogs for a query and returns normalized posts (author, text, time, engagement, link). With a cursor file it reports only what is new since the last run, so a scheduled job becomes a topic watch. It can also watch a known page, channel or profile instead of a keyword.
- `browser-fetch` is the shared browser runner the other two use for sites with no public endpoint. It keeps one persistent Chromium profile per site. Anonymous sites run headless with nothing stored. Sites that need a login get a one-time manual sign-in in a visible window; later runs reuse that session until the site ends it, and the tool then says exactly which site needs a new login. It never stores or types a password.

Anonymous routes are the default and are the only ones a scheduled job should use. Logged-in routes are opt-in per run, meant for ad hoc questions, because automating a personal account on a schedule is how accounts get flagged.

## User Stories

1. As a developer, I want to search Lazada for a product from the terminal, so that I get a price-sorted list without opening a browser.
2. As a developer, I want the same search to cover Shopee once I have signed in there, so that I can compare both marketplaces in one result.
3. As a developer, I want each listing to carry its price, shop name, rating, sold count and link, so that I can judge an offer without clicking through.
4. As a developer, I want to cap results per source and filter by a price range, so that a broad query stays readable.
5. As a developer, I want to sort results by price or by sold count, so that I can find the cheapest or the most popular quickly.
6. As a developer, I want a source I cannot reach (no session, site changed, blocked) reported as a note on that source rather than failing the whole search, so that the other sources still answer.
7. As a developer, I want a strict mode where any unreachable source fails the run, so that a scheduled check cannot silently degrade.
8. As a coding agent, I want `--json` output with a stable shape, so that I can parse listings and posts without screen-scraping the terminal text.
9. As a coding agent, I want the tools over MCP with the same inputs as the CLI, so that I can call them from a Claude Code session without shell quoting.
10. As a developer, I want to search YouTube, Threads, Bluesky, Hacker News and Google News for a topic in one run, so that I get the cross-platform picture at once.
11. As a developer, I want a general web search platform with a site filter, so that I can find blog posts and whatever search engines have indexed from Facebook, TikTok, X or Reddit without logging in.
12. As a developer, I want to search Facebook, Instagram, TikTok, X and Reddit after signing in to each once, so that keyword search works on the platforms that block anonymous visitors.
13. As a developer, I want logged-in platforms to be skipped unless I pass an explicit opt-in flag, so that a scheduled job never touches my personal accounts by accident.
14. As a developer, I want every post to carry platform, author, text, time, likes, comments, shares or reposts, views where shown, and a link, so that posts from different platforms line up in one table.
15. As a developer, I want to limit results to a time window such as the last 24 hours, so that a topic watch shows recent posts only.
16. As a developer, I want a cursor file that makes a run report only posts I have not seen before, so that a scheduled job reads like a feed of what is new.
17. As a developer, I want the first run with a fresh cursor to apply the time window and seed the cursor, so that the first scheduled run shows the current state and later runs show only changes.
18. As a developer, I want to watch a public Facebook Page, a YouTube channel, a Bluesky account or a Threads profile by URL, so that I can follow a known source without a keyword.
19. As a developer, I want the Philippine TikTok trends page (hashtags with post and view counts) as a platform, so that I can see what is trending there without an account.
20. As a developer, I want results rendered newest first with truncated text and the link on each row, so that the terminal view is scannable.
21. As a developer, I want a one-time manual login per site in a visible browser window, so that I never give the tool a password.
22. As a developer, I want the tool to tell me when a site's session has expired and which command restores it, so that a failed run is one short step from working again.
23. As a developer, I want to list the browser profiles and whether each still looks signed in, so that I can fix sessions before a batch of ad hoc runs.
24. As a developer, I want to remove a site's browser profile, so that I can sign out of a site completely from the shed.
25. As a developer, I want browser profiles stored outside the repo with owner-only permissions, so that live session cookies never leave this machine or reach a commit.
26. As a developer, I want logged-in sites to open a visible browser by default and anonymous sites to stay headless, so that the sites that detect headless browsers still work and the rest stay out of my way.
27. As a developer, I want a flag to force headed or headless, so that I can watch a run when debugging an extractor.
28. As a developer, I want a failed extraction to save the page it could not parse and name that file in the error, so that fixing a broken site adapter starts from the real page.
29. As a developer, I want the tool to pace page loads and cap pages per run, so that a single search does not look like an attack on the site.
30. As a scheduled job, I want to run `social-search` every hour for a query on anonymous platforms with a cursor, so that the dashboard shows new posts as they appear.
31. As a scheduled job, I want to run `product-search` daily for a query on Lazada, so that kept runs form a price history.
32. As a dashboard viewer, I want a page for a product-search job that lists each product seen across runs with first seen, lowest, highest and latest price, so that I can spot a drop without reading every run.
33. As a dashboard viewer, I want the generic result view of a social-search job to show the latest posts as a table, so that no custom page is needed to read a topic watch.
34. As a coding agent, I want the recorded run of a job readable through `job-result` and `shed runs --json`, so that I report from stored data instead of re-running a search.
35. As a developer, I want a `product-research` skill, so that Claude knows which flags to use, how to compare offers and how to report them.
36. As a developer, I want a `social-research` skill, so that Claude knows which platforms are anonymous, when to ask me to sign in, how to dedupe across platforms and how to summarize.
37. As a developer, I want the `research` group to be removable like the PMS groups, so that machines that do not need it can delete two folders and keep the test gate green.
38. As a developer, I want the group served to Claude Code as its own MCP entry, so that a project can opt in without seeing the work tools.
39. As a developer, I want the new dependencies (Playwright, yt-dlp) imported lazily, so that `shed list` and `shed mcp` start as fast as today.
40. As a developer, I want the tools to work with no credential file at all, so that a fresh clone can run the anonymous half immediately.
41. As a developer, I want Manila timestamps on every post and listing, so that times line up with the rest of the shed.
42. As a developer, I want duplicate listings and posts within a source collapsed by link, so that paginated or infinite-scroll pages do not repeat rows.
43. As a developer, I want the design record and README to explain the research group and the browser session rule, so that the next change does not reopen these decisions.

## Implementation Decisions

### Group and layout

- A new group named `research` holds the three tools and their two skills. It follows the per-device rule from the design record: nothing outside the group imports it, deleting the group's tool and skill folders keeps pytest green, and it is registered in Claude Code as its own MCP entry with `shed mcp --group research`.
- `browser-fetch` is both a tool (login and profile management, raw page fetch for debugging) and the module the other two import for their browser adapters, the same way the Lark tools share one client module inside their group.
- The design record's goal section gains a sentence: the shed also holds research tools that agents run on Vince's behalf, and those tools follow the same contract. No change to the tool template, the host, the shared library or the `ToolContext`.

### Routes per source (all verified anonymously on 2026-11-04 unless noted)

Anonymous, cron-safe:
- Lazada PH: the catalog search page in a headless browser. Full result page with names and prices.
- YouTube: yt-dlp's search extractor with flat playlist output. Title, channel, link, view count. No API key. Channel listings in watch mode come back flat without dates or view counts, so the adapter fetches per-video metadata for the items it returns, bounded by the limit.
- Threads: the web search page in a headless browser. Post links, authors and text render without login.
- Bluesky: the public post search endpoint on the app view host (the other public host returns 403 for search). The since bound and latest-first sort were verified, and results carry engagement counts. The public author feed for watch mode was verified too.
- Hacker News: the Algolia search endpoint, with a numeric filter for the since bound.
- Google News: the RSS search feed, Philippine English edition.
- Web and blogs: DuckDuckGo's HTML results page in a headless browser, with an optional site filter. A plain request without a browser gets a challenge page, so this route is browser-only. Bing is not used: its RSS and result pages returned unrelated results to an automated visitor.
- TikTok trends: the Creative Center trends page for region PH. Hashtags with post and view counts. This is a separate platform value, not a post search.
- Facebook Pages: a public Page renders its recent posts without login behind a dismissable overlay. Used by watch mode, not by keyword search.

Logged-in, ad hoc only, opt-in per run (expected from how the sites behave for a signed-in user; not tested, since testing needs Vince's accounts):
- Shopee PH: the search page. The logged-out redirect carries an explicit not-logged-in flag, so a session is what the site wants. Headed by default; slider captchas are reported, never solved.
- Facebook: post search with the Posts tab. Highest detection risk of the group.
- Instagram: hashtag pages for `#tag` queries and keyword search otherwise.
- TikTok: the search page.
- X: search with the Latest tab.
- Reddit: web search. Reddit's public API is closed to new applicants and its RSS ends on 2026-11-13, so the website is the only route.

Not available under this policy and therefore skipped with a note: Instagram without a session, Shopee prices without a session, Reddit without a session.

### product-search

- Inputs: `query`; repeatable `source` with values `lazada` and `shopee`, defaulting to both; `limit` per source (default 20, paginates up to three pages when larger than one page); `min_price` and `max_price`; `sort` among relevance, price ascending, price descending and sold; `use_login` (default off, required for Shopee); `strict` (default off); `headed` tri-state override.
- Output: the query, the fetch time in Manila, and one block per source with its name, ok flag, a note when skipped or degraded, and its listings. A listing has source, title, price as a number, currency (PHP), link, shop, rating, sold count, seller location and image link, with the last five optional. Listings are deduplicated by link within a source.
- Render: one table per source, sorted by the chosen sort, price right-aligned, link last.
- Without `use_login`, Shopee is skipped with a note that names the opt-in flag and the login command. With `strict`, any skipped or failed source fails the run.
- A `view.py` gives product-search jobs a dashboard page: one row per product link seen across the job's kept runs, with first seen, lowest, highest and latest price and the latest title, sorted by biggest drop. Built from stored run records only; it never fetches.

### social-search

- Inputs: `query` or `url` (exactly one); repeatable `platform` with values `youtube`, `threads`, `bluesky`, `hackernews`, `news`, `web`, `tiktok-trends`, `facebook`, `instagram`, `tiktok`, `x`, `reddit`, defaulting to the anonymous keyword platforms; `site` (applies to `web`); `limit` per platform (default 20); `since` as a Manila time or a duration such as `24h` (default 24h); `cursor` path; `use_login`; `strict`; `headed`.
- With `url`, watch mode lists the recent posts of that page, channel, account or profile. Anonymous: a public Facebook Page, a YouTube channel (yt-dlp), a Bluesky account (public author feed), a Threads profile. Logged-in: Instagram, TikTok, X profiles and a subreddit. The platform is inferred from the URL's host.
- Output: the query or url, the window, and one block per platform with its name, ok flag, note and posts. A post has platform, link, author, author link, text (title plus body for Hacker News and news), created time in Manila when the platform shows one, likes, comments, shares or reposts, views, and hashtags, with everything after the link optional. Posts are deduplicated by link within a platform.
- Cursor semantics mirror log-watch: the cursor file is JSON keyed by platform and query (or url), holding a bounded set of seen links (oldest dropped past 5,000) and the newest post time seen. The first run with a fresh cursor applies the `since` window and seeds the file; later runs return only unseen posts and update the file. Platforms that show no timestamps (web, Threads where absent) rely on the seen set alone. The cursor path resolves against the working directory like log-watch's.
- Render: per platform, newest first, columns time, author, engagement, truncated text, link. The TikTok trends platform renders rank, hashtag, posts and views.
- Logged-in platforms without `use_login` are skipped with a note naming the flag and the login command; with `use_login` but no live session they fail that platform with the login command, or the run with `strict`.

### browser-fetch

- Inputs: `login` (a site name: opens a visible window at the site's sign-in page and waits until the window is closed or the site's logged-in marker appears), `status` (lists profiles with last login time and whether a cheap probe still finds the logged-in marker), `forget` (deletes a site's profile), and a debugging fetch with `url`, optional `site` to pick a profile, `headed` override and `text` (return the page's visible text) so an extractor can be developed against a live page. The debugging fetch also takes `js` (a function whose result is returned instead of the page) and `scrolls`, so an extractor can be tried against the live page before it goes into a tool.
- One persistent Chromium profile per site under the shed config folder, created with owner-only permissions. Anonymous sites (Lazada, Threads, DuckDuckGo, Facebook Pages in watch mode, TikTok trends) use a throwaway context and store nothing.
- Each site adapter declares: its search URL pattern, its logged-in marker, its login-wall and bot-challenge detection, its default headed or headless mode, its result extractor and its pagination or scroll strategy. Detection of a login wall or a challenge raises a `ToolError` that names the site and the exact login command; the tool never fills credentials and never attempts to pass a challenge.
- Politeness: a minimum delay between page loads per profile, human-paced scrolling for infinite-scroll pages, and a hard cap on pages per run. Values are adapter constants, not flags.
- On an extraction failure, and when an extractor returns no records at all (the usual sign of changed markup), the page's HTML is written under the shed state folder and the error names that file; the source or platform is reported as failed with that note.
- Playwright is added as a project dependency; Chromium is installed once per machine with Playwright's install command and the README says so. The import happens inside the transport seam, never at module import.

### Shared conventions

- yt-dlp is a project dependency used as a library through the transport seam, not shelled out to.
- HTTP goes through the project's existing httpx dependency. RSS and XML parse with the standard library. No new parsing dependencies.
- No credential files. The env loader is not used by this group.
- Every time is parsed and rendered with the shed's Manila helpers.
- Skills: `product-research` (when to run, flags, how to compare and report offers, when to ask Vince to sign in to Shopee) and `social-research` (which platforms are anonymous, when to ask for a sign-in, cursor use for watches, deduping across platforms, how to summarize). Prose only; they point at the tools.
- README gains usage lines for the three tools, a note on the browser profiles and the login rule, and the Chromium install step. The design record gains a section recording the research group, the anonymous-by-default rule, the opt-in flag for logged-in platforms, the reason scheduled jobs never use logins, and the rejected routes (Bing, paid scrapers, developer accounts) with why.

## Testing Decisions

- A good test drives a tool through its `run` function with a stub context and asserts on returned data and rendered text, never on how an adapter parsed a page. Tests must pass offline and without a browser installed.
- One transport seam per tool. Each tool exposes one module-level transport object with a small set of methods: fetch JSON, fetch text, open a page through a site adapter and return its extracted records, and run a yt-dlp search. Tests replace that object with a fake that serves fixtures recorded from the real sites and endpoints on 2026-11-04, trimmed to a few items. This is the same idea as the Lark tools' `make_client` seam and log-watch's frozen clock, raised one level so a single fake covers every adapter.
- Fixtures: one saved page or response per source, in the tool's own folder, the way bdo-incidents keeps its SLS exports. Each fixture is small and has the identifying fields a test asserts on.
- product-search tests: Lazada listings normalize with price as a number and dedupe by link; price filters and sorts; Shopee skipped with a note without the opt-in flag; Shopee login wall becomes a `ToolError` naming the login command; strict mode fails the run; render shows one table per source; the view page computes first, lowest, highest and latest price from several stored runs.
- social-search tests: each anonymous platform normalizes its fixture into posts with the expected fields; `since` filters by created time; the cursor seeds on the first run and returns only unseen posts on the second, bounded set included; logged-in platforms skip without the flag and fail with the login command when the session marker is missing; watch mode infers the platform from the URL and rejects both `query` and `url` together; the trends platform renders rank, hashtag, posts and views; render is newest first.
- browser-fetch tests: profile folders are created with owner-only permissions and removed by `forget`; `status` reports a profile as signed out when the probe misses the marker; a challenge page raises the login error; the extraction-failure path writes the page under the state folder and names it. The browser itself is behind the seam, so no test launches Chromium.
- Host tests are untouched: the host depends on `base` only, and deleting the research group must keep pytest, pyright and ruff green.
- Prior art: bdo-incidents (fixtures and parse tests, and its view tests that build a page from several stored runs), log-watch (frozen clock, cursor first-run and second-run tests, `ToolError` paths), lark-doc (seam patched with monkeypatch), and the host's dashboard test that serves a tool's view page.

## Out of Scope

- Any paid API or scraping service, and any free tier that needs a sign-up: SerpApi, Tavily, Apify, Brave, a YouTube API key, a Threads or Instagram developer app, the Shopee affiliate program. The transport seam leaves room for them later.
- Storing passwords, automatic re-login, solving captchas or passing bot challenges, and any scheduled use of logged-in profiles.
- Reddit, X or TikTok through their APIs (closed, paid or academic-only), Facebook keyword search without a login, Instagram without a login, live Shopee prices without a login.
- Bing as a search backend, Google result pages, a self-hosted SearXNG.
- Marketplaces beyond Lazada and Shopee (Amazon, eBay, AliExpress, Google Shopping), and price alerts or notifications; the dashboard page is the alert.
- Enriching arbitrary post URLs through X's and TikTok's public embed endpoints (verified to work; a candidate follow-up).
- A custom dashboard page for social-search; the generic result view is enough.
- Changes to the host, the tool template, the shared library or the `ToolContext`.

## Further Notes

- Every anonymous route above was exercised on 2026-11-04 from this machine with a headless Chromium or a plain request. Shopee search, Reddit search and subreddit pages, TikTok search, X search, Facebook post search, Instagram hashtag pages and Meta's Ad Library all blocked or redirected a logged-out visitor. Lazada, Threads, Bluesky, Hacker News, Google News RSS, DuckDuckGo in a browser, the TikTok Creative Center trends page and a public Facebook Page all answered.
- Sites change their markup without notice. Each browser adapter is expected to break a few times a year; the saved-page-on-failure rule and the per-adapter fixture exist to make each fix short.
- Automating a personal account breaks those sites' terms and risks the account. The opt-in flag, the ad hoc rule for logged-in platforms and the skills' wording are there so the risk is taken knowingly and never by a scheduled job.
- Suggested build order: social-search with the anonymous platforms first (the stable half), then browser-fetch with Lazada and the login machinery, then product-search and its view, then the logged-in adapters one site at a time.
- Decisions were taken in the 2026-11-04 session: free routes only, then no accounts or payment, then anonymous plus Vince's own browser logins; hybrid adapter split; one transport seam per tool; spec kept in the repo until a tracker exists.
