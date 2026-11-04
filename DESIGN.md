---
name: tool-shed dashboard
description: Localhost job dashboard in the Linear / Vercel / Raycast idiom, played straight; health first, dense tables, one indigo accent.
colors:
  ground: "#fafafa"
  surface: "#ffffff"
  surface-sunken: "#f4f4f5"
  ink: "#18181b"
  ink-muted: "#6b6b76"
  hairline: "#e4e4e7"
  hairline-strong: "#d4d4d8"
  accent: "#5e6ad2"
  accent-strong: "#4f5bc4"
  accent-wash: "#eef0fb"
  on-accent: "#ffffff"
  ok: "#15803d"
  ok-wash: "#dcfce7"
  fail: "#b91c1c"
  fail-wash: "#fee2e2"
  warn: "#b45309"
  warn-wash: "#fef3c7"
  ground-dark: "#09090b"
  surface-dark: "#121214"
  surface-sunken-dark: "#1c1c1f"
  ink-dark: "#ededf0"
  ink-muted-dark: "#8b8b93"
  hairline-dark: "#232326"
  hairline-strong-dark: "#2e2e32"
  accent-dark: "#7c86ff"
  accent-strong-dark: "#929aff"
  accent-wash-dark: "#7c86ff1f"
  on-accent-dark: "#0b0b14"
  ok-dark: "#4ade80"
  ok-wash-dark: "#14532d55"
  fail-dark: "#f87171"
  fail-wash-dark: "#7f1d1d55"
  warn-dark: "#fbbf24"
  warn-wash-dark: "#78350f55"
  brand-roof: "#b45309"
  brand-door: "#fde68a"
  brand-outline: "#78350f"
typography:
  headline:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "18px"
    fontWeight: 600
    letterSpacing: "-0.012em"
  title:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 600
    letterSpacing: "-0.005em"
  subtitle:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "13px"
    fontWeight: 600
  minor:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "12.5px"
    fontWeight: 600
  body:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "cv11, ss01"
  table:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.4
  stat:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "16px"
    fontWeight: 600
    letterSpacing: "-0.01em"
  label:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "12px"
    fontWeight: 500
  pill:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "11.5px"
    fontWeight: 600
    lineHeight: 1.6
    letterSpacing: "0.01em"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.45
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
rounded:
  xs: "4px"
  seg-inner: "5px"
  button: "6px"
  seg: "7px"
  md: "8px"
  card: "10px"
  pill: "999px"
spacing:
  s1: "4px"
  s2: "8px"
  s3: "12px"
  s4: "16px"
  s6: "24px"
  s8: "32px"
components:
  button-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.table}"
    rounded: "{rounded.button}"
    padding: "3px 11px"
    height: "28px"
  button-default-hover:
    backgroundColor: "{colors.surface-sunken}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    typography: "{typography.table}"
    rounded: "{rounded.button}"
    padding: "3px 11px"
    height: "28px"
  button-primary-hover:
    backgroundColor: "{colors.accent-strong}"
  button-copy:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.pill}"
    rounded: "{rounded.button}"
    padding: "1px 8px"
  button-theme:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    rounded: "{rounded.button}"
    padding: "0"
    size: "28px"
  pill-ok:
    backgroundColor: "{colors.ok-wash}"
    textColor: "{colors.ok}"
    typography: "{typography.pill}"
    rounded: "{rounded.pill}"
    padding: "1px 8px 1px 7px"
  pill-fail:
    backgroundColor: "{colors.fail-wash}"
    textColor: "{colors.fail}"
    typography: "{typography.pill}"
    rounded: "{rounded.pill}"
    padding: "1px 8px 1px 7px"
  pill-running:
    backgroundColor: "{colors.warn-wash}"
    textColor: "{colors.warn}"
    typography: "{typography.pill}"
    rounded: "{rounded.pill}"
    padding: "1px 8px 1px 7px"
  pill-off:
    backgroundColor: "{colors.surface-sunken}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.pill}"
    rounded: "{rounded.pill}"
    padding: "1px 8px 1px 7px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "12px 16px"
  card-hover:
    backgroundColor: "{colors.surface}"
  seg:
    backgroundColor: "{colors.surface-sunken}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.minor}"
    rounded: "{rounded.seg}"
    padding: "2px"
  seg-selected:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.seg-inner}"
    padding: "1px 9px"
  nav:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    height: "48px"
  nav-link-current:
    textColor: "{colors.ink}"
  strip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.table}"
    padding: "7px 24px"
  table-header:
    backgroundColor: "{colors.surface-sunken}"
    textColor: "{colors.ink-muted}"
    typography: "{typography.label}"
    padding: "6px 12px"
  table-cell:
    textColor: "{colors.ink}"
    typography: "{typography.table}"
    padding: "6px 12px"
  pre:
    backgroundColor: "{colors.surface}"
    typography: "{typography.mono}"
    rounded: "{rounded.md}"
    padding: "10px 12px"
  code:
    backgroundColor: "{colors.surface-sunken}"
    typography: "{typography.code}"
    rounded: "{rounded.xs}"
    padding: "0 5px"
  error:
    backgroundColor: "{colors.fail-wash}"
    rounded: "{rounded.md}"
    padding: "10px 12px"
  notice:
    backgroundColor: "{colors.warn-wash}"
    rounded: "{rounded.md}"
    padding: "10px 12px"
  empty:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
    padding: "16px 24px"
    width: "760px"
  details-tool:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
    padding: "8px 16px"
