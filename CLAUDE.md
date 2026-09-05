# CLAUDE.md — Build Instructions

You are building the system described in `ARCHITECTURE.md`. Read that file first,
then this one. Build in the milestone order below. After every milestone, run its
acceptance check and do not proceed until it passes. Prefer small, verifiable diffs
over large rewrites.

A working starter already exists in this repo (the `Insight` contract, ingest, the
breadth engines, prioritise, a FastAPI layer, and a chip-based frontend). **Extend
it — do not rebuild from scratch.** Your job is mainly to (a) upgrade attribution
to exact reconciliation, (b) add the scoped-tool "Ask Why" chatbot, (c) add the
waterfall + Meeting Prep to the UI, and (d) build the scenario engine.

---

## Non-negotiable guardrails (apply to every milestone)

1. **The LLM never sees raw data.** It receives only computed `Insight` packets or
   the return values of scoped tools. It may not read CSVs, and it may not state a
   number, date, or cause that didn't come from the engine that turn. If you can't
   trace a sentence to a computed field, delete the sentence.
2. **Unexplained is flagged, never fabricated.** If no event tag overlaps a move,
   label it `UNEXPLAINED` and move on. Do not invent a plausible cause.
3. **Attribution must reconcile exactly.** `trading + price + fx == ΔMV_usd` for
   every holding and every portfolio total, to within floating-point tolerance.
   Assert it in code; a failing assert is a bug, not a rounding choice.
4. **Grounding is word-boundary against `event_log.primary_transmission`.** Never
   substring-match (it links "concentrated"→"rate"), never use world knowledge.
   The taxonomy lives in `config/grounding.py` and nowhere else.
5. **`schema.py` is the contract.** Extend fields additively; never rename or remove
   one without updating every producer and consumer. It is the seam two people work
   against — treat changes as breaking.
6. **Everything degrades without an API key.** No network, no key, or a failed call
   must fall back to deterministic template narration and a clear "chat unavailable"
   — never a crash or an empty screen.
7. **Keep the layers clean.** Finance maths in `engine/`, assembly in `build.py`,
   narration in `narrate/`, transport in `api/`. Don't compute finance in the UI or
   the API.

---

## Milestone 0 — Orient (no code)

- Read `ARCHITECTURE.md`. Read both source READMEs if present in `docs/`.
- Run the existing starter:
  ```bash
  pip install -r requirements.txt
  python run.py            # should print CL-0012 insights + the triage list
  ```
- **Acceptance:** `run.py` prints attribution/mandate/concentration insights for
  CL-0012 and a ranked triage. If it doesn't, fix imports/paths before continuing.

## Milestone 1 — Exact reconciliation in `src/engine/attribution.py`

Replace the current price-vs-flow logic with the three-way decomposition between
any two of the five snapshot dates:

```
trading_effect = (q1 - q0) * p0_local * fx0
price_effect   = q1 * (p1 - p0_local) * fx0
fx_effect      = q1 * p1_local * (fx1 - fx0)
```

- `fx0 = mv0_usd/mv0_local`, `fx1 = mv1_usd/mv1_local` (guard divide-by-zero).
- New position (no q0) or full exit (no q1) → pure trading effect.
- Add `reconcile()` that asserts `trading+price+fx == mv1_usd - mv0_usd` per holding
  and per portfolio (tolerance ~$1).
- Compute `explained_pct` = share of total **|price_effect|** whose holding links to
  ≥1 event in the window; the rest is the UNEXPLAINED residual. Return the list of
  UNEXPLAINED holdings too.

**Acceptance (golden test on CL-0012 / `PF-0014`, full window):**
- reconciliation assert passes for every holding and the total;
- essentially zero trading effect (no position trades);
- largest detractor is the **US Treasury 2045**, and it links to `duration` events;
- the **Golden Harbour perpetual** is in the UNEXPLAINED list (no matching event);
- `explained_pct` is reported (a number, not None).
Write this as `tests/test_golden_cl0012.py` so it stays green.

## Milestone 2 — Carry reconciliation into the Insight

