"""
liquidity.py — can the client actually meet what's coming?

Match near-term demands (uncalled commitments + planned cash needs) against what
is genuinely sellable soon. liquidity_tier tells us how fast a holding turns into
cash; anything gated/illiquid does not count as ready liquidity. A gap here is
exactly the kind of thing that only appears when you combine files.
"""
from __future__ import annotations
import pandas as pd
from src.ingest import DataStore, TODAY

READY_TIERS = {"Daily", "Weekly"}          # sellable within ~a week
NEAR_TIERS = {"Daily", "Weekly", "Monthly"}


def check_client(store: DataStore, client_id: str, snapshot: str = TODAY) -> dict:
    h = store.holdings_of(client_id, snapshot)
    ready = h[h.liquidity_tier.isin(READY_TIERS)].market_value_usd.sum()
    near = h[h.liquidity_tier.isin(NEAR_TIERS)].market_value_usd.sum()
    gated = h[~h.liquidity_tier.isin(NEAR_TIERS)]

    commits = store.commitments[store.commitments.client_id == client_id]
    uncalled = commits.uncalled.sum() if len(commits) else 0.0

    needs = store.cash_needs[store.cash_needs.client_id == client_id]
    need_total = needs.amount.sum() if len(needs) else 0.0

    demand = uncalled + need_total
    return {
        "ready_liquidity_usd": round(ready),
        "near_liquidity_usd": round(near),
        "uncalled_commitments_usd": round(uncalled),
        "planned_cash_needs_usd": round(need_total),
        "total_demand_usd": round(demand),
        "gap_usd": round(ready - demand),
        "gated_positions": [
            {"instrument_name": r.instrument_name, "tier": r.liquidity_tier,
             "mv_usd": round(r.market_value_usd)}
            for _, r in gated.iterrows()
        ],
        "needs": needs[["description", "currency", "amount", "due_from",
                        "certainty"]].to_dict("records") if len(needs) else [],
    }
