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

from src.ingest import get_store
from src.build import build_client_insights
from src.engine.prioritise import build_triage

app = FastAPI(title="JB Wealth Intelligence — RM Workbench API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

USE_LLM = bool(os.environ.get("ANTHROPIC_API_KEY"))
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


@app.get("/")
def root():
    return {"ok": True, "llm_narration": USE_LLM,
            "endpoints": ["/clients", "/triage", "/clients/{id}", "/clients/{id}/insights"]}
