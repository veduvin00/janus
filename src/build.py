"""
build.py — orchestration. Turn raw engine output into narrated Insight objects.

This is the only place that knows about all the engines. Each `_xxx_insights`
function converts one engine's findings into Insight objects (the spine). The API
and CLI both call build_client_insights().

Nothing here computes finance — it assembles. Keep the maths in engine/.
"""
from __future__ import annotations
from src.ingest import DataStore, SNAPSHOTS, TODAY
from src.schema import Insight, Contribution, Evidence
from src.engine import attribution, mandate, concentration, collateral, liquidity
from src.engine.narrate import narrate

START = SNAPSHOTS[0]


# ---------- attribution ----------------------------------------------------------
def _attribution_insights(store: DataStore, cid: str) -> list[Insight]:
    result = attribution.reconcile(store, cid, START, TODAY)
    if not result["holdings"]:
        return []
    totals = result["totals"]
    total_delta = round(totals["delta_usd"])

    worst = sorted(result["holdings"], key=lambda h: h["price_effect"])[:3]
    top = worst[0]
    events = result["events_by_instrument"].get(top["instrument_id"], [])
    event_refs = [f"{e['date']}: {e['description'][:70]}" for e in events[:3]]

    contribs = [
        Contribution("Trading effect (YTD)", round(totals["trading_effect"]), "USD",
                     f"Value change from quantity changes {START}→{TODAY} — should be ~0 with no trades"),
        Contribution("Price effect (YTD)", round(totals["price_effect"]), "USD",
                     f"Market price moves on closing quantities {START}→{TODAY}"),
        Contribution("FX effect (YTD)", round(totals["fx_effect"]), "USD",
                     f"Currency moves on closing quantities {START}→{TODAY}"),
    ]
    for h in worst:
        contribs.append(Contribution(h["instrument_name"], round(h["price_effect"]), "USD",
                                     "price effect on this holding"))

    caveats = []
    for u in result["unexplained"]:
        if abs(u["price_effect"]) < 1:
            continue
        caveats.append(f"UNEXPLAINED: {u['instrument_name']} moved {round(u['price_effect']):,} "
                       f"USD on price with no matching event in the log")

    sev = "high" if total_delta < -1_000_000 else "medium" if total_delta < -250_000 else "low"
    note = _note_matching(store, cid, ["bond", "loss", "sell", "income", "yield"])
    ins = Insight(
        client_id=cid, type="attribution", severity=sev,
        headline=f"Portfolio moved {total_delta:,} USD — driven by {top['instrument_name']}",
        contributions=contribs,
        evidence=Evidence(event_refs=event_refs,
                          holding_refs=[h["instrument_id"] for h in worst],
                          snapshot_pair=[START, TODAY], rm_note=note),
        confidence="high",
        caveats=caveats,
        explained_pct=result["explained_pct"],
        suggested_action="Open the meeting on which cashflows fund the next few years "
                          "without forced sales, not on realising losses.",
    )
    return [ins]


# ---------- mandate --------------------------------------------------------------
def _mandate_insights(store: DataStore, cid: str) -> list[Insight]:
    out = []
    for b in mandate.check_client(store, cid):
        if b["kind"] == "allocation_band":
            head = (f"{b['portfolio_id']} {b['asset_class']} is {b['actual_pct']}% — "
                    f"{b['direction']} its {b['limit_pct']}% mandate limit")
            contribs = [Contribution(f"{b['asset_class']} weight", b["actual_pct"], "%",
                                     f"Mandate band breached ({b['direction']})")]
        else:
            head = (f"{b['instrument']} is {b['actual_pct']}% of {b['portfolio_id']} — "
                    f"over the {b['limit_pct']}% single-position limit")
            contribs = [Contribution(b["instrument"], b["actual_pct"], "%",
                                     f"Single-position limit {b['limit_pct']}%")]
        directed = b.get("client_directed")
        out.append(Insight(
            client_id=cid, type="mandate",
            severity="medium" if directed else "high",
            headline=head, contributions=contribs,
            evidence=Evidence(rule_refs=[b["rule_id"]],
                              rm_note=_note_matching(store, cid, ["waiver", "instructed",
                                                                  "mandate", "ceiling"])),
            confidence="high",
            caveats=(["Client-directed breach (waiver on file) — governance, not drift"]
                     if directed else []),
            suggested_action=("Confirm the waiver still reflects the client's intent."
                              if directed else
                              "Flag for rebalancing back inside the agreed band."),
        ))
    return out


