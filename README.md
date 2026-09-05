# JB Wealth Intelligence

**SingHacks 2026 — Julius Bär: From Portfolio Monitoring to Intelligence**

An AI wealth-intelligence workbench for a Relationship Manager covering 20 clients
across Singapore and Hong Kong. It answers the three questions a plain dashboard
can't: **what moved, why, and who do I call first** — and it hands the RM a
client-ready talking point, not a chart.

## The one principle everything obeys

> **The deterministic engine reasons. The LLM only narrates.** Nothing on screen is
> a number, date, or cause the engine didn't compute and ground. When the data
> can't explain a move, the system says **UNEXPLAINED** — it never free-associates
> a cause from the model's general knowledge.

Two mechanisms enforce this, not just a prompt asking nicely for it:

1. **Exact reconciliation.** Every dollar of portfolio change decomposes into
   `trading_effect + price_effect + fx_effect`, asserted in code to sum exactly to
   the observed move (tolerance ~$1). No fudge term, no "roughly."
2. **Grounded, tool-scoped narration.** The LLM only ever sees the return value of
   a computed `Insight` object or a scoped tool call — never a raw CSV. It cannot
   state a fact that didn't come back from the engine that turn, and the "Ask Why"
   chat is scoped to exactly one client per conversation, so it has no path to
   leak one client's data into another's.

## Quickstart

```bash
pip install -r requirements.txt
python app.py                 # opens the workbench at http://localhost:8080
```

Optional — enables LLM narration, the "Ask Why" chat, and live market-context
search; without it, everything still runs on deterministic template narration
(never a crash, never an empty screen):

```bash
export OPENAI_API_KEY=...
```

Smoke-test the engine alone, no UI:

```bash
python run.py CL-0012 --llm    # prints the golden client's insights + triage
pytest -q                      # golden reconciliation test + engine checks
```

The engine also ships a JSON API surface (`api/main.py`, FastAPI) paired with a
static workbench (`frontend/index.html`) for anyone who wants to drive the same
`Insight` data from a different frontend:

```bash
uvicorn api.main:app --port 8000   # then open frontend/index.html in a browser
```

## The three challenge building blocks

| Block | What's built |
|---|---|
| **1. Intelligent Portfolio Explanations** | Exact trading/price/FX attribution across any two of the five snapshots, with an `explained_pct` and honest `UNEXPLAINED` flags for moves with no grounded cause |
| **2. Proactive Risk & Opportunity Detection** | Mandate drift (vs. client-directed waivers), cross-portfolio concentration with structured-product look-through, collateral/LTV trajectory, and liquidity-gap detection |
| **3. RM Intelligence Workbench** | A prioritised Book ranked by an explainable urgency score, one-click provenance on every number, a scoped "Ask Why" chat, quarantined market-context search, and Accept/Modify/Reject decisions persisted for Meeting Prep |

## Architecture

