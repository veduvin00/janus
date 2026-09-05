# Track B — Workbench & Narrative

**Your mission:** turn the engine's `Insight` objects into something a judge
*feels*. You own the demo. The provenance-chip interaction and the
signal→understanding→decision→engagement story are what win the room.

**Rubric you own:** User Experience & Design (25%) + Strategic Impact (25%).

---

## What you own

```
frontend/index.html        # the RM workbench (starter is functional; make it sing)
api/main.py                # thin JSON layer — extend as you need
src/engine/narrate.py      # the LLM narrator + guardrail prompt
docs/deck + demo script    # you create these
```

Don't touch `src/engine/*` (except `narrate.py`) or `config/grounding.py` — that's
Track A's finance logic. If an insight's *numbers* look wrong, tell A; don't patch
it in the UI.

## The seam (why you're never blocked)

You render Track A's **`Insight` object** (`src/schema.py`). You do **not** need
the engine running to build the UI — use the static files in `fixtures/`:

```js
// build the whole frontend against these, no backend needed:
fixtures/triage.json
fixtures/insights_CL-0012.json   // + CL-0002, CL-0014, CL-0019
```

When A updates the engine they'll regenerate these. Point your fetch at the API
(`/triage`, `/clients/{id}/insights`) once you're ready to integrate; the JSON
shape is identical.

## Your tasks, in priority order

1. **Make the provenance chip the hero.** It already works (click a number → see
   how it was derived; click ⚑ → see the event_log entry). Polish it until it's
   instant and obvious — this single interaction *is* your "traceability" score.
   Every claim on screen must be inspectable in one click.
2. **Build the third screen: Meeting Prep.** Triage → Client → **Meeting Prep**.
   It compiles the insights the RM *accepted* into a client-ready talking-points
   sheet. This closes the loop the README draws (insight → RM decision → client
   conversation) and is the natural end of the demo.
3. **Wire Accept / Modify / Reject.** Right now the buttons are inert. Persist the
   RM's choice (in-memory or a tiny endpoint) so accepted insights flow into
   Meeting Prep. This *is* your "human in the loop" score — make it visible.
4. **Turn on real LLM narration.** `narrate.py` has the guardrail prompt and a
   deterministic fallback. Set `ANTHROPIC_API_KEY`, tune the system prompt so the
   narratives read like something Priscilla would actually say — and stress-test
   that it *cannot* add a fact outside the packet (try to make it hallucinate; if
   it can, tighten the prompt). Governance is the point.
5. **The deck + 3-minute demo script.** Don't over-explain the problem (README says
   so). Open on the triage list ("one RM, 20 clients, who first?"), drill into
   CL-0012, click a chip to show the grounding, accept an action, land on Meeting
   Prep. Rehearse the click path.

## How to run & verify your slice

```bash
# no backend:
open frontend/index.html          # (point API const at fixtures if you stub fetch)
# with backend:
uvicorn api.main:app --port 8000  # then open frontend/index.html
```

## Design notes (UX is 25% — spend the polish here)

- Private-banking restraint: the starter palette (ink/slate, one bronze accent,
  serif headers) is intentional — keep it calm and premium, not a rainbow dashboard.
- Show *fewer* things well. A card with one inspectable claim beats six vague ones.
- The confidence badge and caveat line are features, not clutter — they're the
  "honesty about uncertainty" the judges explicitly reward. Give them room.

## Definition of done

- Triage → Client → Meeting Prep flows end to end, clickable, on the demo clients.
- Every number and event on screen is inspectable in one click.
- Accept flows an insight into Meeting Prep; the RM is visibly in control.
- LLM narration on, guard-railed, and rehearsed — plus a deck and a timed script.
