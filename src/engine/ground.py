"""
ground.py — link a price move to the event(s) that plausibly caused it.

Grounding rule (transparent, defensible, no LLM guessing):
  1. tag the instrument from its attributes  (config.INSTRUMENT_TAGS)
  2. tag each event from its transmission channel (config.EVENT_TAGS)
  3. an event supports a holding's move if tags overlap
     AND the event date falls within the window being explained.

Returns the events plus the exact tags that matched, so the UI can show
"linked because both are tagged: duration, energy".
"""
from __future__ import annotations
import re
import pandas as pd
from config.grounding import EVENT_TAGS, INSTRUMENT_TAGS, LONG_DURATION_YEAR


def _tags_from(text: str, table: dict) -> set[str]:
    """Word-boundary match so 'rate' doesn't fire inside 'concentrated'."""
    t = (text or "").lower()
    out = set()
    for kw, tag in table.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", t):
            out.add(tag)
    return out


def instrument_tags(row: pd.Series) -> set[str]:
    """Tag a holding/instrument row from its descriptive fields."""
    blob = " ".join(str(row.get(c, "")) for c in
                     ["instrument_name", "asset_class", "sub_asset_class",
                      "sector", "region", "underlying_reference"])
    tags = _tags_from(blob, INSTRUMENT_TAGS)
    # long-dated bond => duration/rate risk regardless of naming
    m = re.search(r"(20\d\d)", str(row.get("instrument_name", "")))
    if m and int(m.group(1)) >= LONG_DURATION_YEAR:
        tags.add("duration")
    return tags


def event_tags(row: pd.Series) -> set[str]:
    return _tags_from(str(row.get("primary_transmission", "")), EVENT_TAGS)


def link_events(instrument_row: pd.Series, events: pd.DataFrame,
                start: str, end: str) -> list[dict]:
    """Events within (start, end] whose tags overlap the instrument's tags."""
    itags = instrument_tags(instrument_row)
    if not itags:
        return []
    lo, hi = pd.to_datetime(start), pd.to_datetime(end)
    out = []
    for _, ev in events.iterrows():
        if not (lo < ev.event_date <= hi):
            continue
        matched = itags & event_tags(ev)
        if matched:
            out.append({
                "date": ev.event_date.strftime("%Y-%m-%d"),
                "description": ev.description,
                "severity": ev.severity,
                "matched_tags": sorted(matched),
            })
    return out
