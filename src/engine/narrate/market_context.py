"""
market_context.py — OPTIONAL, deliberately QUARANTINED external color via live
web search. This module is NOT part of the grounded Insight pipeline: nothing
it returns ever touches Insight.contributions, explained_pct, caveats,
suggested_action, or narrative (src/build.py and src/schema.py never import
this module). It exists only to answer "what is current, reputable financial
press saying about X" as adjacent color for an RM — always labeled unverified,
never treated as a computed fact or a cause.

Guardrail-relevant design choices:
  - Restricted to a fixed allowlist of reputable financial-press domains via
    OpenAI's hosted web_search tool (Responses API, `filters.allowed_domains`).
    Social media, forums, and blogs (e.g. r/wallstreetbets) are structurally
    excluded — not filtered after the fact, excluded from what the model can
    even search. The allowlist is a small, auditable data table, the same
    spirit as config/grounding.py's taxonomy.
  - Every claim must carry a citation from that turn's search — same
    "no fact without a source this turn" discipline as narrate/chat.py.
  - Degrades exactly like the rest of the system: no key, no network, or any
    error -> returns None, never raises. The API layer turns that into a
    clean "unavailable", never a crash or a fabricated summary.
"""
from __future__ import annotations
import os

MODEL = "gpt-4o"

# Reputable financial press only. Explicitly NOT social media, forums, or
# blogs — a private bank's client-facing content cannot rest on anonymous,
# meme-driven speculation (e.g. r/wallstreetbets). See CLAUDE.md guardrail #1.
ALLOWED_NEWS_DOMAINS = [
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com",
    "apnews.com", "marketwatch.com", "economist.com",
]

SYSTEM_PROMPT = (
    "You are a market-context lookup for a private-bank Relationship Manager. "
    "Use the web_search tool (restricted to reputable financial press) to "
    "summarize CURRENT reporting relevant to the query below.\n\n"
    "STRICT RULES:\n"
    "1. Use only information returned by web_search this turn. Cite the "
    "publication and headline for every claim.\n"
    "2. Never treat social media, forums, or unverified chatter as a source, "
    "even if a search result references one.\n"
    "3. This is background color only — NOT investment advice, and NOT a "
    "restatement of the bank's own computed portfolio numbers. You were not "
    "given any client data; do not invent or imply any.\n"
    "4. If search finds nothing relevant, say so plainly rather than padding "
    "the answer.\n\n"
    "Answer in 2-4 sentences, plain prose, then list your sources."
)


def get_market_context(query: str) -> dict | None:
    """Best-effort, source-cited external context for `query`.

    Returns {"summary": str, "sources": [{"url","title"}]} or None on no key,
    no network, or any error — callers must treat None as unavailable, never
    fall back to fabricating a summary.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        import openai
        try:
            import truststore
            truststore.inject_into_ssl()
        except Exception:
            pass  # no corporate proxy / truststore unavailable — plain SSL is fine

        client = openai.OpenAI()
        resp = client.responses.create(
            model=MODEL,
            instructions=SYSTEM_PROMPT,
            input=query,
            tools=[{
                "type": "web_search",
                "filters": {"allowed_domains": ALLOWED_NEWS_DOMAINS},
            }],
        )
        text = (resp.output_text or "").strip()
        if not text:
            return None

        sources, seen = [], set()
        for item in resp.output:
            if getattr(item, "type", "") != "message":
                continue
            for part in getattr(item, "content", []) or []:
                for ann in getattr(part, "annotations", []) or []:
                    if getattr(ann, "type", "") != "url_citation":
                        continue
                    url = getattr(ann, "url", None)
                    if url and url not in seen:
                        seen.add(url)
                        sources.append({"url": url, "title": getattr(ann, "title", None) or url})

        return {"summary": text, "sources": sources}
    except Exception:
        return None
