---
name: product-research
description: Use when the user wants to find, compare or watch product prices on Philippine marketplaces (Lazada, Shopee) — e.g. "how much is X on Lazada", "compare earbuds under 500", "cheapest Anker R60i", "watch the price of X", "is Shopee cheaper". Runs `shed product-search` and reports the listings; never scrapes by hand.
---

# Product research

`shed product-search` searches Lazada without any account and Shopee with a saved login, and returns listings
with price, rating, sold count, shop and link. Run it; do not open the sites yourself.

## Which flags

- Plain comparison: `shed product-search --query "<words>" --sort price-asc --json`.
  Lazada answers on its own. Shopee is skipped with a note unless the run has `--use-login`.
- Budget or range: add `--max-price N` and/or `--min-price N`. Popular first: `--sort sold`.
- More rows: `--limit 60` fetches a second page; the default 20 is one page.
- Shopee too: ask the user first, then add `--use-login`. If the note says "no saved session", tell the user to
  run `shed browser-fetch --login shopee` in a terminal (a window opens, they sign in, close it) and run again.
  Never try to sign in for them and never put `--use-login` in a cron job.
- Something looks off (zero rows, prices missing): `shed browser-fetch --url "<search url>" --text` shows what
  the browser saw; a note naming a saved HTML file means the site's markup changed.

## How to report

- Lead with the answer: cheapest match, its price, shop and link, then two or three alternatives.
- Quote prices as they came back (₱, two decimals) and say which source each is from.
- Mention a skipped or failed source in one line, with the command that fixes it.
- Treat sold counts and ratings as the site's own numbers; do not infer stock or legitimacy from them.

## Watching a price

Add a job to `~/.config/shed/cron.toml` with `tool = "product-search"`, `args = { query = "...", source = ["lazada"] }`
and a daily `every`. Each run is kept; `/view/<job>` on the dashboard shows first, lowest, highest and latest
price per product. Before pulling live, read `shed runs <job> --last` when a job already exists.
