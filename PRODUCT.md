# Product

> Draft written by impeccable `init` from the challenge brief, `CLAUDE.md` and the Part B prompt.
> Not yet confirmed answer-by-answer: confirm or correct at the design approval step.

## Register

product

## Platform

web

## Users

Primary: **maintenance engineers** at a manufacturing plant. They're in a shift bay or at a line-side
terminal or laptop, often under dim, machine-lit ambient light, between jobs, with gloves recently off.
Their job: pick the machine in front of them, ask one precise maintenance question (an alarm code, a
pressure range, an inspection interval, what to record before escalating), and get an answer they can
check against the exact manual page.

Secondary: **supervisors**, at a desk at the end of a shift, reviewing what was asked and finding the
questions the manuals could not answer. **Administrators** register machines and upload manual revisions.

## Product Purpose

INDUSTRIX AI answers maintenance questions **only** from the selected machine's own PDF manuals and
proves every answer with a document ID, revision, PDF page, the supporting passage and a link to that
page. When the manuals don't contain the answer, it says so instead of guessing. Success: an engineer
trusts an answer because they can open the cited page in one click, and never gets another machine's
procedure (E17 means low hydraulic pressure on the HP-200 but high discharge temperature on the AC-90).

## Positioning

Every answer is scoped to one machine and traceable to a page: verifiable evidence, not a chatbot opinion.

## Brand Personality

Trustworthy, precise, calm. The voice of a good control-room console: terse, factual, never cheerful,
never apologetic. It names the machine, the document and the page, and states limits plainly.

References named in the brief: Linear and Vercel (quiet, dense, keyboard-first product craft) and
Siemens / ABB control consoles (industrial legibility, status that reads at a glance, nothing decorative).

## Anti-references

A hackathon template. Default purple/pink "AI" gradients, uniform icon-card grids, centered-everything
hero layouts, emoji icons, glassmorphism everywhere, chat-bubble consumer messengers, sci-fi HUD cosplay.

## Design Principles

1. **Evidence is the interface.** Citations are first-class content, not footnotes. The page reference is
   always one click away.
2. **Scope is always visible.** The selected machine and the manuals in scope are on screen whenever an
   answer is.
3. **Say "not in the manuals" with confidence.** Insufficient evidence is a correct, designed outcome,
   visually distinct from a system error.
4. **Calm density.** Show a lot of information quietly: dense tables, mono readouts, one accent, no noise.
5. **Earned familiarity.** Standard affordances, consistent components, nothing invented for flavor.

## Accessibility & Inclusion

WCAG 2.2 AA is an acceptance criterion: 4.5:1 text contrast, 3:1 for UI boundaries and focus, full
keyboard operation with visible focus, status never conveyed by color alone, `prefers-reduced-motion`
honored everywhere, usable at 200% zoom.
