# Track A — Intelligence Engine

**Your mission:** produce correct, defensible `Insight` objects. You own the half
of the score that says *"reasoning you can defend."* If a judge clicks a chip and
the number is wrong, or an event is linked that shouldn't be, that's on this track.

**Rubric you own:** Technical & Operational Feasibility (25%) + the substance
behind Client-Centric Innovation (25%).

---

## What you own

```
src/schema.py            # THE CONTRACT — you steward it, but see "the seam" below
src/ingest.py            # data loading
src/engine/attribution.py
src/engine/ground.py
config/grounding.py      # the auditable event↔instrument taxonomy
src/engine/mandate.py
src/engine/concentration.py
src/engine/collateral.py
src/engine/liquidity.py
src/build.py             # assembles engine output into Insights
src/engine/prioritise.py # the triage score
run.py                   # your test harness
```

Don't touch `frontend/`, `api/main.py`, or `src/engine/narrate.py` without a sync —
those are Track B.

## The seam (read this once, it's the whole reason we can work in parallel)

The interface between us is **`schema.py`'s `Insight` object**. You produce it, B
renders it. As long as your engines emit valid Insight JSON, B never waits on you
and you never wait on B.

- **The schema is frozen.** Any change to `Insight`'s fields is a joint decision
  with B — a silent rename breaks their UI. Ping before editing `schema.py`.
- **`fixtures/` is your handoff.** I've already dumped `insights_CL-0012.json`,
  `_CL-0002`, `_CL-0014`, `_CL-0019` and `triage.json`. Whenever your output
  changes materially, regenerate them (`python -c` snippet in the main README) and
  commit — that's how B stays current without running your code.

## Your tasks, in priority order

1. **Harden the 3 demo clients first, not all 20.** Make CL-0012, CL-0002 and
   CL-0014/CL-0019 produce genuinely *sharp* insights. Specifically:
   - CL-0012: surface that two detractors are **perpetuals (no maturity)** — "wait
     for par" isn't even theoretically possible. That's a hand-added caveat/point.
   - CL-0014 / CL-0019: the look-through "same bet" story — verify the structured
     product's `underlying_reference` actually aggregates into the theme exposure.
   - CL-0002: the collateral trajectory — confirm the LTV climbs *because he drew
     more as tech fell* (cross-reference RM note N-004).
2. **Calibrate confidence + caveats.** The judges reward honesty about uncertainty.
   Private-markets valuations lag a quarter (`liquidity_tier` = Quarterly/Illiquid)
   → those insights should be `medium` confidence with a stated caveat. Get this
   right; it's cheap points most teams miss.
3. **Build `src/engine/scenario.py`** — the Strait-reopens / worsens question from
   the README. Apply a shock vector to energy/shipping/duration exposures and emit
   a normal `Insight`. This is a differentiator; no one else will model it.
4. **Guard the grounding taxonomy.** `config/grounding.py` is where credibility
   lives. When you add instruments/events, verify links are *tight* (we already
   fixed one substring bug where "concentrated" matched "rate"). Word-boundary only.
5. **(Stretch) `src/engine/tax.py`** — unrealised gains/losses across a household by
   `tax_domicile`. Data's in `holdings.unrealised_pnl_base` + `clients`.

## How to run & verify your slice

```bash
python run.py                # CL-0012 + triage, offline
python run.py CL-0002        # any client
```

Sanity-check every number you put on a chip by hand against the CSVs at least once.
"Not a maths test" doesn't mean the maths can be wrong — it means a *defensible*
wrong-by-rounding beats a confident fabrication. Never fabricate.

## Definition of done

- The 3 demo clients each produce 2–4 insights that would survive a judge asking
  "why does it say that?" — every claim traceable to a number + an event/rule.
- `scenario.py` emits at least one Insight for the Strait question.
- Fixtures regenerated and committed so B's UI shows your latest output.
