"""
attribution.py — explain a portfolio's change: PRICE effect vs FLOW effect.

The single most important correctness point in the whole build:
a portfolio's value changes because (a) markets moved the things it holds, and
(b) money went in or out (trades, withdrawals, income). Blaming the market for a
client's own withdrawals is the classic wrong answer. We separate them.

  price_effect (per holding, opening quantity):
      qty_start * (price_end_local - price_start_local)   -> converted to USD
      using the holding's own implied FX at the start snapshot.

  flow_effect (portfolio):
      net external cashflow from transactions.csv in the window
      (Buy/Withdrawal/Coupon/Dividend/Fee/Drawdown ...).

We report price effects per instrument (the explainable part), grounded to events
downstream, and the flow total alongside so the two are never conflated.
"""
from __future__ import annotations
import pandas as pd
from src.ingest import DataStore

# transaction types that represent external cash in/out (a flow, not a market move)
_FLOW_TYPES = {
    "Buy", "Withdrawal", "Coupon", "Dividend", "Interest", "Distribution",
    "Management Fee", "Interest Charge", "Capital Call", "Facility Drawdown",
    "Structured Product Subscription", "Transfer In", "Redemption Request",
}


def _implied_fx(row: pd.Series) -> float:
    mv_local = row.get("market_value_local", 0) or 0
    mv_usd = row.get("market_value_usd", 0) or 0
    return (mv_usd / mv_local) if mv_local else 1.0


def holding_price_effects(store: DataStore, client_id: str,
                          start: str, end: str) -> pd.DataFrame:
    """Per-instrument price effect (USD) on the opening quantity, start -> end."""
    h = store.holdings_of(client_id)
    a = h[h.snapshot_date == start].copy()
    b = h[h.snapshot_date == end].set_index("instrument_id")

    rows = []
    for _, r in a.iterrows():
        iid = r.instrument_id
        if iid not in b.index:
            continue  # position exited; treated as a flow, not a price effect
        end_row = b.loc[iid]
        if isinstance(end_row, pd.DataFrame):
            end_row = end_row.iloc[0]
        p0, p1 = r.price_local, end_row.price_local
        price_effect_local = r.quantity * (p1 - p0)
        price_effect_usd = price_effect_local * _implied_fx(r)
        rows.append({
            "instrument_id": iid,
            "instrument_name": r.instrument_name,
            "asset_class": r.asset_class,
            "sector": r.sector,
            "region": r.region,
            "underlying_reference": r.get("underlying_reference", ""),
            "price_start": p0,
            "price_end": p1,
            "move_pct": round((p1 / p0 - 1) * 100, 1) if p0 else 0.0,
            "mv_start_usd": r.market_value_usd,
            "mv_end_usd": float(end_row.market_value_usd),
            "price_effect_usd": round(price_effect_usd),
        })
    df = pd.DataFrame(rows)
    return df.sort_values("price_effect_usd") if len(df) else df


def net_flows(store: DataStore, client_id: str, start: str, end: str) -> float:
    """Net external cashflow (USD-ish, uses transaction amount) in (start, end]."""
    tx = store.transactions
    tx = tx[(tx.client_id == client_id) &
            (tx.trade_date > pd.to_datetime(start)) &
            (tx.trade_date <= pd.to_datetime(end)) &
            (tx.transaction_type.isin(_FLOW_TYPES))]
    return float(tx.amount.sum())


def withdrawals(store: DataStore, client_id: str, start: str, end: str) -> pd.DataFrame:
    tx = store.transactions
    tx = tx[(tx.client_id == client_id) &
            (tx.trade_date > pd.to_datetime(start)) &
            (tx.trade_date <= pd.to_datetime(end)) &
            (tx.transaction_type == "Withdrawal")]
    return tx[["trade_date", "amount", "narrative"]]
