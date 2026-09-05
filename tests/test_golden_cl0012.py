"""
Golden regression test — CL-0012 / PF-0014, full window (2025-12-31 -> 2026-08-26).

Cheung Kwok Wing's Retirement Income Mandate made zero position trades in the
window (transactions.csv has only Coupon/Dividend/Interest/Withdrawal/Fee rows for
this client — verified directly, not taken from docs/). So every dollar of the
portfolio's move must be explained by price and FX effects alone, and the
reconciliation must be exact.

Figures below were computed directly from data/holdings.csv (start vs end
snapshot) and cross-checked against src.engine.attribution.reconcile() — they are
not copied from ARCHITECTURE.md or docs/, per CLAUDE.md's instruction to verify
independently.
"""
from __future__ import annotations
import math
from src.ingest import get_store, SNAPSHOTS, TODAY
from src.engine.attribution import reconcile

CLIENT = "CL-0012"
PORTFOLIO = "PF-0014"
START = SNAPSHOTS[0]
END = TODAY


def _get(result, instrument_id):
    match = [h for h in result["holdings"] if h["instrument_id"] == instrument_id]
    assert match, f"{instrument_id} missing from reconciliation"
    return match[0]


def test_reconciliation_is_exact_per_holding_and_total():
    result = reconcile(get_store(), CLIENT, START, END)  # raises AssertionError if not exact
    total = result["totals"]
    for h in result["holdings"]:
        recon = h["trading_effect"] + h["price_effect"] + h["fx_effect"]
        assert math.isclose(recon, h["delta_usd"], abs_tol=1.0), h
    recon_total = total["trading_effect"] + total["price_effect"] + total["fx_effect"]
    assert math.isclose(recon_total, total["delta_usd"], abs_tol=1.0)
    # verified against data/holdings.csv: sum(mv_usd @ 2026-08-26) - sum(mv_usd @ 2025-12-31)
    assert math.isclose(total["delta_usd"], -2_102_157.07, abs_tol=1.0)


def test_no_position_trades_so_trading_effect_is_zero():
    # verified against data/transactions.csv: no Buy/Sell rows for CL-0012 in the window
    result = reconcile(get_store(), CLIENT, START, END)
    for h in result["holdings"]:
        assert math.isclose(h["trading_effect"], 0.0, abs_tol=1.0), h
    assert math.isclose(result["totals"]["trading_effect"], 0.0, abs_tol=1.0)


def test_treasury_2045_is_largest_detractor_and_grounded_to_duration():
    result = reconcile(get_store(), CLIENT, START, END)
    treasury = _get(result, "SYN-FI-0201")
    # verified: q=100,000 unchanged, price_local 68.4 -> 58.2, fx0=1.0 (USD-denominated)
    assert math.isclose(treasury["price_effect"], -1_020_000.0, abs_tol=1.0)
    worst = min(result["holdings"], key=lambda h: h["price_effect"])
    assert worst["instrument_id"] == "SYN-FI-0201"
    events = result["events_by_instrument"]["SYN-FI-0201"]
    assert len(events) > 0
    assert all("duration" in e["matched_tags"] for e in events)
    ids = {h["instrument_id"] for h in result["unexplained"]}
    assert "SYN-FI-0201" not in ids


def test_golden_harbour_perpetual_is_unexplained():
    result = reconcile(get_store(), CLIENT, START, END)
    golden_harbour = _get(result, "SYN-FI-0207")
    # verified: q=25,000 unchanged, price_local 82.4 -> 56.8, fx0=1.0
    assert math.isclose(golden_harbour["price_effect"], -640_000.0, abs_tol=1.0)
    assert result["events_by_instrument"]["SYN-FI-0207"] == []
    ids = {h["instrument_id"] for h in result["unexplained"]}
    assert "SYN-FI-0207" in ids


def test_explained_pct_is_a_reported_number():
    result = reconcile(get_store(), CLIENT, START, END)
    assert result["explained_pct"] is not None
    assert isinstance(result["explained_pct"], (int, float))
    assert 0.0 <= result["explained_pct"] <= 100.0
