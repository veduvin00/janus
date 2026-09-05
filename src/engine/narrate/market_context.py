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

from src.ingest import DataStore, TODAY

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
    "summarize reporting relevant to the query below.\n\n"
    "STRICT RULES:\n"
    "1. Use only information returned by web_search this turn. Cite the "
    "publication and headline for every claim.\n"
    "2. Never treat social media, forums, or unverified chatter as a source, "
    "even if a search result references one.\n"
    "3. This is background color only — NOT investment advice, and NOT a "
    "restatement of the bank's own computed portfolio numbers. You were not "
    "given any client data; do not invent or imply any.\n"
    "4. 'Relevant' means anything from roughly the past 12 months, not just "
    "the last few days — don't discard a good match merely for not being "
    "brand-new. Only say search found nothing if that's true across the "
    "whole past year.\n\n"
    "Answer in 2-4 sentences, plain prose, then list your sources."
)


def build_query(store: DataStore, client_id: str, insight) -> str:
    """Build a real-world-themed search query from a computed Insight.

    This dataset is synthetic: instrument names ("Golden Harbour Properties"),
    event descriptions, even the Fed chair, are fictional for the demo. Searching
    real news for that literal text, or for the bank's internal phrasing (e.g.
    "PF-0004 Cash and Equivalents is 0.0% — below its 1% mandate limit"), will
    always come back empty — neither exists in the real world. What DOES exist
    in the real world is the sector/region/asset-class each synthetic holding
    stands in for, so that's what we search on instead.

    Attribution/concentration insights carry evidence.holding_refs (specific
    instruments); mandate/liquidity/collateral insights don't (build.py only
    sets rule_refs for those), so we fall back to the client's whole book —
    still a real sector/region exposure, just not the one specific insight.
    """
    themes, seen = [], set()

    def _theme(sector, region):
        sector = sector or None
        region = region or None
        key = (sector, region)
        if key == (None, None) or key in seen:
            return
        seen.add(key)
        themes.append(f"{sector or 'diversified assets'} in {region or 'global markets'}")

    for iid in (insight.evidence.holding_refs or []):
        row = store.instruments[store.instruments.instrument_id == iid]
        if len(row):
            r = row.iloc[0]
            _theme(r.get("sector"), r.get("region"))

    if not themes:
        book = store.holdings_of(client_id, snapshot=TODAY)
        if len(book):
            top = (book.groupby(["sector", "region"], dropna=False)["market_value_usd"]
                       .sum().sort_values(ascending=False).head(3))
            for sector, region in top.index:
                _theme(sector, region)

    if not themes:
        return insight.headline  # nothing real-world to anchor on — last resort

    client = store.client(client_id)
    residence = client.get("country_of_residence")
    residence_bit = f" Client is based in {residence}." if residence else ""

    # Keep this plain and direct — a query padded with meta-commentary about
    # "synthetic holdings" measurably derails the search (tested: the same
    # themes phrased as a direct query returned five well-cited Bloomberg
    # results; wrapped in disclaimers, it returned nothing).
    return (
        f"Current market, economic, and geopolitical news relevant to: "
        f"{'; '.join(themes[:4])}.{residence_bit}"
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
