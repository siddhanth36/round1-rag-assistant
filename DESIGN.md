# DESIGN.md — INDUSTRIX AI

Status: **direction for approval. No UI code written yet.** Strategic context: `PRODUCT.md`.
Stack constraint (challenge brief): Django templates, HTML5, CSS3, vanilla JS, Bootstrap 5. Nothing else
in the UI layer, except GSAP core for a few purposeful transitions (§5).
This file **supersedes** `design-system/industrix-ai/MASTER.md` wherever they conflict.

## 0. Design read

Reading this as: **an authenticated product tool for maintenance engineers and supervisors**, with a
**calm control-room language**, built by **theming Bootstrap 5.3 through three layers of CSS custom properties**.

Physical scene (impeccable's theme test): *an engineer at a line-side terminal in a dim, machine-lit bay
needs one exact answer and the page that proves it, without glare and without hunting.* That makes dark
the right choice, not a default: low ambient light, long glances, status that must read instantly.

**The trap, named:** "dark navy + one electric-blue accent + industrial" is itself the second most common AI
look (near-black with one bright accent). The brief pins it, so we follow it. What keeps it from being
generic is spending boldness in **one** place: the **evidence slip + scope plate** system (§7), which
mirrors the real manuals' own footer (`HP-OPS-01 | Rev 1.0 | Page 2`). Everything around it stays quiet.

## 1. Color

Strategy: **Restrained**. Tinted navy neutrals (hue 255, chroma ≤ 0.024), one electric-blue accent used
only for primary actions, current selection, links and focus (≤10% of any screen), plus three state colors.

The impeccable brand seed (`oklch(0.650 0.146 60)`, honey amber) was **not** used as the primary: the brief
commits the accent to electric blue, and the skill says committed brand colors win. Its hue (60–70°) is
used where amber means something on a plant floor: **caution = "not in the manuals"**.

### Primitive palette (all values computed; hex is the gamut-mapped sRGB)

| Token | OKLCH | Hex | Role |
|---|---|---|---|
| `--p-navy-050` | oklch(0.165 0.018 255) | `#090F16` | canvas |
| `--p-navy-100` | oklch(0.200 0.020 255) | `#10171F` | surface (panels, tables) |
| `--p-navy-150` | oklch(0.235 0.022 255) | `#171F28` | surface-2 (sidebar, context bar, inputs) |
| `--p-navy-200` | oklch(0.275 0.024 255) | `#202833` | surface-3 (hover, selected row) |
| `--p-navy-300` | oklch(0.330 0.022 255) | `#2E3641` | hairline divider (decorative) |
| `--p-navy-400` | oklch(0.440 0.022 255) | `#4B535F` | strong divider (decorative) |
| `--p-navy-500` | oklch(0.540 0.022 255) | `#66707C` | control border (inputs, selects: needs 3:1) |
| `--p-gray-600` | oklch(0.680 0.020 255) | `#9099A5` | subtle text (meta, placeholders) |
| `--p-gray-700` | oklch(0.780 0.018 255) | `#B0B8C3` | muted text |
| `--p-gray-900` | oklch(0.955 0.006 255) | `#EDF0F4` | primary text |
| `--p-blue-500` | oklch(0.680 0.170 250) | `#319CFC` | accent (primary button fill, selection) |
| `--p-blue-400` | oklch(0.740 0.150 250) | `#58B0FF` | accent hover |
| `--p-blue-300` | oklch(0.800 0.120 250) | `#80C3FF` | accent text / links / source tags |
| `--p-blue-200` | oklch(0.820 0.120 245) | `#7ECCFF` | focus ring |
| `--p-green-400` | oklch(0.780 0.150 158) | `#50D492` | answered / indexed |
| `--p-amber-400` | oklch(0.800 0.140 70) | `#F7AC4D` | insufficient evidence / processing |
| `--p-amber-seed` | oklch(0.650 0.146 60) | `#CE7616` | caution border, dashed outline |
| `--p-red-400` | oklch(0.720 0.165 25) | `#FB756E` | error / failed |

### Contrast (WCAG 2.x, measured)

| Pair | Ratio | Requirement |
|---|---|---|
| text on canvas / surface / surface-2 / surface-3 | 16.90 / 15.88 / 14.62 / 13.00 | 4.5 ✓ |
| muted text on canvas / surface / surface-2 / surface-3 | 9.63 / 9.05 / 8.33 / 7.41 | 4.5 ✓ |
| subtle text on canvas / surface / surface-2 | 6.70 / 6.29 / 5.79 | 4.5 ✓ (placeholders too) |
| accent text (links, tags) on canvas / surface / surface-2 | 10.26 / 9.64 / 8.88 | 4.5 ✓ |
| canvas-colored label on accent fill / on accent hover | 6.70 / 8.32 | 4.5 ✓ (primary button text is dark, not white) |
| accent fill vs canvas / surface (button boundary) | 6.70 / 6.29 | 3.0 ✓ |
| green / amber / red on surface | 9.61 / 9.46 / 6.79 | 4.5 ✓ |
| green / amber / red on surface-2 | 8.85 / 8.71 / 6.25 | 4.5 ✓ |
| amber seed on surface | 5.37 | 3.0 ✓ (dashed caution outline) |
| control border on canvas / surface / surface-2 | 3.81 / 3.58 / 3.30 | 3.0 ✓ (1.4.11 non-text) |
| focus ring on canvas / surface-2 | 10.98 / 9.50 | 3.0 ✓ |
| focus ring directly on accent fill | 1.64 | ✗ so the ring always sits **outside** a 2px canvas-colored gap (`outline-offset: 2px`) |
| hairline / strong dividers on surface | 1.48 / 2.33 | decorative only, never the sole boundary of a control |

### Semantic layer (purpose aliases; components use only these)

`--color-bg` canvas · `--color-surface` · `--color-surface-raised` (surface-2) · `--color-surface-hover` (surface-3) ·
`--color-text` · `--color-text-muted` · `--color-text-subtle` · `--color-divider` · `--color-divider-strong` ·
`--color-control-border` · `--color-accent` · `--color-accent-hover` · `--color-on-accent` (= canvas) ·
`--color-link` (blue-300) · `--color-focus` · `--color-ok` · `--color-caution` · `--color-caution-line` (amber seed) ·
`--color-danger` · tinted state backgrounds `--color-ok-bg` / `--color-caution-bg` / `--color-danger-bg`
(state hue at L 0.24, C 0.04, so they stay surface-like, never neon fills).

### Bootstrap mapping (component layer; theme Bootstrap, don't fight it)

`<html data-bs-theme="dark">`, then a `[data-bs-theme=dark]` block in `tokens.css` overrides:

| Bootstrap variable | ← semantic token |
|---|---|
| `--bs-body-bg`, `--bs-body-color`, `--bs-secondary-color`, `--bs-tertiary-color` | bg, text, text-muted, text-subtle |
| `--bs-secondary-bg`, `--bs-tertiary-bg` | surface-raised, surface |
| `--bs-border-color`, `--bs-border-color-translucent` | divider |
| `--bs-primary`, `--bs-primary-rgb`, `--bs-link-color(-rgb)`, `--bs-link-hover-color` | accent, accent, link, accent-hover |
| `--bs-success`, `--bs-warning`, `--bs-danger` (+ `-rgb`, `-text-emphasis`, `-bg-subtle`, `-border-subtle`) | ok, caution, danger + tinted bgs |
| `--bs-focus-ring-color`, `--bs-focus-ring-width: 2px`, `--bs-focus-ring-opacity: 1` | focus |
| `--bs-font-sans-serif`, `--bs-font-monospace`, `--bs-body-font-size`, `--bs-body-line-height` | §2 |
| `--bs-border-radius(-sm/-lg/-xl)` | §3 |
| `.btn-primary { --bs-btn-bg/-border-color/-color/-hover-bg/-active-bg/-disabled-* }` | accent family, on-accent |
| `.form-control, .form-select { border-color }` | control-border (Bootstrap's default fails 3:1) |
| `.table { --bs-table-bg, --bs-table-hover-bg, --bs-table-striped-bg }` | surface, surface-hover, none (no zebra) |

Bootstrap is used **compiled** (pinned 5.3.x, vendored). No Sass build: CSS variables are enough.

## 2. Typography

**IBM Plex Sans + IBM Plex Mono** (one superfamily, Google Fonts, vendored as latin-subset woff2).
- Plex was drawn for an engineering company: neutral but not bland, humanist details that stay legible at
  13–14px in dense tables. It reads "enterprise instrument", not "startup landing page".
- Plex Mono gives machine IDs, error codes, revisions, page numbers and timestamps a readout voice, and its
  zero is distinct from the letter O, which matters for `M-AC90`, `E17`, `Rev 1.0`.
- Rejected: Fira Sans + Fira Code (the generator's pick; Code's programming ligatures would mangle data like
  `->` or `!=`), Geist (reads as a Vercel clone), Inter / Roboto (the generic defaults the anti-slop skills ban).

Weights: Sans 400 / 500 / 600, Mono 400 / 500 (5 files, ~100 KB total, `font-display: swap`, size-adjusted
`system-ui` fallback to limit layout shift; only Sans 400 is preloaded).

Fixed rem scale, ratio ≈1.2 (product register: no fluid type):

| Token | Size / line-height | Use |
|---|---|---|
| `--fs-xs` | 0.75rem (12px) / 1.4, Mono | minimum size; timestamps in dense tables only |
| `--fs-sm` | 0.8125rem (13px) / 1.45, Mono 500, letter-spacing 0.02em | machine-ID tags, slip headers, readouts |
| `--fs-ui` | 0.875rem (14px) / 1.45 | nav, buttons, table cells, labels |
| `--fs-body` | 1rem (16px) / 1.6 | answers, passages, form inputs (16px also stops iOS zoom) |
| `--fs-lg` | 1.1875rem (19px) / 1.35, 600 | panel titles |
| `--fs-xl` | 1.4375rem (23px) / 1.25, 600, -0.01em | page titles |
| `--fs-2xl` | 1.75rem (28px) / 1.2, 600, -0.015em | login heading only |

Rules: sentence case everywhere; **no** uppercase-tracked eyebrows. Mono tags are case-preserved because
the IDs are already uppercase. Answer and passage prose capped at 72ch. `font-variant-numeric: tabular-nums`
in tables and readouts. `text-wrap: balance` on h1–h3.

## 3. Space, radius, elevation, layers

Spacing (4px base, density 8/10): `--space-1` 4 · `--space-2` 8 · `--space-3` 12 · `--space-4` 16 · `--space-5` 20 ·
`--space-6` 24 · `--space-8` 32 · `--space-12` 48. Bootstrap's own utilities (`1`=4, `2`=8, `3`=16, `4`=24,
`5`=48 px) are a subset of this scale, so both can be used without drift. Rhythm comes from varied gaps:
8 inside groups, 16 between related blocks, 32 between sections.

Controls: height 36px (`--control-h`), 40px for the composer submit; hit targets ≥ 32×32 (WCAG 2.2 AA 2.5.8 needs 24).

Radius (industrial = small, consistent, never pills):
`--radius-xs` 2px (machine tags, status chips) · `--radius-sm` 4px (buttons, inputs) · `--radius-md` 6px
(panels, evidence slips, tables) · `--radius-lg` 8px (offcanvas, dialog). Bootstrap: `-sm`=2, base=4, `-lg`=6, `-xl`=8.

Elevation: on dark UI, depth comes from **surface lightness + a 1px divider**, not shadows. One shadow,
for overlays only: `0 12px 32px oklch(0 0 0 / 0.5)`.

Layers: `--z-sticky` 100 · `--z-dropdown` 200 · `--z-offcanvas` 300 · `--z-modal` 400 · `--z-toast` 500 · `--z-tooltip` 600.

Shell: left sidebar 232px (≥992px; an offcanvas below), sticky context bar 56px, content max 1320px with
24px gutters (16px under 576px). Assistant: conversation column ≤ 72ch + evidence rail 380px (≥1200px);
below that, slips render under each answer.

## 4. Icons

**Bootstrap Icons 1.13.x** (native to the stack, consistent 16px grid, fine outline strokes), shipped as
one **inline SVG sprite of only the ~25 icons used**, not the 130 KB icon font. Sizes 16px (UI) and 20px
(status). Every icon-only button has `aria-label`; decorative icons have `aria-hidden="true"`. No emoji anywhere.

Set: `gear-wide-connected` machines · `journal-text` manuals · `chat-square-text` assistant ·
`clock-history` history · `clipboard-data` review · `upload` · `file-earmark-pdf` · `box-arrow-up-right`
open page · `copy` · `check-circle` answered · `journal-x` not in manuals · `x-octagon` error ·
`arrow-repeat` retry · `hourglass-split` processing · `chevron-left`/`-right`/`-down` · `funnel` ·
`list` menu · `box-arrow-right` sign out · `person-badge` role · `info-circle` · `exclamation-triangle`.

## 5. Motion

Principle: motion only conveys **state** (product register). Durations: `--dur-1` 120ms (hover, press) ·
`--dur-2` 180ms (state change, expand) · `--dur-3` 240ms (answer reveal). Easing: `--ease-out`
`cubic-bezier(0.25, 1, 0.5, 1)` (ease-out-quart); exits `cubic-bezier(0.5, 0, 0.75, 0)` at 140ms. No bounce,
no elastic, no `linear` except the loading indicator.

| Moment | How | Reduced motion |
|---|---|---|
| Hover / press | CSS: background and border color only, 120ms | instant |
| Focus ring | **no transition**: appears instantly | same |
| Answer arrives | GSAP: status header then statements, `autoAlpha` 0→1 + `y` 6→0, stagger 0.04s, 0.24s | instant, no movement |
| `[S#]` clicked | GSAP: slip scrolls into view and its outline pulses accent → divider once, 0.6s | outline set, no pulse; scroll is instant |
| Retrieving | CSS: 2px indeterminate bar on the context bar's bottom edge + live text "Searching 2 manuals for M-HP200…" | static bar, text only |
| Upload steps | GSAP: result rows fade in as the response is rendered, 0.18s each | instant |
| History row expand | `<details>` + 180ms height via CSS `interpolate-size` where supported | instant |

GSAP rules (gsap-core): **gsap 3.13 core only**, vendored, loaded `defer` on the assistant, manage and
history pages. Every tween lives in one `gsap.matchMedia()` with a `(prefers-reduced-motion: reduce)` branch
that sets final states. Content is **visible by default**: animations enhance an already-rendered state
and never gate visibility, so if GSAP fails to load nothing disappears. Transform/opacity only.
Banned: page-load choreography, scroll-triggered reveals (the generator suggested ScrollTrigger: rejected),
parallax, infinite decorative loops, fake "typing" of answers (answers arrive whole; faking a stream would
misrepresent how the system works).
Note: GSAP is ~27 KB gzipped for effects CSS could nearly do. It's kept because the brief asks for it. If
the performance budget tightens, it's the first thing to drop.

## 6. Answer states (never color alone: icon + label + shape + color)

| | answered | insufficient_evidence | error |
|---|---|---|---|
| Chip | `check-circle` "Answered · 2 sources" (green on ok-bg) | `journal-x` "Not in the manuals" (amber on caution-bg) | `x-octagon` "Couldn't answer" (red on danger-bg) |
| Panel border | 1px solid divider (neutral: the evidence is the color) | 1px **dashed** amber-seed outline | 1px solid danger |
| Body | validated answer, `[S#]` as accent mono buttons linking to slips | fixed message in muted text + "Searched: HP-OPS-01 r1.0 · HP-MNT-01 r1.0 (M-HP200)" + "Try rephrasing, or check you selected the right machine." | "The assistant couldn't complete this request. It's been logged for your administrator." + [Retry] (asks again = a new log entry) |
| Evidence | slips (rail or inline) | none, explicitly: no empty slip area | none |
| Readout | latency `2.3 s` (mono) | latency | none |
| Live region | `aria-live="polite"` announces "Answered with 2 sources" | announces "Not in the manuals for M-HP200" | `role="alert"` |
| After render | focus moves to the answer heading | same | focus moves to Retry |

Manual indexing states reuse the same vocabulary: `INDEXED` (green, check), `PROCESSING` (amber,
hourglass), `FAILED` (red, x-octagon + `indexing_error` shown to admins).

## 7. Signature: scope plate + evidence slip

- **Scope plate** (in the context bar whenever a machine is selected): `[M-HP200]` mono tag ·
  "HP-200 Hydraulic forming press" · "Scope: HP-OPS-01 r1.0 · HP-MNT-01 r1.0". Machine isolation, the
  product's core guarantee, is always visible instead of buried in a select box.
- **Evidence slip** (citation card): header in Mono 13px written exactly like the manuals' printed footer,
  `S1  HP-OPS-01 | Rev 1.0 | Page 2`, so engineers recognize it on the page they open. Below it: the
  filename (subtle), the verbatim passage (body size, clamped to 6 lines with "Show full passage"), then
  `[Open page 2 ↗]` and `[Copy reference]`. Full 1px border, `--radius-md`, never a side stripe.
- Industrial accents, used selectively (industrial-brutalist-ui): mono readouts (IDs, timestamps,
  latency, page counts), machine-ID tags (1px control-border box, 2px radius), and the 2px retrieval status
  bar. **Not** borrowed: zero radius everywhere, CRT scanlines, noise, ASCII framing, hazard red, giant
  uppercase type.

## 8. Page layouts

All pages share the shell (sidebar + sticky context bar + skip link "Skip to main content"). Text in angle
brackets is real data at runtime. No sample answers, passages or numbers are invented in the UI.

**Shell**
```
┌──────────────┬───────────────────────────────────────────────────────────────────────────┐
│ INDUSTRIX AI │ <Page title>   [scope plate, when a machine is selected]   eng1 · Engineer ⏻│
│              ├───────────────────────────────────────────────────────────────────────────┤
│ Dashboard    │                                                                           │
│ Assistant    │  main                                                                     │
│ History      │                                                                           │
│ Manuals      │                                                                           │
│ Manage   (A) │                                                                           │
│ Review   (S) │                                                                           │
│──────────────│                                                                           │
│ 3 manuals indexed (mono readout)                                                         │
└──────────────┴───────────────────────────────────────────────────────────────────────────┘
(A)/(S): rendered only for Admin / Supervisor. <992px: sidebar becomes an offcanvas behind a menu button.
```

**Login** (asymmetric split, not a centered card on a gradient)
```
┌───────────────────────────────────────────┬──────────────────────────────────────────────┐
│ INDUSTRIX AI                              │  Sign in                                     │
│                                           │  Username  [______________________________]  │
│ Maintenance answers from your machine's   │  Password  [__________________________][Show]│
│ own manuals, with the page to prove it.   │  [ Sign in ]                                 │
│                                           │  ⚠ Invalid username or password. (role=alert)│
│ How every answer is sourced (schematic):  │                                              │
│ [M-xxxx] → DOC-ID | Rev x.x | Page n      │  Accounts are issued by your administrator.  │
└───────────────────────────────────────────┴──────────────────────────────────────────────┘
5/12 · 7/12 columns ≥992px; below, the left panel collapses to a one-line header.
```

**Dashboard / machine selector** (Engineer; Admin sees corpus status; Supervisor lands on Review)
```
Select a machine                                         Recent questions
Answers use only the selected machine's manuals.         ┌──────────────────────────────────┐
┌────────────────────────────────────────────────────┐   │ <hh:mm> [M-AC90]  <question…>     │
│ [M-HP200]  HP-200 · Hydraulic forming press         │   │         ✓ Answered · <n> sources  │
│ 2 manuals  HP-OPS-01 r1.0 · HP-MNT-01 r1.0          │   │ <hh:mm> [M-HP200] <question…>     │
│                                    Ask about it  ›  │   │         ◇ Not in the manuals      │
├────────────────────────────────────────────────────┤   │                 View history  ›   │
│ [M-AC90]   AC-90 · Air compressor                   │   └──────────────────────────────────┘
│ 1 manual   AC-OM-01 r1.0                            │
│                                    Ask about it  ›  │
└────────────────────────────────────────────────────┘
One bordered list of nameplate rows (not a card grid); each row is a single link. Machines with no indexed
manual are shown disabled: "No indexed manuals yet".
```

**AI assistant** (a question log, not chat bubbles: each question is independent)
```
┌ context bar ────────────────────────────────────────────────────────────────────────────────┐
│ Assistant  [M-HP200] HP-200 Hydraulic forming press · Scope: HP-OPS-01 r1.0 · HP-MNT-01 r1.0 [Change ▾]│
├─────────────────────────────────────────────────────────┬───────────────────────────────────┤
│ Q <hh:mm>  <question text>                              │ Evidence for the selected answer  │
│ ┌ ✓ Answered · 2 sources · 2.3 s ─────────────────────┐ │ ┌ evidence slip S1 ─────────────┐ │
│ │ <answer statement> [S1]                              │ │ └──────────────────────────────┘ │
│ │ <answer statement> [S2]                              │ │ ┌ evidence slip S2 ─────────────┐ │
│ └──────────────────────────────────────────────────────┘ │ └──────────────────────────────┘ │
│ Q <hh:mm>  <question text>                              │                                   │
│ ┌ ◇ Not in the manuals ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐  │                                   │
│   The available manuals for the selected machine do      │                                   │
│   not contain enough information to answer this question.│                                   │
│   Searched: HP-OPS-01 r1.0 · HP-MNT-01 r1.0 (M-HP200)    │                                   │
│ └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘  │                                   │
├─────────────────────────────────────────────────────────┴───────────────────────────────────┤
│ Question  [ Ask about M-HP200…                                          ]  0/1000  [Ask ⏎] │
│ Enter to ask · Shift+Enter for a new line · Each question is answered independently.        │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
Empty state: "Ask about M-HP200" + the brief's four practice questions as suggestion buttons.
Loading: the question row appears at once; the answer area shows a 3-line skeleton (static under reduced
motion); after 4 s: "The first question loads the search model and can take up to 15 s."
Changing machine clears the on-screen log (history keeps everything) and updates the scope plate.
<1200px: no rail; slips render under each answer. The rail follows the last answer or the clicked [S#].
```

**Evidence slip (citation card)**
```
┌──────────────────────────────────────────────────────────┐
│ S1   HP-OPS-01 | Rev 1.0 | Page 2              (Mono 13) │
│ HP200_Operations_Rev1_0.pdf                    (subtle)  │
│ ──────────────────────────────────────────────────────── │
│ “<verbatim passage from the cited chunk, max 6 lines>”   │
│ Show full passage ▾                                      │
│ ──────────────────────────────────────────────────────── │
│ [↗ Open page 2]   [⧉ Copy reference]                     │
└──────────────────────────────────────────────────────────┘
"Open page 2" → /manuals/<id>/view/?page=2&q=<question id>&tag=S1 (same tab; Back returns to the log).
"Copy reference" copies "HP-OPS-01 Rev 1.0, PDF page 2".
```

**PDF viewer**
```
┌ context bar ───────────────────────────────────────────────────────────────────────────────┐
│ [M-HP200] HP-OPS-01 | Rev 1.0      ‹  Page [2] of 3  ›      [Open in new tab ↗]  [‹ Back] │
├────────────────────────────┬───────────────────────────────────────────────────────────────┤
│ Cited passage              │                                                               │
│ (only when opened from a   │   <iframe title="HP-OPS-01 Rev 1.0, page 2"                   │
│  citation)                 │           src="/api/manuals/<id>/file/#page=2">               │
│ “<verbatim passage>”       │   native browser PDF viewer                                   │
│ From question <#42>  ›     │                                                               │
└────────────────────────────┴───────────────────────────────────────────────────────────────┘
Page change rebuilds the iframe src (some viewers ignore a hash-only change). Where inline PDF is not
supported (iOS), show "Open PDF at page 2" instead of an empty frame. <992px: passage stacks above.
```

**Manuals library** (all roles; read-only except Admin's upload shortcut)
```
Manuals                                  Machine ( All | M-HP200 | M-AC90 )    [Upload manual] (A)
┌─────────────┬────────────┬──────┬─────────────────────────────┬───────┬────────────┬──────────────┬──────┐
│ Machine     │ Document   │ Rev  │ File                        │ Pages │ Status     │ Uploaded     │      │
├─ M-HP200 · HP-200 Hydraulic forming press ──────────────────────────────────────────────────────────────┤
│ [M-HP200]   │ HP-OPS-01  │ 1.0  │ HP200_Operations_Rev1_0.pdf │ 3     │ ✓ Indexed  │ <user> <date>│ View │
│ [M-HP200]   │ HP-MNT-01  │ 1.0  │ HP200_Maintenance_Rev1_0.pdf│ 3     │ ✓ Indexed  │ <user> <date>│ View │
├─ M-AC90 · AC-90 Air compressor ─────────────────────────────────────────────────────────────────────────┤
│ [M-AC90]    │ AC-OM-01   │ 1.0  │ AC90_Operations_Maintenance…│ 3     │ ✓ Indexed  │ <user> <date>│ View │
└─────────────┴────────────┴──────┴─────────────────────────────┴───────┴────────────┴──────────────┴──────┘
Grouped by machine; table in an overflow-x container on narrow screens; Failed rows show the error to Admins.
```

**Admin: manage (upload + register)**
```
Manage corpus
┌ Upload a manual ─────────────────────────────────┐  ┌ Result ──────────────────────────────────┐
│ Machine      [M-HP200 · HP-200 ▾]                 │  │ Idle: details appear after upload.        │
│ Document ID  [HP-OPS-01]   Revision [1.0]         │  │ ✓ Uploaded   HP-OPS-01 r1.0 (<size>)      │
│ PDF file     ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐    │  │ ✓ Extracted  <3> pages                    │
│              │ Drop a PDF or browse · ≤ 20 MB │    │  │ ✓ Indexed    ready for M-HP200 questions │
│              └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘    │  │   or ✕ Failed: <indexing_error>          │
│ [Upload and index]                                │  └───────────────────────────────────────────┘
└───────────────────────────────────────────────────┘
┌ Register a machine ───────────────────────────────────────────────────────────────────────────┐
│ Machine ID [M-____]  Model [____]  Name [____________]  [Register]   Registered: [M-HP200] [M-AC90] │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
Errors appear under their field (aria-describedby). While indexing, one honest progress state
("Uploading and indexing… the first upload loads the model, up to 15 s"); the result rows are built from
the response, never simulated step by step.
```

**Supervisor: review (analytics + unanswered queue)**, deliberately not a big-number card template
```
Review                                                         Machine [All ▾]
┌ Outcome ledger ─────────────────────────────────────────────────────────────────────────────┐
│ All machines  <total>   ███████████████████████░░░░░▒▒   <n> ✓ answered  <n> ◇ not in manuals  <n> ✕ │
│ [M-HP200]     <total>   ██████████████████░░░░▒          …                                       │
│ [M-AC90]      <total>   ██████████░░░▒                   …                                       │
│ Avg response <x.x s> (mono)                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
Unanswered queue (<n>)                         Outcome [Unanswered ▾]  [Show all]
┌───────────┬──────────┬───────┬────────────────────────────────────────┬──────────────────┬────────┐
│ <time>    │ [M-HP200]│ <user>│ <question>                             │ ◇ Not in manuals │ Details▾│
└───────────┴──────────┴───────┴────────────────────────────────────────┴──────────────────┴────────┘
Details: answer text, citations, and the trace summary (retrieved n, best distance, reason) for diagnosis.
Ledger bars are stacked segments with pattern + label (not color alone) and a text table equivalent for
screen readers. The queue is the main content: the brief's job is "identify unanswered queries".
```

**Question history** (Engineer: own; Supervisor sees the same component with an "Asked by" column)
```
History                                      Machine [All ▾]   Outcome [All ▾]
┌──────────────────┬──────────┬────────────────────────────────────────────┬──────────────────────┬───┐
│ <date> <hh:mm>   │ [M-AC90] │ <question text, 1 line, truncated>         │ ✓ Answered · 1 source │ ▾ │
├──────────────────┴──────────┴────────────────────────────────────────────┴──────────────────────┴───┤
│ expanded: the same answer panel + evidence slips as the assistant · [Ask again about M-AC90]          │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
‹ Previous   Page 1 of <n>   Next ›          Empty: "No questions yet. Ask one from the assistant."
```

## 9. Accessibility acceptance criteria

WCAG 2.2 AA. Contrast as measured in §1. Visible focus on every interactive element (2px ring, 2px
offset). Skip link. Landmarks (`header`, `nav`, `main`). One `h1` per page. Full keyboard flow (Tab order =
visual order, Enter submits, Shift+Enter newline, Esc closes offcanvas, `[S#]` reachable). Labels on all
inputs (no placeholder-only labels). Errors announced (`role="alert"` / `aria-live`), placed next to their
field. Status never by color alone (§6). Tables with `<th scope>` and captions. iframe has a `title`.
200% zoom with no horizontal page scroll (only tables scroll in their own container). Reduced motion (§5).
Manual check before sign-off: keyboard-only run of all ten pages plus a screen-reader pass (NVDA on Windows).

## 10. Performance acceptance criteria

No CDN at runtime: Bootstrap 5.3.x CSS + bundle JS, the icon sprite, GSAP 3.13 and the fonts are vendored
under `frontend/static/vendor/` (evaluation-day network is unknown). `ManifestStaticFilesStorage` for
cache-busting. One CSS bundle (`tokens.css` + `app.css`); page JS as `defer`red ES modules; GSAP only on the
pages that use it. Budget per page excluding the PDF: ≤ 180 KB transferred. Local targets: LCP < 1.5 s,
CLS < 0.05 (skeletons reserve answer space; font fallback is size-adjusted), INP < 200 ms.

## 11. Generic AI-design patterns we will not ship

Purple/pink "AI" gradients or gradient text · mesh/aurora backgrounds · glassmorphism and backdrop blur on
content · uniform icon + heading + text card grids · hero-metric stat tiles (big number, small label) ·
centered-everything layouts and centered login cards on gradients · uppercase tracked eyebrows above
sections · 01/02/03 numbered section markers · colored left/right stripe borders on cards and alerts ·
emoji icons · pill-shaped everything / "button-in-button" arrows · chat bubbles with avatars and a fake
typing stream · sparkle/magic-wand "AI" iconography · neon glow / sci-fi HUD / CRT effects · scroll-reveal
on every block · bounce/elastic easing · nested cards · gray-on-gray low-contrast "elegant" text ·
placeholder-only form labels · spinners in the middle of content · modals as the first answer
(inline/expand first; only the mobile nav is an offcanvas) · Inter/Roboto defaults.

## 12. What each skill contributed

| Skill | Taken | Rejected (and why) |
|---|---|---|
| ui-ux-pro-max (`--design-system`, density 8 / motion 3 / variance 5) | dense 4px spacing scale, a11y checklist (4.5:1, focus, skip link, aria-live errors, 44px touch spirit), "no emoji icons", reduced-motion rule; color domain confirmed dark navy + status colors for ops dashboards | generated palette `#7C3AED`/`#EC4899` on lavender (contradicts brief), "AI Personalization Landing" pattern (marketing, not product), Fira pair, ScrollTrigger reveal |
| impeccable (`context.mjs`, init → PRODUCT.md, `palette.mjs`, product register) | OKLCH throughout, Restrained strategy, scene-sentence theme test, contrast rules, absolute bans, product-register motion (150–250ms, state only), state vocabulary, z-index scale | seed hue as primary (brief fixes blue); used for caution instead |
| frontend-design | one signature element (scope plate + slip), copy rules (active verbs, errors that say what to do, no apologies), structure must encode truth | — |
| design-taste-frontend | anti-default list, "design read" step | its dials/landing presets: it states it is not for dashboards |
| high-end-visual-design | no Inter/Roboto, transform/opacity-only motion, custom ease curves, blur only on fixed layers | double-bezel shells, pill CTAs, heavy whitespace, glass nav, eyebrow pills, scroll entrances: all fight a dense product UI |
| design-system | primitive → semantic → component tokens; component state tables | Tailwind integration, slide generator |
| industrial-brutalist-ui | mono readouts, machine-ID tags, status bar | zero radius, CRT/noise, ASCII framing, hazard red, uppercase macro type |
| gsap-core | `gsap.matchMedia()` reduced-motion pattern, `autoAlpha`, transform aliases | timelines/plugins (not needed) |
| accessibility-compliance-accessibility-audit / web-performance-optimization | §9 and §10 as acceptance criteria | — |
