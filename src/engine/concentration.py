"""
concentration.py — the hidden-risk engine.

Two things a single-portfolio view misses, both of which the README flags:

  1. CROSS-PORTFOLIO aggregation: a client with several portfolios can be
     concentrated in a theme that looks fine inside each one.
  2. LOOK-THROUGH: a structured product's asset_class only says what it's
     *called*. instruments.underlying_reference says what it's exposed to.
     We attribute the note's value to its underlying theme.

We aggregate a client's whole book into theme exposures (via the same tags used
for grounding) and surface any theme above a threshold of total wealth.
"""
from __future__ import annotations
import pandas as pd
from src.ingest import DataStore, TODAY
from src.engine.ground import instrument_tags

THEME_THRESHOLD_PCT = 20.0   # flag a theme above this share of the client's book


def theme_exposures(store: DataStore, client_id: str,
                    snapshot: str = TODAY) -> pd.DataFrame:
    h = store.holdings_of(client_id, snapshot).copy()
    total = h.market_value_usd.sum()
    if total <= 0:
        return pd.DataFrame()

    # tag every holding (look-through happens inside instrument_tags via
    # underlying_reference), then attribute its full MV to each of its themes.
    records = []
    for _, r in h.iterrows():
        tags = instrument_tags(r)
        for t in (tags or {"untagged"}):
            records.append({"theme": t, "mv_usd": r.market_value_usd,
                            "instrument_id": r.instrument_id,
                            "instrument_name": r.instrument_name,
                            "portfolio_id": r.portfolio_id})
    df = pd.DataFrame(records)
    agg = (df.groupby("theme").mv_usd.sum()
             .sort_values(ascending=False).reset_index())
    agg["pct_of_book"] = (100 * agg.mv_usd / total).round(1)
    agg["total_book_usd"] = round(total)
    return agg


def flag_concentrations(store: DataStore, client_id: str,
                        snapshot: str = TODAY) -> list[dict]:
    agg = theme_exposures(store, client_id, snapshot)
    if not len(agg):
        return []
    out = []
    for _, r in agg.iterrows():
        if r.theme == "untagged":
            continue
        if r.pct_of_book >= THEME_THRESHOLD_PCT:
            # which instruments and how many portfolios drive it (the hidden part)
            drivers = _drivers(store, client_id, snapshot, r.theme)
            out.append({
                "theme": r.theme, "pct_of_book": r.pct_of_book,
                "mv_usd": round(r.mv_usd), "total_book_usd": int(r.total_book_usd),
                "n_portfolios": drivers["n_portfolios"],
                "instruments": drivers["instruments"],
            })
    return out


def _drivers(store, client_id, snapshot, theme):
    h = store.holdings_of(client_id, snapshot)
    insts, pfs = [], set()
    for _, r in h.iterrows():
        if theme in instrument_tags(r):
            insts.append({"instrument_id": r.instrument_id,
                          "instrument_name": r.instrument_name,
                          "portfolio_id": r.portfolio_id,
                          "mv_usd": round(r.market_value_usd)})
            pfs.add(r.portfolio_id)
    return {"instruments": sorted(insts, key=lambda x: -x["mv_usd"]),
            "n_portfolios": len(pfs)}
