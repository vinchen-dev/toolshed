---
version: 1
slug: "src-shed-ui-py"
primary_target: "src/shed/ui.py"
related_targets: ["tools/bdo-incidents/view.py"]
---

# Dashboard (shed ui): Jobs, job and run pages, Tools, tool views

Scope: the whole localhost dashboard served by `src/shed/ui.py`, plus the BDO sessions view in `tools/bdo-incidents/view.py`. Visitor mode: Operate.

Audience and job: Vince, on a second screen next to dark terminals, in a browser tab on demand, and sometimes on a phone. First question on every open: is anything failing or is everything healthy, then the stats behind it. Tables must stay dense and scannable (confirmed). Constraint kept from the engineering record: stdlib server, inline CSS, a few lines of script.

Chosen direction: the category canon named by the user in plain words (Linear, Vercel, Raycast); their craft level is the bar, executed without irony or added quirk. No roll was run.

## Direction contract

THESIS: Every page answers "anything failing?" before it shows anything else, in the Linear/Vercel/Raycast product idiom played straight. It refuses the grey-screen-of-tables default: real elevation, a health strip, and controls that look like the best product in the category.

OWN-WORLD: Layered neutral surfaces (tinted ground, white or near-black panels, hairline borders, soft offset shadows), Inter at 13.5px with tabular numerals, one indigo accent for interaction and selection, state colour (green, red, amber) only where it carries meaning, status pills with a dot, segmented controls for filters, 6 to 10px radii, 150 ms transitions, light and dark from the Mac setting with a three-way theme switch.

STORY: Open the page, read the strip (All healthy, or "1 failing: bdo-session"), glance at each job's card, click into history or the BDO page where QAT and PROD stat tiles sit above the incident table and the stitched timeline.

FIRST VIEWPORT: 48px top bar: house mark + tool-shed, tabs Jobs / Tools / BDO sessions with the current one underlined in accent; right: scheduler pill, refreshed stamp, theme button. Under it a full-width health strip: status dot, headline, job count, last run, live countdown to the next. Then "Jobs" and the card grid; each card: name, status pill, tool and interval, last summary, footer with last/next and the primary "Run now" button.

FORM: Category canon, the standing exit taken by the user in plain words; position: user-pinned, not from an ordered list. The roll was run afterwards for the record only (assigned index 5, six challengers dealt, none built because the pin beats the roll). seed:be56ca5f40a4f07b

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
