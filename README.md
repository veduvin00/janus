<<<<<<< HEAD
# JB Wealth Intelligence — RM Workbench (starter)

An **intelligence layer between portfolio data and the Relationship Manager**. It
does the interpreting a plain dashboard leaves to the human: it computes what
moved and why, grounds every explanation in the authoritative event log, flags
hidden risk, and hands Priscilla a prioritised, defensible, *traceable* set of
actions she stays in control of.

The design principle, top to bottom: **the deterministic engine reasons; the LLM
only narrates.** Every claim on screen is backed by a computed number and a real
event — nothing is free-associated. That is what makes it defensible in a
compliance review, and it is the thing most hackathon teams will skip.

## Quickstart

```bash
pip install -r requirements.txt

# 1. prove the engine works, offline, no API key needed:
python run.py                 # CL-0012 (the bond retiree) + the triage list
python run.py CL-0002         # any client

# 2. run the workbench:
uvicorn api.main:app --port 8000
#    then open frontend/index.html in a browser
```

LLM narration is **optional**. With no key it uses a deterministic template so the
demo always runs. To narrate with a real model: `export ANTHROPIC_API_KEY=...`
and run `python run.py --llm` (or start the API with the key set).

## Architecture

```
data/ (the 12 files)
      │
  ingest.py ─ load once into a DataStore (USD throughout)
      │
  engine/ ── DETERMINISTIC. each returns findings, never prose:
      ├ attribution.py  price-effect vs flow-effect across the 5 snapshots
      ├ ground.py       link moves to event_log via an auditable tag taxonomy
      │                 (config/grounding.py) — word-boundary, not substring
      ├ mandate.py      allocation-band + single-position breaches (drift vs directed)
      ├ concentration.py look-through structured products + cross-portfolio aggregation
      ├ collateral.py   LTV trajectory vs margin-call trigger
      └ liquidity.py    commitments + cash needs vs what's actually sellable
      │
  schema.py ─ THE SPINE. every finding becomes an Insight:
              { headline, contributions[], evidence{events,holdings,rules,rm_note},
                confidence, caveats[], suggested_action, narrative }
      │
  build.py ── wraps engine output into Insights, attaches the matching RM note
  prioritise.py ─ explainable urgency score → the call list
  narrate.py ─ LLM constrained to the packet (or deterministic fallback)
      │
  api/main.py ─ serves Insight objects as JSON
  frontend/index.html ─ workbench: triage → client → cards where every number
                        and event is a CLICKABLE PROVENANCE CHIP
```

## The one thing to protect

`schema.py`'s `Insight` is the contract the whole team codes against. Engines
*produce* it, the narrator and UI *consume* it. If a new capability can't express
its output as an `Insight` with real `contributions` and `evidence`, it isn't
ready for the screen. Keep finance maths in `engine/`, keep assembly in `build.py`,
keep the LLM downstream of both.

## Where to extend (in priority order)

1. **Scenario analysis** (`engine/scenario.py`) — the README's Strait-reopens /
   worsens question. Re-price energy/shipping/duration exposures under a shock
   vector; emit an `Insight` like everything else.
2. **Tax-aware optimisation** — unrealised gains/losses across a household, by tax
   domicile. Data is in `holdings` (`unrealised_pnl_base`) + `clients.tax_domicile`.
3. **Richer narration** — feed the full packet to the model; tune the guardrail
   prompt in `narrate.py`. The contract doesn't change.
4. **Swap the frontend** — React against the same JSON. The API is UI-agnostic.

## Demo path (go deep, not wide)

- **CL-0012** Cheung Kwok Wing — 71, income mandate. Bonds down on price alone,
  grounded to the Fed/10-year events; won't sell at a loss but the longest bond
  matures 2045 and two holdings are perpetuals. The attribution + RM-note tension.
- **CL-0002** Ravi Chandrasekaran — tech-founder LTV squeeze; drew *more* as
  collateral got most volatile (RM note N-004). The collateral trajectory story.
- **CL-0014 / CL-0019** — hidden concentration: "the perpetual, the shares, the
  accumulator and his own business are the same bet." The look-through story.

## Data note

Synthetic data, calibrated to real 2026 market history. For anything that
happened in 2026, `event_log.csv` is authoritative — the grounding layer only
ever cites it, never the model's own memory.
=======
# janus
Singhacks hackathon
>>>>>>> a6965fc7152606a32d796e4e5908b4e6b1ea9e26
