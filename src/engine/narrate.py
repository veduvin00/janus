"""
narrate.py — turn a finished Insight into RM-facing language.

The governance point of the whole system lives here: the model is fed ONLY the
Insight packet and is forbidden from adding any fact not already in it. It cannot
free-associate about geopolitics, because it never sees the raw data — only the
numbers and events the deterministic engine already computed and grounded.

Runs with or without an API key:
  - if ANTHROPIC_API_KEY is set, calls Claude with the guardrail prompt below
  - otherwise, falls back to a deterministic template so the demo always runs

Swap the model call for any provider; the contract (Insight in, prose out) holds.
"""
from __future__ import annotations
import os
import json
from src.schema import Insight

SYSTEM_PROMPT = (
    "You are a drafting assistant for a private-bank Relationship Manager. "
    "You will receive a single INSIGHT object containing pre-computed numbers, "
    "grounded events, a confidence level and caveats. Write 2-4 sentences an RM "
    "could say to a client. STRICT RULES: (1) Use ONLY facts present in the "
    "object. (2) Never invent numbers, events, causes or instruments. (3) If "
    "confidence is not 'high', reflect the caveat honestly. (4) No investment "
    "advice beyond the suggested_action provided. Output plain prose only."
)


def _fallback(ins: Insight) -> str:
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


def narrate(ins: Insight, use_llm: bool | None = None) -> str:
    if use_llm is None:
        use_llm = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not use_llm:
        return _fallback(ins)
    try:
        import anthropic
        client = anthropic.Anthropic()
        packet = json.dumps(ins.to_dict(), default=str, indent=2)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user",
                       "content": f"INSIGHT:\n{packet}\n\nWrite the RM-facing note."}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:  # never let narration break the pipeline
        return _fallback(ins) + f"  [narrator fallback: {type(e).__name__}]"