---

# Design System: tool-shed dashboard

## Overview

**Creative North Star: "The Status Board Next to the Terminals"**

A one-machine operations dashboard that sits beside dark terminals and must answer "is anything failing?" before it shows anything else. It takes the Linear / Vercel / Raycast product idiom and plays it straight: layered neutral surfaces, hairline borders, soft ambient shadows, a single indigo accent reserved for interaction and selection, and state colour (green, red, amber) only where it carries meaning. No irony, no added quirk, no decoration that does not carry a reading.

Density is the point. Body copy runs at 13.5px Inter with tabular numerals, tables at 13px with 12px muted headers, and the whole page is a stack of hairline-separated, sticky-headed, scannable regions. Elevation is real but quiet: panels sit one pixel-shadow above the ground and lift to a 14px blur on hover. Light and dark follow the Mac setting with a three-way switch; the dark theme is a true near-black, not a dimmed light theme.

The scale is small and fixed: six spacing steps (4 to 32px), radii from 6px (buttons) to 10px (cards), one transition duration (150ms), one animation (a 1.2s opacity pulse on anything running). Everything is served from one stdlib `http.server` page with inline CSS; the system has to stay expressible as a single string.

**Key Characteristics:**
- Health first: a full-width strip under the nav states failing / running / healthy before any content.
- Layered neutrals: tinted ground, white or near-black panels, hairline borders, ambient shadows.
- One accent (indigo) for interaction, selection and focus; state colours only on pills, dots, strips and notices.
- Inter at 13.5px with `cv11` + `ss01` and tabular numerals everywhere a number can change.
- Status pills with a leading dot; segmented controls for filters; sticky table headers.
- 150ms transitions, a single 1.2s pulse, both killed under reduced motion.
- Light and dark from the OS, overridable per browser (`shed-theme` in localStorage).

## Colors

A near-achromatic zinc ramp carries the page; one indigo and three state hues do all the signalling.

### Primary
- **Indigo** (`{colors.accent}` light / `{colors.accent-dark}` dark): the only chromatic interaction colour. Primary button fill, the 2px underline on the current nav tab, the 2px focus ring, the caret, and text selection (accent at 28% via `color-mix`). `accent-strong` is the primary button hover; `accent-wash` is declared but unused by any shipped rule.

