"""
mandate.py — is each portfolio inside the bands it agreed to respect?

Two checks:
  1. asset-class allocation vs [min_pct, max_pct] from mandates.csv
  2. any single position over max_single_position_pct

We also look at the RM notes: a breach the client explicitly instructed (a
"waiver"/"confirmed in writing") is governance-different from silent drift.
We surface that distinction rather than hiding it — the README calls this out.
"""
from __future__ import annotations
import pandas as pd
from src.ingest import DataStore, TODAY

_WAIVER_HINTS = ("waiver", "in writing", "confirmed the instruction",
                 "acknowledged", "instructed")


def _client_directed(store: DataStore, client_id: str) -> bool:
    notes = " ".join(n["note"].lower() for n in store.notes_of(client_id))
    return any(h in notes for h in _WAIVER_HINTS)


def check_portfolio(store: DataStore, portfolio_id: str,
                    snapshot: str = TODAY) -> list[dict]:
    pf = store.portfolios[store.portfolios.portfolio_id == portfolio_id]
    if not len(pf):
        return []
    pf = pf.iloc[0]
    bands = store.mandates[store.mandates.mandate_code == pf.mandate_code]
    if not len(bands):
        return []

    h = store.holdings[(store.holdings.portfolio_id == portfolio_id) &
                       (store.holdings.snapshot_date == snapshot)]
    total = h.market_value_usd.sum()
    if total <= 0:
        return []

    breaches = []
    by_class = h.groupby("asset_class").market_value_usd.sum()
    for _, band in bands.iterrows():
        actual_pct = round(100 * by_class.get(band.asset_class, 0.0) / total, 1)
        if actual_pct < band.min_pct:
            breaches.append(_b(portfolio_id, band, actual_pct, "below", pf.mandate_code))
        elif actual_pct > band.max_pct:
            breaches.append(_b(portfolio_id, band, actual_pct, "above", pf.mandate_code))

    # single-position limit
    max_single = bands.iloc[0].max_single_position_pct
    for _, row in h.iterrows():
        pos_pct = round(100 * row.market_value_usd / total, 1)
        if pos_pct > max_single:
            breaches.append({
                "portfolio_id": portfolio_id, "kind": "single_position",
                "rule_id": f"{pf.mandate_code}:max_single_position={max_single}%",
                "instrument": row.instrument_name, "instrument_id": row.instrument_id,
                "actual_pct": pos_pct, "limit_pct": max_single,
                "client_directed": _client_directed(store, pf.client_id),
            })
    return breaches


def _b(pid, band, actual, direction, code):
    limit = band.min_pct if direction == "below" else band.max_pct
    return {
        "portfolio_id": pid, "kind": "allocation_band",
        "rule_id": f"{code}:{band.asset_class}:[{band.min_pct},{band.max_pct}]",
        "asset_class": band.asset_class, "actual_pct": actual,
        "direction": direction, "limit_pct": limit,
    }


def check_client(store: DataStore, client_id: str, snapshot: str = TODAY) -> list[dict]:
    out = []
    for pid in store.portfolios_of(client_id).portfolio_id:
        out.extend(check_portfolio(store, pid, snapshot))
    return out
