"""
narrate/ — turn a finished Insight into RM-facing language.

The governance point of the whole system lives here: the model is fed ONLY the
Insight packet and is forbidden from adding any fact not already in it. It cannot
free-associate about geopolitics, because it never sees the raw data — only the
numbers and events the deterministic engine already computed and grounded.

  template.py   the always-works deterministic fallback (no deps, no network)
  chat.py       the scoped, tool-use "Ask Why" loop for one open client

Runs with or without an API key:
  - if OPENAI_API_KEY is set, calls the model with the guardrail prompt below
  - otherwise, falls back to template.render() so the demo always runs

Swap the model call for any provider; the contract (Insight in, prose out) holds.
"""
from __future__ import annotations
import json
from src.schema import Insight
from src.engine.narrate.template import render

SYSTEM_PROMPT = (
    "You are a drafting assistant for a private-bank Relationship Manager. "
    "You will receive a single INSIGHT object containing pre-computed numbers, "
    "grounded events, a confidence level and caveats. Write 2-4 sentences an RM "
    "could say to a client. STRICT RULES: (1) Use ONLY facts present in the "
    "object. (2) Never invent numbers, events, causes or instruments. (3) If "
    "confidence is not 'high', reflect the caveat honestly. (4) No investment "
    "advice beyond the suggested_action provided. Output plain prose only."
)

MODEL = "gpt-4o"


def narrate(ins: Insight, use_llm: bool | None = None) -> str:
    import os
    if use_llm is None:
        use_llm = bool(os.environ.get("OPENAI_API_KEY"))
    if not use_llm:
        return render(ins)
    try:
        import openai
        try:
            import truststore
            truststore.inject_into_ssl()
        except Exception:
            pass  # no corporate proxy / truststore unavailable — plain SSL is fine

        client = openai.OpenAI()
        packet = json.dumps(ins.to_dict(), default=str, indent=2)
        resp = client.chat.completions.create(
            model=MODEL,
            max_tokens=400,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"INSIGHT:\n{packet}\n\nWrite the RM-facing note."},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or render(ins)
    except Exception as e:  # never let narration break the pipeline
        return render(ins) + f"  [narrator fallback: {type(e).__name__}]"
