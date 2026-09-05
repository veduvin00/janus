"""
template.py — deterministic prose from a finished Insight. Zero deps, always
works. This is what the system falls back to with no API key, no network, or
a failed model call — never a crash, never an empty screen.
"""
from __future__ import annotations
from src.schema import Insight


def render(ins: Insight) -> str:
    """Deterministic narrative from the packet — no model required."""
    bits = [ins.headline.rstrip(".") + "."]
    if ins.contributions:
        c = ins.contributions[0]
        val = f"{c.value:,}" if isinstance(c.value, (int, float)) else c.value
        bits.append(f"{c.label}: {val}{(' ' + c.unit) if c.unit else ''}.")
    if ins.evidence.event_refs:
        bits.append("Grounded in: " + "; ".join(ins.evidence.event_refs[:2]) + ".")
    if ins.confidence != "high" and ins.caveats:
        bits.append("Caveat: " + ins.caveats[0])
    if ins.suggested_action:
        bits.append("Suggested next step: " + ins.suggested_action)
    return " ".join(bits)