### Neutral
- **Ground** (`{colors.ground}` / `{colors.ground-dark}`): page background. The tint is what makes white panels read as raised.
- **Surface** (`{colors.surface}` / `{colors.surface-dark}`): cards, tables, `pre`, buttons, the health strip, the empty state, the selected segment. The nav is surface at 86% over a 10px backdrop blur.
- **Surface sunken** (`{colors.surface-sunken}` / `{colors.surface-sunken-dark}`): table headers, hovered table rows, the segmented-control track, inline `code`, off/never pills, button hover.
- **Ink** (`{colors.ink}` / `{colors.ink-dark}`): all primary text; hover state of muted links and nav tabs.
- **Ink muted** (`{colors.ink-muted}` / `{colors.ink-muted-dark}`): nav tabs at rest, table headers, metadata, `h4`, `dt`, stat captions, summaries, the strip's stats, dimmed "visit" rows.
- **Hairline** (`{colors.hairline}` / `{colors.hairline-dark}`): every border at rest (cards, tables, nav bottom, strip bottom, `pre`, `code`, segmented track).
- **Hairline strong** (`{colors.hairline-strong}` / `{colors.hairline-strong-dark}`): button borders, hovered or open card borders, the dashed empty-state border, the `.item` left rule, the scrollbar thumb, link underlines at rest.

### State
- **Ok green** (`{colors.ok}` on `{colors.ok-wash}`): ok pills, the strip dot when all healthy, "scheduler on".
- **Fail red** (`{colors.fail}` on `{colors.fail-wash}`): failed pills, the strip dot and headline when anything is failing, `.error` boxes (border at 40%).
- **Warn amber** (`{colors.warn}` on `{colors.warn-wash}`): running pills and the running strip dot (both pulse), `.notice` boxes. In this build amber means "in progress", never "warning".
- **Off** (`{colors.ink-muted}` on `{colors.surface-sunken}`): never-run, disabled, scheduler-off, and routine "visit" timeline rows.

### Brand
- **House mark** (`{colors.brand-roof}`, `{colors.brand-door}`, `{colors.brand-outline}`): the favicon and the 18px mark in the nav brand. These three are hard-coded in the SVG and do not change with theme; they are the only non-token colours on the page.

### Named Rules
**The One Accent Rule.** Indigo appears only on things you can act on or have selected: the primary button, the current tab, the focus ring, the caret, selection. Never on headings, borders at rest, or data.

**The Meaning-Only State Rule.** Green, red and amber are set on pills, strip dots, `.error` and `.notice` and nowhere else. A number is never coloured; its pill is.

**The Theme Pair Rule.** Every colour token has a light and a dark value and both themes share every rule; the dark theme is a different palette under the same CSS, not a filter. A new colour must be added to all three blocks (`:root`, `prefers-color-scheme: dark`, `[data-theme="dark"]`).

## Typography

**Display Font:** none; Inter carries headings.
**Body Font:** Inter (self-hosted variable latin subset at `/static/inter.woff2`, weights 100–900, `font-display: swap`), falling back to `system-ui, -apple-system, "Segoe UI", sans-serif`.
**Label/Mono Font:** `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` for `pre` and `code` only.

**Character:** Small, tight and numerate. Inter at 13.5px with `cv11` (single-storey a) and `ss01` (open digits) and antialiased smoothing; headings are the same face a notch heavier with slightly negative tracking, never larger than 18px. Tabular numerals are set on everything that holds a changing number (strip, nav clock, stats, card footers, `dl.facts`, `.num` and `.time` cells).

### Hierarchy
- **Headline** (600, 18px, -0.012em): the page `h1` only ("Jobs", a job name, "BDO sessions"); carries an inline muted 13px/500 `small` for counts or the parent job.
- **Title** (600, 14px, -0.005em): `h2` section heads ("Latest result", "History", "Incidents", "Timeline") with a 12.5px/400 muted `small`; also the card name and nav brand (brand at -0.01em).
- **Subtitle** (600, 13px): `h3`, used for env names and top-level result keys.
- **Minor** (600, 12.5px, muted): `h4` for nested result keys and "args"/"examples".
- **Body** (400, 13.5px / 1.5): everything else; the nav right cluster, card footers and `h2 small` drop to 12.5px.
- **Table** (400, 13px / 1.4): `table.data` cells and the health strip. Keys at 500.
- **Stat** (600, 16px, -0.01em, tabular): the big number in a stat tile, with a 12px muted caption.
- **Label** (500, 12px, no transform): table headers, muted on sunken background, sticky.
- **Pill** (600, 11.5px / 1.6, +0.01em): status pills and the copy button; the only positive tracking in the system.
- **Mono** (12.5px / 1.45 in `pre`; 12px in `code`).

