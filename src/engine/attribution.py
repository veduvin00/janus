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
from src.engine.ground import link_events

_DESC_COLS = ["instrument_name", "asset_class", "sector", "region", "underlying_reference"]

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


# ---------------------------------------------------------------------------------
# Exact three-way reconciliation: trading + price + fx == delta, for every holding.
#
#   trading_effect = (q1 - q0) * p0_local * fx0      quantity change, priced at t0
#   price_effect   = q1 * (p1_local - p0_local) * fx0  price move on the closing qty
#   fx_effect      = q1 * p1_local * (fx1 - fx0)       currency move on the closing qty
#
# A new position (no t0 row) or a full exit (no t1 row) has no meaningful price/fx
# split, so its entire delta is trading_effect.
# ---------------------------------------------------------------------------------
def implied_fx(mv_usd: float, mv_local: float) -> float:
    return (mv_usd / mv_local) if mv_local else 1.0


def three_way_decomposition(store: DataStore, client_id: str,
                            start: str, end: str) -> pd.DataFrame:
    """Per-holding trading/price/fx decomposition between two snapshots.

    Keyed by (portfolio_id, instrument_id) so a client with several portfolios
    holding the same instrument doesn't collide.
    """
    h = store.holdings_of(client_id)
    a = h[h.snapshot_date == start].set_index(["portfolio_id", "instrument_id"])
    b = h[h.snapshot_date == end].set_index(["portfolio_id", "instrument_id"])
    keys = sorted(set(a.index) | set(b.index))

    rows = []
    for pid, iid in keys:
        r0 = a.loc[(pid, iid)] if (pid, iid) in a.index else None
        r1 = b.loc[(pid, iid)] if (pid, iid) in b.index else None
        ref = r1 if r1 is not None else r0

        mv0_usd = float(r0.market_value_usd) if r0 is not None else 0.0
        mv1_usd = float(r1.market_value_usd) if r1 is not None else 0.0
        delta_usd = mv1_usd - mv0_usd

        if r0 is None or r1 is None:
            # new position or full exit: nothing to split, it's all trading.
            trading, price, fx = delta_usd, 0.0, 0.0
        else:
            q0, q1 = float(r0.quantity), float(r1.quantity)
            p0, p1 = float(r0.price_local), float(r1.price_local)
            fx0 = implied_fx(mv0_usd, float(r0.market_value_local))
            fx1 = implied_fx(mv1_usd, float(r1.market_value_local))
            trading = (q1 - q0) * p0 * fx0
            price = q1 * (p1 - p0) * fx0
            fx = q1 * p1 * (fx1 - fx0)

        rows.append({
            "portfolio_id": pid,
            "instrument_id": iid,
            "instrument_name": ref.instrument_name,
            "asset_class": ref.get("asset_class", ""),
            "sector": ref.get("sector", ""),
            "region": ref.get("region", ""),
            "underlying_reference": ref.get("underlying_reference", ""),
            "trading_effect": trading,
            "price_effect": price,
            "fx_effect": fx,
            "mv0_usd": mv0_usd,
            "mv1_usd": mv1_usd,
            "delta_usd": delta_usd,
        })
    return pd.DataFrame(rows)


def reconcile(store: DataStore, client_id: str, start: str, end: str,
             tol: float = 1.0) -> dict:
    """Exact reconciliation + event grounding for one client between two snapshots.

    Asserts trading + price + fx == delta_usd per holding and per portfolio total
    (within `tol`, to absorb float rounding only — never a fudge term).

    explained_pct is the share of total |price_effect| whose holding links to at
    least one event in (start, end]; holdings with zero linked events are returned
    as UNEXPLAINED, never given a guessed cause.
    """
    df = three_way_decomposition(store, client_id, start, end)

    recon_diff = df.trading_effect + df.price_effect + df.fx_effect - df.delta_usd
    bad = df[recon_diff.abs() > tol]
    assert bad.empty, (
        f"reconciliation failed for {client_id} holdings: "
        f"{bad[['portfolio_id', 'instrument_id']].to_dict('records')}"
    )

    port_totals = df.groupby("portfolio_id")[
        ["trading_effect", "price_effect", "fx_effect", "delta_usd"]
    ].sum()
    port_diff = (port_totals.trading_effect + port_totals.price_effect
                 + port_totals.fx_effect - port_totals.delta_usd)
    bad_ports = port_totals[port_diff.abs() > tol]
    assert bad_ports.empty, (
        f"reconciliation failed for {client_id} portfolios: {list(bad_ports.index)}"
    )

    totals = df[["trading_effect", "price_effect", "fx_effect", "delta_usd"]].sum()
    total_diff = totals.trading_effect + totals.price_effect + totals.fx_effect - totals.delta_usd
    assert abs(total_diff) <= tol, (
        f"reconciliation failed for {client_id} total: diff={total_diff}"
    )

    total_abs_price = df.price_effect.abs().sum()
    explained_abs = 0.0
    unexplained = []
    events_by_instrument = {}
    for _, r in df.iterrows():
        events = link_events(r[_DESC_COLS], store.events, start, end)
        events_by_instrument[r.instrument_id] = events
        if events:
            explained_abs += abs(r.price_effect)
        else:
            unexplained.append({
                "portfolio_id": r.portfolio_id,
                "instrument_id": r.instrument_id,
                "instrument_name": r.instrument_name,
                "price_effect": r.price_effect,
                "delta_usd": r.delta_usd,
            })
    explained_pct = round(100 * explained_abs / total_abs_price, 1) if total_abs_price else None

    return {
        "client_id": client_id,
        "start": start,
        "end": end,
        "holdings": df.to_dict("records"),
        "portfolio_totals": port_totals.reset_index().to_dict("records"),
        "totals": totals.to_dict(),
        "explained_pct": explained_pct,
        "unexplained": unexplained,
        "events_by_instrument": events_by_instrument,
    }