```
data/ (12 CSVs + rm_notes.json)              # immutable, as provided
      │
  src/ingest.py ── load once (cached), USD throughout, FX implied from
      │            market_value_usd / market_value_local
      ▼
  ┌─ src/engine/ ──────────── DETERMINISTIC. returns findings, never prose ───────┐
  │  attribution.py   exact 3-way decomposition between any two of the 5          │
  │                   snapshots — trading + price + fx == ΔMV_usd, asserted.      │
  │                   New positions / exits are pure trading effects. Reports     │
  │                   explained_pct vs. the UNEXPLAINED residual.                 │
  │  ground.py        links a move to event_log.csv via a tag taxonomy            │
  │                   (config/grounding.py), WORD-BOUNDARY matched — never a      │
  │                   substring, never the model's memory. No overlap →           │
  │                   UNEXPLAINED, flagged not guessed.                           │
  │  mandate.py       allocation-band + single-position breaches, split into      │
  │                   client-directed waivers vs. silent drift                    │
  │  concentration.py cross-portfolio theme aggregation + structured-product      │
  │                   look-through via underlying_reference                      │
  │  collateral.py    LTV trajectory across all five snapshots vs. the            │
  │                   margin-call trigger                                        │
  │  liquidity.py     uncalled commitments + planned cash needs vs. what's        │
  │                   actually sellable, gated positions excluded                │
  └─────────────────────────────────────────────────────────────────────────────┘
      │
  src/schema.py ── THE SPINE. Every finding becomes one Insight:
      │            { type, severity, headline, contributions[], evidence{events,
      │              holdings, rules, rm_note}, explained_pct, confidence,
      │              caveats[], suggested_action, narrative }
      ▼
  src/build.py ────────── engine output → Insight[]; attaches the matching RM note
  src/engine/prioritise.py ── the Book: an explainable urgency score per client,
      │                       every ranking backed by the exact insights that
      │                       built it — never a black-box number
      ▼
  ┌─ src/engine/narrate/ ──────────── LLM strictly downstream ───────────────────┐
  │  template.py   deterministic prose from the Insight. Zero deps, always works. │
  │  chat.py       "Ask Why" — a tool-use loop scoped to ONE open client:         │
  │                  get_portfolio_attribution(t0,t1) · get_rm_notes() ·         │
  │                  get_insights() · check_mandate_bands() · check_liquidity()  │
  │                System prompt forbids any number/cause not returned by a      │
  │                tool this turn, requires naming the cited event, flags        │
  │                note-vs-data contradictions. No key / any error → a clean     │
  │                "chat unavailable", never a crash.                            │
  └───────────────────────────────────────────────────────────────────────────────┘
      │
  app.py ─────────────── NiceGUI workbench, one process, primary demo surface
                          · Book: ranked call list with the urgency score
                          · Client: insight cards where every number and event is
                            a CLICKABLE PROVENANCE CHIP
                          · Ask Why: the scoped chat, for the open client only
                          · Market Context: quarantined, clearly-labelled web
                            search — never mixed into the grounded numbers
                          · Meeting Prep: Accept / Modify / Reject on any insight,
                            persisted to data/decisions.json, compiled into
                            client-ready talking points
      │
  api/main.py + frontend/index.html ── an alternate JSON API + static workbench
                          serving the same Insight data, for anyone who wants to
                          drive it from a different frontend
```

## The golden worked example (the regression test)

**CL-0012, Cheung Kwok Wing** — 71, Income mandate, drawing ~USD 1.1m/year. His
Retirement Income portfolio made essentially no position trades, so its ~-USD 2.1m
move is pure market drift:

- **US Treasury 2.375% due 2045** — the largest detractor, cited to the mid-2026
  Fed-hold and 10-year-yield events (channel: `duration`).
- **Golden Harbour Properties perpetual** — down hard, but with **no matching
  event in the log → flagged `UNEXPLAINED`**, not guessed at.
- **A shipping/energy position** — *positive*, cited to the Strait of Hormuz
  closure (channel: `shipping`) — the same event that hurt others.

`tests/test_golden_cl0012.py` asserts this stays true — reconciliation exact to
the cent, the Treasury named as the top detractor, Golden Harbour unexplained,
and `explained_pct` present as a real number.

## Data invariants

- Five snapshots: `2025-12-31, 2026-02-27, 2026-03-31, 2026-06-30, 2026-08-26`.
  The interesting work is in the comparison, never a single snapshot.
- `event_log.csv` is authoritative for anything that happened in 2026 — the
  grounding layer cites it and only it, never the model's memory of the world.
- FX is implied per holding from `market_value_usd / market_value_local`, not
  looked up from a separate convention table.
- Some real-world data imperfections (lagged private-market valuations, a note
  that disagrees with the numbers) exist by design — the system is expected to
  surface them, not assume them away.

## Honest scope

The challenge brief frames its "directions" as a menu, not a checklist — depth on
fewer stories beats shallow coverage of all of them. Not yet built:

- **Scenario / stress testing** — "what if the Strait of Hormuz situation
  de-escalates or worsens" is not yet modelled as a shock over current exposures.
- **Tax-aware optimisation** — unrealised gains/losses and domicile are in
  `clients.csv`/`holdings.csv` but no engine reasons over them yet.
- **Life-event wealth planning** — objectives and cash needs are surfaced
  descriptively (client header, liquidity engine) but there's no dedicated
  life-stage insight generator.
- **Morning-Book (raw ΔUSD) sort** — the Book is currently ranked by the
  explainable urgency score only; a second "who moved the most money" sort isn't
  wired in yet.

Everything else in the three building blocks — explanation, hidden risk, mandate
governance, liquidity, and collateral — is built, grounded, and covered by
`pytest -q`.