### Named Rules
**The 18px Ceiling Rule.** Nothing on the page is set larger than 18px. Hierarchy comes from weight (600 vs 400 vs 500), colour (ink vs muted) and tracking, not size.

**The Tabular Number Rule.** Any element whose number can change between refreshes gets `font-variant-numeric: tabular-nums` so it does not shimmy.

## Layout

A single centred column of at most 1440px. `main` pads `16px 24px 32px`; the sticky nav and the health strip run full-bleed with their inner content aligned to the same 1440px column via `max(24px, calc((100% - 1440px) / 2 + 24px))`.

The vertical stack on every page: 48px nav (sticky, `z-index: 2`, blurred surface), health strip (7px vertical padding, one line, wraps), then `h1` with 8px above / 4px below, `.meta` row (metadata and controls, `4px 16px` gap, 16px below), then sections headed by `h2` with 24px above and 8px below.

Cards tile in an `auto-fill, minmax(320px, 1fr)` grid with a 12px gap. Stat tiles inside a card are a fixed two-column grid with `8px 12px` gap. `dl.facts` is a wrapping flex of `dt dd` pairs with a `4px 24px` gap. Tables live inside `.scroll`, which caps height at `calc(100vh - 150px)` and scrolls both axes with a thin themed scrollbar; headers stay sticky inside it. Nested result items indent 12px behind a 1px strong-hairline left rule.

Spacing is the six-step scale `s1 4 / s2 8 / s3 12 / s4 16 / s6 24 / s8 32`. Component-internal paddings use small literals off the scale (1, 2, 3, 6, 7, 9, 10, 11px) and that is the build; do not promote them to tokens.

**Responsive (one breakpoint, `max-width: 640px`):** `main` tightens to `12px 16px 24px`; nav and strip pad 16px; nav drops to 44px and the right-hand cluster wraps to its own full-width row (`6px 0 8px`); tab padding halves to 8px; the strip drops to 12.5px with its headline taking a full line; `.scroll` loses its max-height so the page scrolls as one.

## Elevation & Depth

A hybrid: tonal layering does the structure, two ambient shadows do the lift. The tinted ground (`ground`) makes white `surface` panels read as raised before any shadow is applied; hairline borders define edges; `surface-sunken` marks regions that sit below the panel plane (table headers, hovered rows, the segmented track, inline code). Shadows are soft, centred and low-alpha in light, and much darker in dark where the border carries most of the edge.

### Shadow Vocabulary
- **Rest** (`--shadow-1: 0 1px 2px rgba(0,0,0,.05)`; dark `.5` alpha): cards, buttons, `.scroll` tables, `pre`, the selected segment, `details.tool`.
- **Lift** (`--shadow-2: 0 4px 14px rgba(0,0,0,.08)`; dark `.5` alpha): hovered cards, open or targeted tool details.
- **Dot halo** (`0 0 0 3px color-mix(in srgb, currentColor 18%, transparent)`): the 8px health-strip dot's soft ring, in the dot's own state colour.
- **Nav glass** (`color-mix(in srgb, var(--surface) 86%, transparent)` + `backdrop-filter: blur(10px)`): the only translucency.

### Named Rules
**The Two Shadows Rule.** There are exactly two box-shadows, rest and lift, and the only transition between them is hover or open. Buttons drop their shadow entirely on `:active`; nothing ever gets a third, larger shadow.

**The Border Carries Dark Rule.** In dark mode shadows go to 50% black but the border steps from `hairline` to `hairline-strong` on hover; the edge, not the glow, is what reads.

## Shapes

Softly rounded rectangles on a small, stepped radius scale: 4px for inline `code` and the focus ring, 5px for a pressed segment inside a 7px track, 6px for buttons, 8px (`--radius`) for tables, `pre`, `.error` and `.notice`, 10px for cards, the empty state and tool details, and a full 999px pill for status. The bigger the container, the bigger the radius; nothing exceeds 10px except pills and circles.

