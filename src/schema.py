"""
schema.py — THE SPINE.

Every insight in this system is one `Insight` object. The deterministic engine
PRODUCES these; the narrator and the UI CONSUME them. Nothing reaches the screen
that isn't backed by a field in here.

Why this matters for the challenge:
  - Traceability  -> every claim carries `evidence` (event ids, holding ids, rule ids)
  - Explainability -> every claim carries `contributions` (the numbers behind it)
  - Honesty       -> every insight carries `confidence` + `caveats`
  - Human-in-loop -> `suggested_action` is a proposal the RM accepts/modifies/rejects

If you ever find yourself wanting the LLM to "just explain the CSVs", stop and
put the computation here instead. The LLM only ever narrates a finished Insight.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any
import hashlib


# ordered so severities sort correctly
SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass
class Contribution:
    """One computed number that supports the insight. Renders as a clickable chip."""
    label: str                 # e.g. "Bond sleeve price change"
    value: Any                 # e.g. -2482300  (USD)  or  -15.0 (pct)
    unit: str = ""             # "USD", "%", "x", ""
    detail: str = ""           # how it was derived, in one line

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Evidence:
    """Provenance. What in the data backs this insight."""
    event_refs: list[str] = field(default_factory=list)      # event_log dates/descriptions used
    holding_refs: list[str] = field(default_factory=list)    # instrument_ids implicated
    rule_refs: list[str] = field(default_factory=list)       # mandate rule / limit ids applied
    snapshot_pair: list[str] = field(default_factory=list)   # ["2025-12-31","2026-08-26"]
    rm_note: str | None = None                               # the relevant RM note verbatim

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Insight:
    client_id: str
    type: str                      # attribution | mandate | concentration | collateral | liquidity
    severity: str                  # info|low|medium|high|critical
    headline: str
    contributions: list[Contribution] = field(default_factory=list)
    evidence: Evidence = field(default_factory=Evidence)
    confidence: str = "high"       # high|medium|low
    caveats: list[str] = field(default_factory=list)
    suggested_action: str = ""
    narrative: str = ""            # filled by narrate.py, FROM the fields above only
    id: str = ""
    explained_pct: float | None = None   # attribution only: % of |price_effect| grounded to an event

    def __post_init__(self):
        if not self.id:
            key = f"{self.client_id}:{self.type}:{self.headline}"
            self.id = "INS-" + hashlib.sha1(key.encode()).hexdigest()[:8]

    @property
    def severity_rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity_rank"] = self.severity_rank
        return d