# ---------- concentration --------------------------------------------------------
def _concentration_insights(store: DataStore, cid: str) -> list[Insight]:
    out = []
    for c in concentration.flag_concentrations(store, cid):
        multi = c["n_portfolios"] > 1
        head = (f"{c['pct_of_book']}% of the book is exposed to '{c['theme']}'"
                + (f" across {c['n_portfolios']} portfolios" if multi else ""))
        contribs = [Contribution(f"'{c['theme']}' exposure", c["pct_of_book"], "%",
                                 f"{c['mv_usd']:,} USD of {c['total_book_usd']:,} USD book")]
        for inst in c["instruments"][:4]:
            contribs.append(Contribution(inst["instrument_name"], inst["mv_usd"], "USD",
                                         f"in {inst['portfolio_id']}"))
        out.append(Insight(
            client_id=cid, type="concentration",
            severity="high" if c["pct_of_book"] >= 30 else "medium",
            headline=head, contributions=contribs,
            evidence=Evidence(holding_refs=[i["instrument_id"] for i in c["instruments"]],
                              rm_note=_note_matching(store, cid,
                                                     ["concentrat", "same bet", "single name",
                                                      "uncorrelated", "hedge"])),
            confidence="medium" if any("SP" in i["instrument_id"] for i in c["instruments"]) else "high",
            caveats=(["Includes look-through on structured products to their underlying "
                      "theme — verify the reference basket"]
                     if any("SP" in i["instrument_id"] for i in c["instruments"]) else []),
            suggested_action="Show the aggregated exposure; test whether it still matches "
                             "the client's stated intent for this money.",
        ))
    return out


# ---------- collateral -----------------------------------------------------------
def _collateral_insights(store: DataStore, cid: str) -> list[Insight]:
    out = []
    for f in collateral.check_client(store, cid):
        if f["severity"] in ("low",):
            continue
        head = (f"{f['facility_id']} LTV is {f['now_ltv_pct']}% vs a "
                f"{f['margin_call_ltv_pct']}% margin-call trigger "
                f"({f['buffer_pts']} pts of headroom)")
        contribs = [Contribution("Current LTV", f["now_ltv_pct"], "%",
                                 f"Margin call at {f['margin_call_ltv_pct']}%"),
                    Contribution("Headroom to trigger", f["buffer_pts"], "pts",
                                 "Distance before a margin call")]
        for pt in f["trajectory"]:
            if pt["ltv_pct"] is not None:
                contribs.append(Contribution(f"LTV {pt['date']}", pt["ltv_pct"], "%",
                                             f"drawn {int(pt['drawn']):,}" if pt["drawn"] else ""))
        out.append(Insight(
            client_id=cid, type="collateral", severity=f["severity"],
            headline=head, contributions=contribs,
            evidence=Evidence(snapshot_pair=[SNAPSHOTS[0], TODAY],
                              rm_note=_note_matching(store, cid,
                                                     ["collateral", "lombard", "drew", "utilisation",
                                                      "margin"])),
            confidence="high",
            suggested_action="Walk the LTV trajectory with the client; agree a plan "
                             "before collateral volatility forces the bank's hand.",
        ))
    return out


# ---------- liquidity ------------------------------------------------------------
def _liquidity_insights(store: DataStore, cid: str) -> list[Insight]:
    liq = liquidity.check_client(store, cid)
    if liq["total_demand_usd"] <= 0:
        return []
    gap = liq["gap_usd"]
    sev = "high" if gap < 0 else "medium" if liq["gated_positions"] and \
        liq["total_demand_usd"] > 0.5 * liq["ready_liquidity_usd"] else "low"
    if sev == "low":
        return []
    head = (f"Near-term demands of {liq['total_demand_usd']:,} USD against "
            f"{liq['ready_liquidity_usd']:,} USD of readily sellable assets")
    contribs = [
        Contribution("Ready liquidity (Daily/Weekly)", liq["ready_liquidity_usd"], "USD"),
        Contribution("Uncalled commitments", liq["uncalled_commitments_usd"], "USD"),
        Contribution("Planned cash needs", liq["planned_cash_needs_usd"], "USD"),
        Contribution("Liquidity gap", gap, "USD", "Ready liquidity minus total demand"),
    ]
    caveats = []
    if liq["gated_positions"]:
        caveats.append("Some assets are gated/illiquid and cannot be relied on: "
                       + ", ".join(g["instrument_name"] for g in liq["gated_positions"][:3]))
    return [Insight(
        client_id=cid, type="liquidity", severity=sev, headline=head,
        contributions=contribs,
        evidence=Evidence(rm_note=_note_matching(store, cid,
                          ["liquidity", "gate", "redemption", "capital call", "tuition"])),
        confidence="high", caveats=caveats,
        suggested_action="Produce a liquidity map: which obligations are matched by "
                         "sellable assets, and where the timing is tight.",
    )]


# ---------- helpers --------------------------------------------------------------
def _note_matching(store: DataStore, cid: str, keywords: list[str]) -> str | None:
    for n in sorted(store.notes_of(cid), key=lambda x: x["note_date"], reverse=True):
        low = n["note"].lower()
        if any(k in low for k in keywords):
            return n["note"]
    return None


def build_client_insights(store: DataStore, cid: str, narrate_llm: bool | None = None) -> list[Insight]:
    insights: list[Insight] = []
    for fn in (_attribution_insights, _mandate_insights, _concentration_insights,
               _collateral_insights, _liquidity_insights):
        try:
            insights.extend(fn(store, cid))
        except Exception as e:
            print(f"[warn] {fn.__name__} failed for {cid}: {e}")
    insights.sort(key=lambda i: i.severity_rank, reverse=True)
    for ins in insights:
        ins.narrative = narrate(ins, use_llm=narrate_llm)
    return insights
