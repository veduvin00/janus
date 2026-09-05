"""
main.py — FastAPI layer. Serves the deterministic Insight objects as JSON.

    uvicorn api.main:app --reload

Endpoints:
    GET /clients               -> the book (id, name, headline stats)
    GET /triage                -> prioritised call list with explainable scores
    GET /clients/{id}          -> client detail
    GET /clients/{id}/insights -> narrated Insight objects (the spine)

The frontend is a static page that consumes these. Keeping the engine behind a
JSON API means the UI is swappable (React, mobile, whatever) without touching the
finance code — which is the point of the Insight contract.
"""
from __future__ import annotations
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.ingest import get_store
from src.build import build_client_insights
from src.engine.prioritise import build_triage
from src.engine.narrate.chat import ask_why
from src.engine.narrate.market_context import get_market_context, build_query

app = FastAPI(title="JB Wealth Intelligence — RM Workbench API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

USE_LLM = bool(os.environ.get("OPENAI_API_KEY"))
_cache: dict = {}


@app.get("/clients")
def clients():
    s = get_store()
    out = []
    for cid in s.client_ids():
        c = s.client(cid)
        out.append({"client_id": cid, "client_name": c.get("client_name"),
                    "age": c.get("age"), "risk_profile": c.get("risk_profile"),
                    "aum_usd": c.get("total_aum_usd"),
                    "base_currency": c.get("base_currency"),
                    "life_stage": c.get("life_stage")})
    return out


@app.get("/triage")
def triage():
    if "triage" not in _cache:
        _cache["triage"] = build_triage(get_store(), narrate_llm=False)
    return _cache["triage"]


@app.get("/clients/{cid}")
def client_detail(cid: str):
    s = get_store()
    c = s.client(cid)
    if not c:
        raise HTTPException(404, "client not found")
    c["notes"] = s.notes_of(cid)
    return c


@app.get("/clients/{cid}/insights")
def client_insights(cid: str):
    s = get_store()
    if not s.client(cid):
        raise HTTPException(404, "client not found")
    key = f"ins:{cid}"
    if key not in _cache:
        _cache[key] = [i.to_dict() for i in
                       build_client_insights(s, cid, narrate_llm=USE_LLM)]
    return _cache[key]


class ChatRequest(BaseModel):
    question: str


@app.post("/clients/{cid}/chat")
def chat(cid: str, req: ChatRequest):
    s = get_store()
    if not s.client(cid):
        raise HTTPException(404, "client not found")
    answer = ask_why(s, cid, req.question)
    if answer is None:
        return {"available": False, "answer": None,
                "message": "Ask Why is unavailable right now (no OPENAI_API_KEY "
                           "configured, or the model call failed) — the rest of the "
                           "workbench is unaffected."}
    return {"available": True, "answer": answer}


class MarketContextRequest(BaseModel):
    query: str | None = None
    insight_id: str | None = None


@app.post("/clients/{cid}/market-context")
def market_context(cid: str, req: MarketContextRequest):
    """Deliberately NOT grounded in this client's data — see
    src/engine/narrate/market_context.py. Live web search restricted to
    reputable financial press, quarantined from the Insight pipeline: never
    feeds contributions, explained_pct, or suggested_action.

    Pass insight_id (preferred) to have the query built server-side from that
    insight's real-world sector/region themes rather than this demo's
    synthetic instrument names or internal phrasing, neither of which exist
    in real news. `query` is a manual override for free-text follow-ups."""
    s = get_store()
    if not s.client(cid):
        raise HTTPException(404, "client not found")

    query = req.query
    if req.insight_id:
        match = next((i for i in build_client_insights(s, cid, narrate_llm=False)
                      if i.id == req.insight_id), None)
        if match:
            query = build_query(s, cid, match)
    if not query:
        raise HTTPException(400, "query or insight_id is required")

    result = get_market_context(query)
    if result is None:
        return {"available": False, "summary": None, "sources": [],
                "message": "External market context is unavailable right now "
                           "(no OPENAI_API_KEY, or the search failed)."}
    return {"available": True, **result}


@app.get("/")
def root():
    return {"ok": True, "llm_narration": USE_LLM,
            "endpoints": ["/clients", "/triage", "/clients/{id}", "/clients/{id}/insights",
                         "/clients/{id}/chat", "/clients/{id}/market-context"]}