Edges are 1px hairlines everywhere; the empty state is the one dashed border (strong hairline). The current nav tab is underlined with a 2px accent bar flush to the nav's bottom border. The health-strip dot is an 8px circle with a 3px halo; the pill dot is a 6px circle in `currentColor`. The disclosure chevron is a 14px masked SVG stroke in `currentColor` that rotates 90° when open. The theme button is a 16px half-filled circle. Text selection is tinted indigo.

## Components

Quiet, precise, product-grade; every control looks like it belongs in a Mac app's settings, not a web form.

### Buttons
- **Shape:** softly rounded (6px), 28px tall, `3px 11px` padding, 13px/500 Inter, rest shadow.
- **Default:** surface fill, strong-hairline border, inherits text colour. Hover: sunken surface. Active: shadow removed. Focus: 2px indigo ring, 2px offset.
- **Primary (`.primary`):** indigo fill, white (light) or near-black (dark) text, transparent border. Hover: `accent-strong`. Only "Run now" uses it.
- **Copy (`.copy`):** inline inside a `summary` or `h4`, `1px 8px`, 11.5px, no shadow, no min-height; swaps its label to "copied" for 1.2s.
- **Theme (`.theme`):** 28px square icon button, muted at rest, ink on hover; cycles system / light / dark and updates its own `title`.
- **Transitions:** background, border-color, box-shadow at 150ms.

### Pills (status chips)
- **Style:** 999px, `1px 8px 1px 7px`, 11.5px/600 with +0.01em, a 6px leading dot in `currentColor`, `white-space: nowrap`, vertically middle-aligned so it sits inside an `h1` or a table cell.
- **States:** `ok` (green on green wash), `fail` (red on red wash), `running` (amber on amber wash, dot pulses 1.2s), `never` and `off` (muted on sunken). The BDO timeline maps event kinds onto the same four: FLAG / FAIL / SCRAPER_STOP → fail, LOC / STALE → running, RETRY_OK / SAVED → ok, VISIT → off; any row whose pill is `off` has its whole text dimmed to muted (`tr:has(.pill.off)`).

### Cards
- **Corner Style:** 10px.
- **Background:** surface, hairline border, rest shadow; `12px 16px` padding.
- **Hover:** border to strong hairline, lift shadow, 150ms.
- **Anatomy (job card):** `header` flex with the 14px/600 name on the left and a status pill on the right; a muted line (tool · interval · view link); `.summary` paragraph; `footer` of 12.5px muted tabular text and the primary button, wrapping.
- **Anatomy (env card):** same shell with a `.stats` 2×2 tile grid (16px/600 number over 12px muted caption) and a muted "last event" line.
- **Grid:** `auto-fill, minmax(320px, 1fr)`, 12px gap.

### Segmented control (`.seg`)
- **Style:** inline-flex track on sunken surface with a hairline border, 7px radius, 2px inner padding, 4px horizontal margin; segments are `1px 9px`, 12.5px/500, 5px radius, muted text.
- **State:** the current value is a `<b>` on surface with ink text and the rest shadow (a raised tab); other segments are links that go to ink on hover. Used for the BDO window (1h / 6h / 24h / 48h) and env (both / qat / prod) filters.

### Navigation
- **Style:** sticky 48px bar (44px on phones), blurred surface at 86%, hairline bottom border; content aligned to the 1440px column.
- **Brand:** 18px house mark + "tool-shed" at 14px/600, -0.01em, 8px gap, 12px right margin, no underline.
- **Tabs:** muted text, `0 12px` padding, stretch to full height, 2px transparent bottom border. Hover: ink. Current (`aria-current="page"`, set by script from the path): ink text with an indigo 2px bottom border.
- **Right cluster:** scheduler pill, "refreshed HH:MM:SS, every N s" in 12.5px muted tabular, theme button; wraps to its own row under 640px.

