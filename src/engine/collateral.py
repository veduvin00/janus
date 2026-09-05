"""
collateral.py — trace loan-to-value across the five snapshots.

A Lombard line is fine until the collateral falls. Because collateral value and
drawdown both move over time, the risk is a trajectory, not a single number. We
read the pre-computed LTV per snapshot from credit_facilities.csv and measure the
distance to the margin-call trigger, flagging facilities that are close or over.
"""
from __future__ import annotations
from src.ingest import DataStore, SNAPSHOTS, TODAY

WARN_BUFFER_PCT = 5.0   # within this many pts of the margin-call LTV => warn


def check_client(store: DataStore, client_id: str) -> list[dict]:
    fac = store.facilities_of(client_id)
    out = []
    for _, f in fac.iterrows():
        trigger = f.margin_call_ltv_pct
        traj = [{"date": d, "ltv_pct": f.get(f"ltv_pct_{d}"),
                 "drawn": f.get(f"drawn_{d}"),
                 "headroom": f.get(f"headroom_{d}")} for d in SNAPSHOTS]
        now_ltv = f.get(f"ltv_pct_{TODAY}")
        buffer = round(trigger - now_ltv, 1)
        if now_ltv >= trigger:
            sev = "critical"
        elif buffer <= WARN_BUFFER_PCT:
            sev = "high"
        elif buffer <= 2 * WARN_BUFFER_PCT:
            sev = "medium"
        else:
            sev = "low"
        out.append({
            "facility_id": f.facility_id, "facility_type": f.facility_type,
            "collateral_portfolio_id": f.collateral_portfolio_id,
            "margin_call_ltv_pct": trigger, "now_ltv_pct": now_ltv,
            "buffer_pts": buffer, "severity": sev, "trajectory": traj,
            "drawn_now": f.get(f"drawn_{TODAY}"),
        })
    return out
