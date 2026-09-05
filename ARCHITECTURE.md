# JB Wealth Intelligence — Unified Architecture

One system, merged from two builds that independently arrived at the same spine.
Covers all three challenge building blocks: **Explanations (#1)**, **Risk &
Opportunity (#2)**, and the **RM Workbench (#3)**.

## The one principle everything obeys

> **The deterministic engine reasons. The LLM only narrates. Nothing on screen is
> a number or cause the engine didn't compute and ground. When the data can't
> explain a move, the system says "unexplained" — it never free-associates a cause.**

This is the whole pitch. It's what survives a compliance review, and it's what
most teams skip. Two mechanisms enforce it:

1. **Exact reconciliation** (from the Morning-Book build) — every dollar of change
   decomposes into trading + price + FX effects that sum *exactly* to the observed
   move, with no fudge term. Arithmetic you can audit line by line.
2. **Grounded, tool-scoped narration** (both builds) — the LLM sees only computed
   facts, via a fixed schema or scoped tool calls. It cannot state a figure, date,
   or cause that didn't come from the engine that turn.

## Architecture

```
data/ (12 CSVs + rm_notes.json)          # immutable, as provided
      │
  ingest.py ── load once (cached), USD throughout, FX implied from
      │        market_value_usd / market_value_local (no FX-convention lookup)
      ▼
  ┌─ engine/ ──────────────── DETERMINISTIC. returns findings, never prose ─────────┐
  │  attribution.py   EXACT 3-way decomposition between any two of the 5 snapshots:  │
  │                     trading_effect = (q1-q0)·p0·fx0                               │
  │                     price_effect   = q1·(p1-p0)·fx0                               │
  │                     fx_effect      = q1·p1·(fx1-fx0)                              │
  │                   → these SUM EXACTLY to ΔMV_usd (assert it). New positions /     │
  │                     exits are pure trading effects. Reports explained% vs         │
  │                     UNEXPLAINED residual.                                         │
  │  ground.py        link a move to event_log via a tag taxonomy (config/           │
  │                   grounding.py), WORD-BOUNDARY match against primary_transmission.│
  │                   No tag overlap → UNEXPLAINED, flagged not guessed.              │
  │  mandate.py       allocation-band + single-position breaches (drift vs directed) │
  │  concentration.py look-through structured products + cross-portfolio aggregation │
  │  collateral.py    LTV trajectory across snapshots vs margin-call trigger         │
  │  liquidity.py     commitments + planned cash needs vs what's actually sellable   │
  │  scenario.py      (to build) Strait reopens/worsens shock over exposures         │
  └──────────────────────────────────────────────────────────────────────────────────┘
      │
  schema.py ── THE SPINE. every finding becomes one Insight:
      │        { type, severity, headline, contributions[], evidence{events,
      │          holdings, rules, rm_note}, explained_pct, confidence, caveats[],
      │          suggested_action, narrative }
      ▼
  build.py ─────── engine output → Insight[]; attaches the matching RM note
  prioritise.py ── the Book: sortable by URGENCY (explainable score) or by |ΔUSD|
      │            (the "Morning Book"). Same insights, two sorts.
      ▼
  ┌─ narrate/ ─────────────────── LLM strictly downstream ──────────────────────────┐
  │  template.py   deterministic prose from the Insight. Zero deps, always works.    │
  │  chat.py       "Ask Why" — Claude tool-use loop scoped to ONE open client.       │
  │                Tools return already-computed facts only:                         │
  │                  get_portfolio_attribution(t0,t1) · get_rm_notes() ·             │
  │                  get_insights() · check_mandate_bands() · check_liquidity()      │
  │                System prompt forbids any number/cause not returned by a tool     │
  │                this turn; requires naming the cited event; flags note-vs-data     │
  │                contradictions. Degrades to nothing (never errors) with no key.   │
  └──────────────────────────────────────────────────────────────────────────────────┘
      │
  api/main.py ──── serves Insights + triage + the chat endpoint as JSON
  frontend/ ─────── Book → Client → Ask Why → Meeting Prep
                    · Book: ranked call list (urgency or dollar-move sort)
                    · Client: insight cards where every number & event is a
                      CLICKABLE PROVENANCE CHIP, plus a trading/price/FX waterfall
                    · Ask Why: the scoped chat, for the open client only
                    · Meeting Prep: the insights the RM ACCEPTED, as talking points
```

## What merged from where

| Capability | Source | Decision |
|---|---|---|
| Exact trading/price/FX reconciliation | Morning-Book build | **Adopt as the attribution core** (replaces the simpler price-vs-flow split) |
| explained% / UNEXPLAINED honesty metric | Morning-Book build | **Adopt** — carried on every attribution Insight |
| Scoped-tool "Ask Why" chatbot | Morning-Book build | **Adopt** as the interactive narration layer |
| Decomposition waterfall chart | Morning-Book build | **Adopt** in the Client view |
| Corporate-proxy / SSL handling (`truststore`) | Morning-Book build | **Adopt** — realistic for a bank; helps feasibility score |
| `Insight` universal contract | Starter | **Keep** — the spine for all engines and both UIs |
| mandate / concentration / collateral / liquidity engines | Starter | **Keep** — the breadth (blocks #2, #3) |
| Explainable cross-client triage score | Starter | **Keep**, unified with Morning Book as one sortable Book |
| Provenance-chip workbench + FastAPI JSON layer | Starter | **Keep** as the primary UI; UI stays swappable |

## The one open decision (for the humans, not Claude Code)

**Primary UI = the custom workbench** (FastAPI + `frontend/index.html`), because
the clickable provenance chip is the single highest-value interaction for the 25%
UX score, and the JSON API keeps the UI swappable. The existing **Streamlit** app
from the Morning-Book build stays valid as an *alternate surface on the same API*
— point it at `/clients/{id}/insights` and `/clients/{id}/chat`; don't rebuild it.
If frontend time runs short, Streamlit is the guaranteed-working fallback. Decide
which surface you demo by the first integration checkpoint; both consume identical
JSON, so no work is wasted either way.

## The golden worked example (your regression test)

**CL-0012, Cheung Kwok Wing** — 71, Income mandate, drawing ~USD 1.1m/yr. His
`Retirement Income Mandate` portfolio made essentially no position trades, so its
~-USD 2.1m (~-7%) move is market drift:

- **US Treasury 2.375% due 2045** — largest detractor, cited to the June 2026
  Fed-hold / 10-year-yield events (channel: `duration`).
- **Golden Harbour Properties perpetual** — down hard, but **no matching event in
  the log → flagged `UNEXPLAINED`**, not guessed.
- **A shipping/energy position** — *positive*, cited to the Strait of Hormuz
  closure (channel: `shipping`) — the same event that hurt others.

If a change breaks any of these, it broke the grounding. Keep it green.

## Data invariants

- Five snapshots: `2025-12-31, 2026-02-27, 2026-03-31, 2026-06-30, 2026-08-26`.
  The interesting work is in the comparison, never a single snapshot.
- `event_log.csv` is authoritative for anything that happened in 2026. The
  grounding layer cites it and only it — never the model's memory of the world.
- FX is implied per holding from `market_value_usd / market_value_local`.
- Some real-world data imperfections exist by design; handle them, don't assume
  them away.
