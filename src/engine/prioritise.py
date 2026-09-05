"""
prioritise.py — "who does Priscilla call first, and can she defend the ranking?"

The urgency score is a transparent weighted sum of the insights each client has.
Crucially, it is NOT a black box: every client's score comes with the exact
contributions that built it, so the ranking is explainable by construction — the
one place in this build where attribution-style XAI genuinely fits.
"""
from __future__ import annotations
from src.ingest import DataStore
from src.build import build_client_insights

# points per insight, by type and severity — tune openly, it's the whole rubric
SEVERITY_POINTS = {"critical": 40, "high": 25, "medium": 12, "low": 4, "info": 1}
TYPE_WEIGHT = {"collateral": 1.3, "liquidity": 1.2, "mandate": 1.0,
               "concentration": 1.0, "attribution": 0.8}


def score_client(store: DataStore, cid: str, narrate_llm=False) -> dict:
    insights = build_client_insights(store, cid, narrate_llm=narrate_llm)
    contributions, score = [], 0.0
    for ins in insights:
        pts = SEVERITY_POINTS.get(ins.severity, 0) * TYPE_WEIGHT.get(ins.type, 1.0)
        score += pts
        contributions.append({
            "type": ins.type, "severity": ins.severity,
            "points": round(pts, 1), "headline": ins.headline,
            "insight_id": ins.id,
        })
    contributions.sort(key=lambda c: -c["points"])
    return {
        "client_id": cid, "client_name": store.client_name(cid),
        "score": round(score, 1), "n_insights": len(insights),
        "top_reason": contributions[0]["headline"] if contributions else "No flags",
        "contributions": contributions,
    }


def build_triage(store: DataStore, narrate_llm=False) -> list[dict]:
    ranked = [score_client(store, cid, narrate_llm) for cid in store.client_ids()]
    ranked.sort(key=lambda r: -r["score"])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked
