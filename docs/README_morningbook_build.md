# Wealth Intelligence — Morning Book & "Ask Why" Chatbot

A SingHacks 2026 (Julius Baer) submission for **Building Block #1: Intelligent
Portfolio Explanations**. Turns the RM's morning routine — "who moved, and
why?" — into a chat-driven workflow, grounded entirely in a deterministic
attribution engine so the LLM can narrate but never invent a number.

## The idea

Everyone building this challenge can diff two snapshots and ask an LLM to
narrate the change. That produces confident-sounding prose that is, at best,
unverifiable and, at worst, a hallucinated cause dressed up as an insight.

This build instead treats explanation as two separable problems:

1. **What happened, exactly** — pure arithmetic over `holdings.csv`,
   `transactions.csv` and `event_log.csv`. No LLM involved. Every dollar is
   reconciled: trading effect + market-price effect + FX effect always sums
   exactly to the observed change, with no fudge/residual term.
2. **What it means, in conversation** — Claude, called only through two
   scoped tools that return the already-computed facts above. The model
   restyles and reasons over facts it was handed; it cannot state a figure,
   date, or cause that didn't come back from a tool call in that turn.

The result: when the event log genuinely doesn't explain a move (e.g. a Hong
Kong property bond sliding with no matching event), the system says so —
"unexplained, flag for RM review" — instead of free-associating a plausible-
sounding cause. That honesty is deliberate: it's the difference between an
explanation an RM can defend to a client and one that just sounds fluent.

## Architecture

```
data/*.csv, rm_notes.json
        │
        ▼
attribution.py    — deterministic decomposition + event-tag matching
        │                (pandas only, no network, fully auditable)
        ▼
narrate.py        — template narration (always works, zero dependencies)
        │
        ▼
chatbot.py        — Claude tool-use loop, scoped to one client/portfolio
        │                (falls back to nothing if no API key / no network)
        ▼
app.py            — Streamlit UI: Morning Book → Investigate → Ask Why
```

### `data_loader.py`
Loads and joins the CSVs/JSON once per session (`st.cache_data`). Nothing
clever — mirrors `starter/quickstart.py`'s loading pattern.

### `attribution.py` — the auditable core
- `decompose_portfolio(data, portfolio_id, t0, t1)` — per-holding exact
  decomposition between two of the five snapshot dates:
  - `trading_effect = (qty1 - qty0) * price0_local * fx0`
  - `price_effect = qty1 * (price1_local - price0_local) * fx0`
  - `fx_effect = qty1 * price1_local * (fx1 - fx0)`
  - These three sum exactly to the USD value change — no residual fudge term.
  - `fx0`/`fx1` are implied from `market_value_usd / market_value_local`
    already in `holdings.csv`, so no separate FX-convention lookup is needed.
  - New positions / full exits are handled as pure trading effects.
- `instrument_tags(row)` — transparent, editable heuristics mapping an
  instrument to event-log themes (e.g. government bonds → `duration`;
  Quarterly-Gate alternatives → `private credit, semi-liquid alternatives`).
  Deliberately **not** based on raw `asset_class`/currency fields, which are
  too coarse and were found (and fixed) to cause false matches — e.g. "Golden
  Harbour" bonds initially matched *gold-price* events by substring, and a
  generic "fixed income" tag matched every bond to any event mentioning
  "long-duration fixed income". Matching now requires word-boundary overlap
  against `event_log.csv`'s own `primary_transmission` field — never free-text
  guessing about the world.
- `match_events(...)` — events within the snapshot window whose tags overlap
  the instrument's tags, ranked by severity then date.
- `book_overview(data, t0, t1)` — one row per portfolio, ranked by absolute
  dollar move. This is the "Morning Book."

### `narrate.py`
- `build_facts(...)` — turns totals + per-holding rows into a structured fact
  sheet (headline, decomposition, confidence split, ranked bullet citations).
