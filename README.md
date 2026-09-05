# Janus — RM Wealth Intelligence

Janus turns a relationship manager's raw portfolio data into a small number of
specific, defensible statements about what happened in a client's portfolio,
why, and what to do about it — built for the Julius Baer Wealth Intelligence
challenge (SingHacks 2026).

## The one principle everything obeys

> **A deterministic engine reasons. A language model only narrates. Nothing on
> screen is a number or a cause the engine didn't compute and cite. When the
> data can't explain a move, the system says "unexplained" — it never
> free-associates a cause.**

Every dollar of portfolio change is decomposed by exact arithmetic — trading,
price, and currency effects that sum *exactly* back to the observed change,
asserted in code, not eyeballed. Every cause cited alongside it comes from a
transparent, word-boundary match against the bank's own event log, never from
a model's memory of the world. An LLM (OpenAI's `gpt-4o`) is used only
downstream of that computation, to turn already-cited facts into prose or to
answer a question — and it is structurally unable to introduce a fact that
wasn't already in front of it.

## Quickstart

```bash
pip install -r requirements.txt
python run.py                 # CLI smoke test: CL-0012 + the triage list
python app.py                 # the full workbench, one command, http://localhost:8080
```

Optional, for LLM narration, the "Ask Why" chat, and market-context search:

```bash
export OPENAI_API_KEY=sk-...
```

Everything works without it — narration falls back to a deterministic
template, and the chat/search features report themselves as unavailable
rather than failing.

A lighter-weight alternative surface also exists — a static HTML page behind
a plain JSON API:

```bash
uvicorn api.main:app --reload
# open frontend/index.html
```

## How it works

```
data/*.csv + rm_notes.json   (12 files, immutable, as provided)
        │
        ▼
   src/ingest.py             loads everything once into a DataStore
        │
        ▼
┌─── src/engine/ ─────────────────────────────────────────────────┐
│  DETERMINISTIC. No LLM. Returns findings, never prose.           │
│                                                                   │
│  attribution.py   exact trading + price + FX decomposition       │
│  mandate.py       allocation-band & single-position breaches     │
│  concentration.py cross-portfolio look-through concentration     │
│  collateral.py    loan-to-value trajectory vs. margin call       │
│  liquidity.py     commitments & cash needs vs. what's sellable   │
│  ground.py        links a move to event_log.csv (config/         │
│                   grounding.py's tag taxonomy, word-boundary      │
│                   matched — no tag overlap means UNEXPLAINED)     │
└───────────────────────────────────────────────────────────────────┘
        │
        ▼
   src/schema.py              the Insight contract (see below)
   src/build.py               assembles engine findings into Insights
   src/engine/prioritise.py   explainable urgency score across the book
        │
        ▼
┌─── src/engine/narrate/ ──────────────────────────────────────────┐
│  template.py   deterministic prose from an Insight. No deps.      │
│                Always works — the fallback for everything below.  │
│  __init__.py   narrate(): template, or GPT-4o under the same       │
│                guardrail prompt, per Insight.                     │
│  chat.py       "Ask Why" — a tool-calling loop scoped to ONE       │
│                client. 5 tools, all closed over that client_id.   │
│  market_context.py   QUARANTINED live web search for external     │
│                       color. Never touches an Insight's numbers.  │
└───────────────────────────────────────────────────────────────────┘
        │
        ▼
   api/main.py     FastAPI JSON layer  ──►  frontend/index.html
   app.py          NiceGUI workbench (the primary surface) ──► data/decisions.json
```

## The Insight contract

Every finding from every engine becomes one `Insight` object
(`src/schema.py`) before it reaches narration or the UI:

| Field | Purpose |
|---|---|
| `headline` | the one-line finding |
| `contributions[]` | the computed numbers behind it — renders as inspectable chips |
| `evidence` | event references, holding IDs, mandate rule IDs, the RM note on file |
| `confidence` | `high` / `medium` / `low` |
| `caveats[]` | honesty about what the number doesn't cover |
| `suggested_action` | a proposal, not an instruction — the RM decides |
| `explained_pct` | attribution only: share of the price move traced to a real event |
| `narrative` | filled in last, by `narrate/`, from the fields above only |

If a fact can't be traced to a field on this object, it doesn't get said.

## The five engines

- **Attribution** — between any two of the five dated snapshots, decomposes
  each holding's dollar change into `trading_effect + price_effect +
  fx_effect`, which reconciles exactly to the observed change (asserted, with
  a golden regression test). Reports what share of the move is grounded to a
  real event and flags the rest as unexplained rather than guessing.
- **Mandate** — checks each portfolio against its allocation bands and
  single-position limit, and distinguishes a breach the client explicitly
  directed (a waiver noted by the RM) from silent drift.
- **Concentration** — aggregates a client's exposure across their *entire*
  book, looking through structured products to what they're actually exposed
  to, to catch the same bet sitting in more than one wrapper.
- **Collateral** — traces loan-to-value across all five snapshots against
  each facility's margin-call trigger.
- **Liquidity** — matches uncalled commitments and planned cash needs against
  what's genuinely sellable soon, given each holding's liquidity tier.

## Narration, Ask Why, and Market Context

Three distinct ways a language model can touch this system, in increasing
order of how much it's allowed to do — and one thing it can never do, which
is see a raw CSV.

1. **Narration** (`narrate/__init__.py`) takes one finished `Insight` and
   writes 2–4 RM-facing sentences from it. Falls back to
   `narrate/template.py` with no API key, no network, or any model error —
   never a crash.
2. **Ask Why** (`narrate/chat.py`) is a conversational tool-calling loop
   scoped to exactly one client. Its five tools (`get_portfolio_attribution`,
   `get_rm_notes`, `get_insights`, `check_mandate_bands`, `check_liquidity`)
   are each closed over one `client_id` at creation time, so there is no
   code path for it to answer about a different client. Capped at 5 tool
   rounds; returns `None` on any failure, which callers turn into a plain
   "chat unavailable."
3. **Market Context** (`narrate/market_context.py`) is deliberately
   quarantined from all of the above — `src/build.py` and `src/schema.py`
   never import it. It runs a live web search restricted to eight named
   financial-press domains (Reuters, Bloomberg, WSJ, FT, CNBC, AP,
   MarketWatch, The Economist — no social media, no forums), and its output
   never feeds an Insight's `contributions`, `explained_pct`, or
   `suggested_action`. The workbench always labels it "unverified — not used
   in the numbers above."

All three currently call OpenAI's `gpt-4o`; the model call is isolated
enough in each file to swap providers without touching the guardrail logic.

## The RM Workbench (`app.py`)

One command (`python app.py`) launches the primary surface: a three-column
NiceGUI app.

- **Left rail** — the triage queue, every client ranked by an explainable
  urgency score with its top reason shown inline.
- **Center panel** — the selected client's Insight cards: computed metrics,
  event-log citations, caveats, and the RM's own note on file, followed by an
  **Accept / Modify / Reject** row and a **Market Context Enquiry** drawer
  that runs Ask Why and the external search concurrently.
- **Right rail** — the RM Decision Log: every Accept/Modify/Reject persisted
  to `data/decisions.json`, filterable, undoable, and doubling as a
  Meeting Prep sheet of client-ready talking points.

The static alternative (`api/main.py` + `frontend/index.html`) exposes the
same engine as plain JSON (`/clients`, `/triage`, `/clients/{id}/insights`,
`POST /clients/{id}/chat`, `POST /clients/{id}/market-context`) behind a
lighter, clickable-provenance-chip page, for anyone who wants a different UI
on the same contract.

## Data notes

- Five snapshots: `2025-12-31, 2026-02-27, 2026-03-31, 2026-06-30,
  2026-08-26`. The interesting work is always in the comparison between two
  of them, never a single one.
- `event_log.csv` is the authoritative source for anything that happened in
  2026 — the grounding layer cites it and only it.
- Every holding's implied FX rate is derived from `market_value_usd /
  market_value_local`; there's no separate FX-convention lookup to get wrong.
- The dataset contains a small number of realistic data imperfections by
  design — they're meant to be handled, not assumed away.

## Repository layout

```
data/            the 12 source CSVs + rm_notes.json (read-only) + decisions.json (written by app.py)
config/          config/grounding.py — the event↔instrument tag taxonomy
src/ingest.py    loads data/ once into a DataStore
src/schema.py    the Insight contract
src/build.py     assembles engine output into narrated Insights
src/engine/      the five deterministic engines + prioritise.py + narrate/
api/main.py      FastAPI JSON layer
frontend/        static HTML alternative surface
app.py           the primary NiceGUI workbench
fixtures/        pre-generated Insight/triage JSON for the 4 demo clients
tests/           the golden regression test for CL-0012
scripts/         gen_fixtures.py — regenerate fixtures/ after an engine change
```