### Health strip (signature)
One line directly under the nav, full width, surface background with a hairline bottom border, `7px` vertical padding, 13px muted tabular text. Leftmost an 8px dot with a 3px halo in its state colour, then a 600-weight ink headline, then plain stats (job count, last run and duration, a live countdown ticked every second). States: `ok` green "All healthy"; `fail` red dot and red headline "N failing: names"; `running` amber pulsing dot "running names"; `off` muted dot "N of M not run yet" or "No jobs yet". Failing wins over running wins over never-run.

### Metadata row (`.meta`) and facts (`dl.facts`)
Wrapping flex rows of muted text with ink 600 values (`.meta b`) or 500 tabular `dd` values; used for tool / interval / last run / next, run arguments and job settings. Gaps `4px 16px` and `4px 24px`.

### Data table (`.scroll` + `table.data`)
- **Container:** hairline border, 8px radius, surface, rest shadow, scrolls both axes, capped at `100vh - 150px` (uncapped on phones).
- **Header:** sticky, sunken surface, 12px/500 muted, nowrap, `6px 12px`.
- **Cells:** 13px / 1.4, `6px 12px`, hairline bottom border, top-aligned; last row borderless so the container's radius stays clean. `.num` right-aligned tabular, `.time` nowrap tabular, `.key` nowrap 500.
- **Hover:** whole row to sunken surface. Rows whose pill is `off` are muted.

### Code (`pre`, `code`)
`pre`: surface, hairline border, 8px radius, `10px 12px`, rest shadow, 12.5px / 1.45 mono, scrolls. `code`: sunken surface, hairline border, 4px radius, `0 5px`, 12px mono.

### Messages (`.error`, `.notice`, `.empty`)
`.error` and `.notice`: 8px radius, `10px 12px`, 12px vertical margin, state wash background with the state colour at 40% as border (fail / warn). `.empty`: dashed strong-hairline border, 10px radius, `16px 24px`, surface, max 760px wide; holds a short instruction and often a `pre`.

### Disclosure (`details`, `details.tool`)
`summary`: muted, pointer, native marker hidden, 14px masked chevron that rotates 90° on open, ink on hover, 6px gap; used for "Text output", "Tool log", "Result as JSON". `details.tool` wraps each tool in the catalog as a card (10px, `8px 16px`, surface, rest shadow) that lifts and strengthens its border when open or `:target`.

### Motion
One duration, 150ms, on colour, border-color, box-shadow, text-decoration-color and the chevron transform; one keyframe, `pulse` (opacity to .25 at 50%, 1.2s ease-in-out, infinite) on running dots; smooth anchor scrolling. `prefers-reduced-motion: reduce` sets `scroll-behavior: auto` and kills every transition and animation with `!important`.

## Do's and Don'ts

### Do:
- **Do** put the health answer first: any new page keeps the nav and the strip and opens with an `h1` + `.meta` row before content.
- **Do** build new controls from the existing primitives: pill for state, `.seg` for a filter, default button for actions, `.primary` for the one action that runs something.
- **Do** keep tables in `.scroll` > `table.data` with the 12px sticky header and 13px cells; density is a product commitment.
- **Do** use `tabular-nums` on any number that changes, and the `.num` / `.time` / `.key` cell classes.
- **Do** add every new colour to all three theme blocks and check it on `desktop-bdo-dark.png`-style dark captures.
- **Do** stay inside the six-step spacing scale and the 4 / 6 / 8 / 10px radius ladder (999px for pills).
- **Do** keep state colour on the pill, dot, strip or message box, with the hue's wash behind it.

### Don't:
- **Don't** set any text above 18px or use a second typeface; hierarchy is weight and colour.
- **Don't** colour headings, borders at rest, or data cells with the accent or a state hue.
- **Don't** add a third shadow, a hard-offset shadow, a gradient, or an image background; the lift stops at `--shadow-2`.
- **Don't** introduce a motion longer than 150ms or a second animation; the pulse is the only ambient movement and it must stay inside the reduced-motion kill.
- **Don't** use amber for warnings; in this system it reads as "running".
- **Don't** load web fonts or icons from a CDN or icon package; the font is self-hosted and the only icons are inline SVG (house mark, theme circle, masked chevron).
- **Don't** move the stylesheet out of `_STYLE`; the system has to remain one inline string served by stdlib `http.server`.
