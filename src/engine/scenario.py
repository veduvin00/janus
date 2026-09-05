"""
scenario.py — apply a named shock vector to current exposures and emit a
normal Insight per affected client, so "what if this de-escalates / worsens"
is a first-class, inspectable output rather than something only the chatbot
can speculate about.

Reuses the same instrument tags used for grounding (config/grounding.py) to
decide which holdings a shock touches — a scenario estimate and a historical
attribution both describe exposure in the same vocabulary an RM already sees
on every insight card.

A scenario is a stated assumption, not a measured fact: every Insight this
produces carries confidence="low" and a caveat saying so, and the shock sizes
below are the entire assumption set — nothing is hidden in code the RM can't
be shown.
"""
from __future__ import annotations
from src.ingest import DataStore, TODAY
from src.engine.ground import instrument_tags

# Illustrative, fully-disclosed shock magnitudes (% price move per holding),
# keyed by the same tags config/grounding.py uses to link events to holdings.
# "technology" is included because a Gulf de-escalation/escalation is modelled
# as a broad risk-on/risk-off move, not just an energy-desk story — growth
# equities move with the same regime, just smaller.
SHOCKS = {
    "hormuz_reopens": {
        "label": "Strait of Hormuz de-escalation",
        "narrative": "shipping resumes normal transit, energy prices ease, and "
                     "safe-haven / duration demand unwinds",
        "tag_shocks": {
            "energy": -12.0, "shipping": -8.0, "gulf": -6.0,
            "duration": +4.0, "safe_haven": -5.0, "technology": +3.0,
        },
    },
    "hormuz_worsens": {
        "label": "Strait of Hormuz escalation",
        "narrative": "shipping is disrupted, energy prices spike, and capital "
                     "rotates into safe havens and away from risk assets",
        "tag_shocks": {
            "energy": +15.0, "shipping": +10.0, "gulf": +8.0,
            "duration": -5.0, "safe_haven": +6.0, "technology": -4.0,
            "airlines": -7.0,
        },
    },
}


def _holding_shock(row, tag_shocks: dict) -> tuple[float, set[str]]:
    """Pick the single largest-magnitude shock among a holding's matched tags
    rather than summing them — a holding tagged both 'energy' and 'gulf' is
    one bet on the same event, not two independent shocks stacked together."""
    tags = instrument_tags(row) & tag_shocks.keys()
    if not tags:
        return 0.0, set()
    best = max(tags, key=lambda t: abs(tag_shocks[t]))
    return tag_shocks[best], tags


def apply_shock(store: DataStore, client_id: str, scenario_id: str,
                snapshot: str = TODAY) -> dict | None:
    """Estimate this client's book-level impact of one named shock. Returns
    None if nothing in the client's current book is tagged for this shock."""
    shock = SHOCKS[scenario_id]
    h = store.holdings_of(client_id, snapshot)
    total = h.market_value_usd.sum()
    if total <= 0:
        return None

    affected = []
    est_total = 0.0
    for _, r in h.iterrows():
        pct, tags = _holding_shock(r, shock["tag_shocks"])
        if pct == 0.0:
            continue
        impact = r.market_value_usd * pct / 100.0
        est_total += impact
        affected.append({
            "instrument_id": r.instrument_id, "instrument_name": r.instrument_name,
            "portfolio_id": r.portfolio_id, "mv_usd": round(r.market_value_usd),
            "tags": sorted(tags), "shock_pct": pct, "est_impact_usd": round(impact),
        })
    if not affected:
        return None

    affected.sort(key=lambda a: -abs(a["est_impact_usd"]))
    return {
        "scenario_id": scenario_id, "label": shock["label"],
        "narrative": shock["narrative"], "snapshot": snapshot,
        "total_book_usd": round(total), "est_impact_usd": round(est_total),
        "est_impact_pct_of_book": round(100 * est_total / total, 1),
        "affected": affected,
    }


def scenario_insight(store: DataStore, client_id: str, scenario_id: str,
                     snapshot: str = TODAY):
    """One Insight for one client under one named shock, or None if nothing
    in their current book is tagged for it. Local import of schema avoids a
    build.py <-> scenario.py import cycle."""
    from src.schema import Insight, Contribution, Evidence

    r = apply_shock(store, client_id, scenario_id, snapshot)
    if r is None:
        return None

    direction = "gain" if r["est_impact_usd"] >= 0 else "loss"
    head = (f"{r['label']}: an estimated {abs(r['est_impact_usd']):,} USD "
            f"{direction} ({r['est_impact_pct_of_book']}% of book)")

    contribs = [Contribution("Estimated total impact", r["est_impact_usd"], "USD",
                             r["narrative"])]
    shown = r["affected"][:4]
    for a in shown:
        contribs.append(Contribution(a["instrument_name"], a["est_impact_usd"], "USD",
                                     f"{a['shock_pct']:+.1f}% shock via tag(s) "
                                     f"{', '.join(a['tags'])}"))
    rest = len(r["affected"]) - len(shown)

    caveats = ["Scenario estimate, not a forecast: shock sizes are stated, "
               "disclosed assumptions applied to today's holdings — not a "
               "fitted market model."]
    if rest > 0:
        caveats.append(f"+{rest} more holding(s) affected, not itemised above")

    return Insight(
        client_id=client_id, type="scenario",
        severity="high" if abs(r["est_impact_pct_of_book"]) >= 10 else "medium",
        headline=head, contributions=contribs,
        evidence=Evidence(holding_refs=[a["instrument_id"] for a in r["affected"]],
                          snapshot_pair=[snapshot, snapshot]),
        confidence="low",
        caveats=caveats,
        suggested_action="Walk through which holdings drive this estimate and "
                         "whether the client wants any hedge in place before "
                         "it plays out.",
    )


def scenario_insights_for_client(store: DataStore, client_id: str,
                                 snapshot: str = TODAY) -> list:
    """One Insight per shock (see SHOCKS) that actually touches this client's
    book — used by the on-demand chat tool, not the daily book."""
    out = []
    for scenario_id in SHOCKS:
        ins = scenario_insight(store, client_id, scenario_id, snapshot)
        if ins is not None:
            out.append(ins)
    return out