- `render_template(...)` — deterministic prose from those facts. Works with
  zero external dependencies; this is what you see if there's no API key.
- `try_llm_narration(...)` — optional persona-restyled version, used by the
  older single-shot explanation view. Wraps `truststore.inject_into_ssl()` so
  it works behind an SSL-inspecting corporate proxy, and fails silently to
  the template on any error.

### `chatbot.py` — the "why?" conversation
- Two tools, both **pre-scoped to the one client/portfolio already open in
  the UI** — Claude cannot query other clients:
  - `get_portfolio_attribution(t0, t1)` → the decomposition JSON above
  - `get_rm_notes()` → Priscilla's notes for this client
- `SYSTEM_PROMPT` hard-forbids stating any number/cause not returned by a
  tool this turn, requires naming the specific event when citing one, and
  requires flagging RM-notes-vs-data contradictions explicitly.
- `run_chat_turn(...)` runs the standard Anthropic tool-use loop (call →
  execute tool locally → feed result back → repeat, capped at 5 rounds).
- `get_client()` returns `None` (never raises) if `ANTHROPIC_API_KEY` isn't
  set or the network call fails — the rest of the app keeps working.

### `app.py` — the UI, three sections
1. **Morning book** — ranked table of every portfolio's move over a chosen
   window (`Since` → `As of`). Click a row (or use the sidebar) to drill in.
2. **Investigate** — start/end value, waterfall chart (trading/price/FX/
   total), the deterministic explanation, an auditable per-holding table, and
   RM notes — all usable with zero API dependency.
3. **Ask why** — the chat. Disabled with a clear message if no API key is
   configured; otherwise a normal chat scoped to the selected client.

## Running it

```powershell
pip install -r requirements.txt          # pandas, streamlit, plotly
pip install anthropic truststore          # only needed for the chat
$env:ANTHROPIC_API_KEY = "sk-ant-..."     # optional — chat degrades gracefully without it
python -m streamlit run app/app.py --server.port=8523
```

Behind a corporate SSL-inspecting proxy, `pip install` may need:
```powershell
pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org <package>
```

## The worked example baked into the data

Client **CL-0012, Cheung Kwok Wing** (71, retired, drawing USD 1.1m/year) is
the flagship demo case — his RM note (`N-016`) almost verbatim matches the
challenge README's own worked example. His `Retirement Income Mandate`
portfolio made **zero trades** across all five snapshots, so its entire
USD 2.10m (-7.0%) decline is pure market drift:

- **-USD 1.02m**, US Treasury 2.375% due 2045 — correctly cited to the June
  2026 Fed-hold / 10-year-yield events (channel: `duration`)
- **-USD 640k**, Golden Harbour Properties perpetual bond — **no matching
  event exists in the log, so it's flagged `UNEXPLAINED`**, not guessed at
- **+USD 360k**, Pacific Orient Shipping — cited to the Strait of Hormuz
  closure (channel: `shipping`) — the same event that hurt everything else,
  helping this one

Net: **59% of the price-driven move traces to a specific logged event, 41%
is honestly flagged as residual.** That split, and the citations behind it,
are what the chatbot is grounded in when the RM asks "why is this down?"

## Known limitations / next steps
- `st.dataframe`'s click-to-select on the Morning Book uses Streamlit's
  built-in `on_select` API; verify it manually before a live demo since it's
  a canvas-rendered grid, not plain DOM (the sidebar dropdown is the
  guaranteed fallback for switching clients).
- The instrument→event tag heuristics in `instrument_tags()` are hand-built
  for this dataset's 16 events and 62 instruments — intentionally simple and
  editable rather than a general NLP matcher, in keeping with the
  "auditable, not free-associating" design goal.
- The chat currently offers two tools (attribution + RM notes). Natural
  extensions: a `check_mandate_bands()` tool and a `check_liquidity()` tool,
  following the same pattern — deterministic function, Claude only narrates.