In `src/build.py`, the attribution Insight's `contributions` become the
trading / price / FX effects and the top detractors; add `explained_pct` to the
Insight and put the UNEXPLAINED holdings into `caveats`. Regenerate `fixtures/`.

**Acceptance:** `GET /clients/CL-0012/insights` shows an attribution insight with
trading/price/fx contributions, an `explained_pct`, and an UNEXPLAINED caveat.

## Milestone 3 — Scoped "Ask Why" chatbot in `src/narrate/chat.py`

- Convert `narrate.py` into a `narrate/` package: keep the deterministic fallback as
  `template.py`; add `chat.py`.
- Implement a Claude tool-use loop (call → run tool locally → feed result → repeat,
  cap 5 rounds). Tools are **pre-scoped to the one open client** and return only
  computed facts:
  - `get_portfolio_attribution(t0, t1)` → the reconciliation JSON from M1
  - `get_rm_notes()` → this client's notes
  - `get_insights()` → this client's Insight[]
  - (stretch) `check_mandate_bands()`, `check_liquidity()`
- System prompt: forbid any number/cause not returned by a tool this turn; require
  naming the specific event when citing one; require flagging any RM-note-vs-data
  contradiction. Wrap `truststore.inject_into_ssl()` for corporate proxies; on any
  error or missing key return `None`, never raise.
- Add `POST /clients/{id}/chat` to `api/main.py`.

**Acceptance:** with a key, "why is CL-0012 down?" returns an answer that cites the
Treasury→duration events, calls the Golden Harbour move UNEXPLAINED, and invents no
number; try to bait it into naming another client's holding — it must refuse. With
no key, the endpoint returns a clean "chat unavailable" and the rest still works.

## Milestone 4 — Unify the Book in `src/engine/prioritise.py`

Support two sorts over the same insight set: `sort=urgency` (the explainable score,
default) and `sort=move` (per-portfolio, ranked by absolute USD change — the
"Morning Book"). Expose via `GET /triage?sort=...`.

**Acceptance:** both sorts return ranked lists; each row carries its explanation
(top reason for urgency; ΔUSD for move).

## Milestone 5 — Frontend: waterfall + Ask Why + Meeting Prep

In `frontend/index.html` (or a React port against the same JSON):
- Client view: add a **trading / price / FX / total waterfall** for the selected
  window, alongside the existing provenance chips.
- Add an **Ask Why** chat panel bound to the open client, hitting `/chat`.
- Add a **Meeting Prep** view: the insights the RM **Accepted** compiled into
  client-ready talking points. Wire Accept/Modify/Reject to persist the choice.

**Acceptance:** Book → Client → Ask Why → Meeting Prep flows end to end on the demo
clients; every number/event is inspectable in one click; Accept moves an insight
into Meeting Prep.

## Milestone 6 — `src/engine/scenario.py`

Apply a shock vector (e.g. Strait reopens: energy/shipping down, duration relief;
Strait worsens: the reverse) to the current exposures and emit a normal `Insight`
per affected client. Reuse the grounding tags to decide what each shock touches.

**Acceptance:** a scenario insight appears for at least CL-0019 and CL-0015 with a
defensible, reconciled estimate and a stated confidence.

## Milestone 7 — Polish

Regenerate `fixtures/`, run the golden test, confirm graceful no-key degradation,
and verify the three demo clients (CL-0012, CL-0002, CL-0014/CL-0019) each tell a
clean 2–4 insight story.

---

## Commands

```bash
python run.py [CLIENT_ID] [--llm]        # CLI smoke test / golden checks
pytest -q                                # run tests (add golden test in M1)
uvicorn api.main:app --port 8000         # API; open frontend/index.html
export ANTHROPIC_API_KEY=...             # enable chat + LLM narration (optional)
```

## Definition of done

All three building blocks demonstrable on the golden clients; attribution
reconciles exactly and reports explained% with honest UNEXPLAINED flags; the
Ask-Why chat is grounded and cannot fabricate; the workbench flows Book → Client →
Ask Why → Meeting Prep with one-click provenance; everything still runs with no API
key.
