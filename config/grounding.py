"""
grounding.py — the auditable mapping that links portfolio moves to real events.

A DATA table, not logic buried in a function, so a compliance reviewer can read
exactly why an event was linked to a holding. Change the mapping here.

Matching is WORD-BOUNDARY (see ground._tags_from), not naive substring — otherwise
"concentrated" would match "rate" and "European fixed income" would match a US
Treasury. Precision here is the whole selling point.

  EVENT_TAGS      : phrase -> tag, applied to event_log.primary_transmission
  INSTRUMENT_TAGS : phrase -> tag, applied to a holding's descriptive fields

An event links to a holding when their tag sets intersect AND the event falls
inside the snapshot window being explained. Nothing is inferred by the LLM.
"""

# --- event transmission channel  ->  canonical tag ---------------------------------
EVENT_TAGS = {
    "gold": "gold", "precious metal": "gold", "inflation": "gold",
    "energy": "energy", "oil": "energy", "brent": "energy", "lng": "energy",
    "shipping": "shipping", "transport": "shipping",
    "gulf": "gulf",
    "em credit": "em_credit",
    "duration": "duration", "long-duration": "duration", "rate-sensitive": "duration",
    "european fixed income": "eur_fixed_income", "eur assets": "eur_fixed_income",
    "technology": "technology", "growth equity": "technology",
    "concentrated equity": "technology",
    "collateralised lending": "collateral",
    "private credit": "private_credit", "semi-liquid": "private_credit",
    "safe haven": "safe_haven", "defence": "defence", "airlines": "airlines",
}

# --- instrument attributes  ->  canonical tag --------------------------------------
INSTRUMENT_TAGS = {
    "gold": "gold", "xau": "gold",
    "energy": "energy", "oil": "energy",
    "shipping": "shipping", "marine": "shipping", "tanker": "shipping",
    "technology": "technology", "cloud": "technology",
    "semiconductor": "technology", "software": "technology",
    # duration / rate-sensitive fixed income — NOT "perpetual": a perpetual coupon
    # structure means indefinite life, not automatic rate-sensitivity. A subordinated
    # perpetual's price can move on issuer/sector credit stress with zero curve
    # linkage (see Golden Harbour Properties vs Pacific Rim Bank in CL-0012's book —
    # same "perpetual" wording, unrelated drivers). Duration risk is asserted only
    # for instruments that are actually government/IG debt, or explicitly long-dated
    # (see LONG_DURATION_YEAR below).
    "treasury": "duration", "sovereign": "duration",
    "investment grade": "duration", "corporate bond": "duration",
    # genuine EM / Asia credit only — not every bond fund
    "emerging market": "em_credit", "asia investment grade": "em_credit",
    "gulf": "gulf", "private credit": "private_credit",
}

# Long-dated bonds carry duration/rate risk even if the name doesn't say so.
LONG_DURATION_YEAR = 2035
