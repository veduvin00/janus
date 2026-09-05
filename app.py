"""
app.py — RM Intelligence Workbench (NiceGUI).
One-command launch for the entire application:

    python app.py

Full feature parity with the workbench:
  - Urgency-ranked triage queue (Left Rail)
  - Client profile header with safe missing-data handling (Family offices, etc.)
  - Insight cards with clickable provenance chips (trading, price, FX, event log)
  - Caveats and collapsible RM notes
  - Interactive Accept / Modify / Reject actions persisted to data/decisions.json
  - Live RM Decision Log & Meeting Prep panel (Right Rail)
  - Scoped "Ask Why" chatbot (from computed facts only)
  - Cleaned & formatted "Market Context Enquiry" (quarantined reputable financial press)
"""
from __future__ import annotations
import asyncio
import datetime
import json
import math
import os
import re
import socket
from typing import Any
from nicegui import ui, app

from src.ingest import get_store, DataStore
from src.build import build_client_insights
from src.engine.prioritise import build_triage
from src.schema import Insight
from src.engine.narrate.chat import ask_why
from src.engine.narrate.market_context import get_market_context, build_query

USE_LLM = bool(os.environ.get("OPENAI_API_KEY"))
DECISIONS_FILE = os.path.join(os.path.dirname(__file__), "data", "decisions.json")

# In-memory caches for fast, instant client navigation
_cache_triage: list[dict] | None = None
_cache_insights: dict[str, list[Insight]] = {}


# ---------- Decisions Persistence (JSON) -----------------------------------------
def load_decisions() -> list[dict]:
    if not os.path.exists(DECISIONS_FILE):
        return []
    try:
        with open(DECISIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_decisions_list(decisions: list[dict]):
    os.makedirs(os.path.dirname(DECISIONS_FILE), exist_ok=True)
    with open(DECISIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(decisions, f, indent=2, ensure_ascii=False)


def record_decision(cid: str, client_name: str, ins: Insight, action: str, notes: str | None = None) -> dict:
    decisions = load_decisions()
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = {
        "decision_id": f"DEC-{ins.id[4:] if ins.id.startswith('INS-') else ins.id[:8]}",
        "timestamp": now_iso,
        "client_id": cid,
        "client_name": client_name,
        "insight_id": ins.id,
        "insight_type": ins.type,
        "severity": ins.severity,
        "headline": ins.headline,
        "action": action.upper(),  # ACCEPT | MODIFY | REJECT
        "suggested_action": ins.suggested_action,
        "rm_notes": notes,
    }

    # Replace existing decision for this client + insight if present, else prepend
    idx = next((i for i, d in enumerate(decisions) if d.get("client_id") == cid and d.get("insight_id") == ins.id), None)
    if idx is not None:
        decisions[idx] = entry
    else:
        decisions.insert(0, entry)

    save_decisions_list(decisions)
    return entry


def delete_decision_entry(decision_id: str):
    decisions = load_decisions()
    decisions = [d for d in decisions if d.get("decision_id") != decision_id]
    save_decisions_list(decisions)


def get_decision_for(cid: str, insight_id: str) -> dict | None:
    decisions = load_decisions()
    return next((d for d in decisions if d.get("client_id") == cid and d.get("insight_id") == insight_id), None)


# ---------- Helpers & Formatting ------------------------------------------------
def get_cached_triage(store: DataStore) -> list[dict]:
    global _cache_triage
    if _cache_triage is None:
        _cache_triage = build_triage(store, narrate_llm=False)
    return _cache_triage


def get_cached_insights(store: DataStore, cid: str) -> list[Insight]:
    if cid not in _cache_insights:
        # Fast, deterministic template narration (18ms) prevents blocking the WebSocket event loop
        _cache_insights[cid] = build_client_insights(store, cid, narrate_llm=False)
    return _cache_insights[cid]


def safe_age(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        if isinstance(val, float) and math.isnan(val):
            return ""
        return f"{round(val)}y"
    return str(val)


def safe_aum(val: Any) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "0"
    if isinstance(val, (int, float)):
        return f"{val:,.0f}"
    return str(val)


def format_val(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        if isinstance(val := v, float) and math.isnan(val):
            return "—"
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        if isinstance(v, float) and not v.is_integer():
            return f"{v:.1f}"
    return str(v)


def clean_market_summary(summary: str) -> str:
    """Cleans up raw markdown links and redundant source blocks from LLM web search output."""
    if not summary:
        return "—"

    text = summary.strip()

    # 1. Strip off trailing redundant Sources / References block
    for marker in ["Sources:", "**Sources:**", "Sources\n", "References:"]:
        if marker in text:
            text = text.split(marker)[0].strip()

    # 2. Transform markdown citations [domain.com](url) into clean clickable source chips
    def replace_link(match):
        label = match.group(1).replace("https://", "").replace("http://", "").replace("www.", "").strip()
        domain = label.split("/")[0]
        url = match.group(2)
        return (
            f'<a href="{url}" target="_blank" rel="noopener" '
            f'class="inline-block bg-[#f0ead9] text-[#8a6d3b] hover:underline font-medium text-[11px] px-1.5 py-0.5 rounded border border-[#e5e3dd] ml-1 mr-0.5 align-middle">'
            f'{domain} ↗</a>'
        )

    text = re.sub(r'\(?\[([^\]]+)\]\((https?://[^\)]+)\)\)?', replace_link, text)

    # 3. Format into clean paragraphs
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    html_parts = []
    for p in paragraphs:
        html_parts.append(f'<p class="mb-2 leading-relaxed text-[13px] text-[#232a32]">{p}</p>')

    return "".join(html_parts)


def build_smart_market_query(store: DataStore, client_id: str, insight: Insight, user_query: str | None = None) -> str:
    """Translates synthetic portfolio context and user queries into real-world macroeconomic search themes."""
    if user_query and not user_query.strip().startswith("Tell me more about:"):
        cleaned_user = re.sub(r"\b(PF|CF|CL|INS)-\d+\b", "", user_query, flags=re.IGNORECASE).strip()
        if len(cleaned_user) > 5:
            return f"Current financial and economic news on: {cleaned_user}"

    headline_lower = insight.headline.lower()
    themes = []

    if "equity" in headline_lower or "equities" in headline_lower or "stock" in headline_lower:
        themes.append("Global equity markets, stock valuations, and asset allocation trends")
    if "bond" in headline_lower or "fixed income" in headline_lower or "yield" in headline_lower or "treasury" in headline_lower:
        themes.append("Global bond markets, central bank interest rates, and yield curve movements")
    if "cash" in headline_lower or "money market" in headline_lower:
        themes.append("Cash preservation, money market yields, and short-term liquidity")
    if "private equity" in headline_lower or "venture" in headline_lower:
        themes.append("Private equity dealmaking, valuations, and secondary markets")
    if "private credit" in headline_lower or "direct lending" in headline_lower:
        themes.append("Private credit lending market, corporate borrowing, and default risks")
    if "ltv" in headline_lower or "margin" in headline_lower or "collateral" in headline_lower:
        themes.append("Wealth management margin loans, leverage risk, and collateral volatility")
    if "shipping" in headline_lower or "hormuz" in headline_lower or "oil" in headline_lower or "energy" in headline_lower:
        themes.append("Middle East geopolitical risk, Strait of Hormuz oil shipping, and energy commodities")
    if "real estate" in headline_lower or "property" in headline_lower:
        themes.append("Commercial and residential real estate market outlook and interest rate impact")
    if "tech" in headline_lower or "technology" in headline_lower or "ai" in headline_lower:
        themes.append("Technology sector earnings, artificial intelligence infrastructure, and market sentiment")

    if not themes:
        for iid in (insight.evidence.holding_refs or []):
            row = store.instruments[store.instruments.instrument_id == iid]
            if len(row):
                r = row.iloc[0]
                sec = r.get("sector")
                reg = r.get("region")
                ast = r.get("asset_class")
                if sec and sec != "Diversified":
                    themes.append(f"{sec} sector developments in {reg or 'global markets'}")
                elif ast:
                    themes.append(f"{ast} market trends in {reg or 'global markets'}")

    if not themes:
        themes.append("Global asset allocation, macroeconomic trends, and monetary policy")

    client = store.client(client_id)
    residence = client.get("country_of_residence")
    residence_bit = f" with focus on investors in {residence}" if residence else ""
    return f"Latest reputable financial news on: {'; '.join(themes[:2])}{residence_bit}."


CUSTOM_CSS = """
<style>
  :root {
    --ink: #12161c;
    --slate: #5b6672;
    --line: #e5e3dd;
    --paper: #f6f4ef;
    --card: #ffffff;
    --accent: #8a6d3b;
    --accent-soft: #f0ead9;
    --crit: #8b2f2f;
    --high: #b4632a;
    --med: #8a6d3b;
    --low: #5b6672;
    --serif: "Iowan Old Style", Georgia, "Times New Roman", serif;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  body {
    margin: 0;
    font-family: var(--sans);
    color: var(--ink);
    background-color: var(--paper) !important;
  }
  .serif-font {
    font-family: var(--serif);
  }
  .callrow {
    border: 1px solid var(--line);
    background: var(--card);
    border-radius: 10px;
    padding: 11px 12px;
    margin-bottom: 8px;
    cursor: pointer;
    transition: all 0.12s ease-in-out;
  }
  .callrow:hover {
    border-color: var(--accent);
    transform: translateY(-1px);
  }
  .callrow.active {
    border-color: var(--accent);
    background: #ffffff;
    box-shadow: 0 0 0 2px var(--accent-soft);
  }
  .rank-badge {
    display: inline-block;
    width: 20px;
    height: 20px;
    line-height: 20px;
    text-align: center;
    border-radius: 50%;
    background: var(--ink);
    color: #ffffff;
    font-size: 11px;
    margin-right: 6px;
    font-weight: 600;
  }
  .card-box {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 18px 20px;
    margin-bottom: 18px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
  }
  .pill {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .6px;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 20px;
    color: #ffffff;
  }
  .pill-critical { background: var(--crit); }
  .pill-high { background: var(--high); }
  .pill-medium { background: var(--med); }
  .pill-low { background: var(--low); }
  .pill-info { background: var(--low); }

  .chip {
    font-size: 12px;
    border: 1px solid var(--line);
    background: var(--paper);
    border-radius: 16px;
    padding: 4px 11px;
    cursor: pointer;
    transition: 0.1s;
    white-space: nowrap;
    display: inline-block;
    user-select: none;
    margin: 3px 4px 3px 0;
    color: var(--ink);
  }
  .chip:hover {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .chip.event { border-style: dashed; }
  .chip.neg { color: var(--crit); font-weight: 600; }

  .prov {
    background: #faf8f3;
    border: 1px solid var(--line);
    border-left: 3px solid var(--accent);
    border-radius: 6px;
    padding: 8px 12px;
    margin: 6px 0 10px 0;
    font-size: 12.5px;
    line-height: 1.5;
    display: none;
  }
  .prov.open { display: block; }
  .prov .k { color: var(--slate); font-weight: 600; }

  .caveat {
    font-size: 12.5px;
    color: var(--high);
    background: #fbf3ea;
    border-radius: 6px;
    padding: 7px 11px;
    margin-top: 10px;
  }
  .rmnote {
    margin-top: 10px;
    font-size: 12.5px;
  }
  .rmnote summary {
    cursor: pointer;
    color: var(--accent);
    font-weight: 600;
  }
  .rmnote p {
    color: #333333;
    background: var(--paper);
    border-radius: 6px;
    padding: 9px 11px;
    margin: 6px 0 0;
    line-height: 1.5;
    font-style: italic;
  }

  /* Explicit Private-Banking Button System */
  .btn-action {
    font-family: var(--sans) !important;
    font-size: 12px !important;
    font-weight: 500 !important;
    border-radius: 7px !important;
    padding: 6px 14px !important;
    cursor: pointer !important;
    transition: all 0.12s ease-in-out !important;
    line-height: 1.4 !important;
    text-transform: none !important;
  }
  .btn-action .q-btn__content {
    color: inherit !important;
    font-size: 12px !important;
    font-weight: 500 !important;
    text-transform: none !important;
  }
  .btn-action.btn-dark {
    background-color: #12161c !important;
    color: #ffffff !important;
    border: 1px solid #12161c !important;
  }
  .btn-action.btn-dark:hover {
    background-color: #232a32 !important;
  }
  .btn-action.btn-outline {
    background-color: #ffffff !important;
    color: #12161c !important;
    border: 1px solid #e5e3dd !important;
  }
  .btn-action.btn-outline:hover {
    border-color: #8a6d3b !important;
    background-color: #faf8f3 !important;
  }
  .btn-action.btn-accepted {
    background-color: #2e7d32 !important;
    color: #ffffff !important;
    border: 1px solid #2e7d32 !important;
  }
  .btn-action.btn-modified {
    background-color: #8a6d3b !important;
    color: #ffffff !important;
    border: 1px solid #8a6d3b !important;
  }
  .btn-action.btn-rejected {
    background-color: #5b6672 !important;
    color: #ffffff !important;
    border: 1px solid #5b6672 !important;
  }
  .btn-action.btn-ghost {
    background-color: #ffffff !important;
    color: #5b6672 !important;
    border: 1px solid #e5e3dd !important;
  }
  .btn-action.btn-ghost:hover {
    color: #12161c !important;
    border-color: #8a6d3b !important;
    background-color: #faf8f3 !important;
  }

  /* Market Context Enquiry Box Styling */
  .askbox {
    margin-top: 12px;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 12.5px;
    background: #f4f6f8;
    border: 1px solid var(--line);
  }
  .enqsection {
    margin-top: 9px;
    padding: 10px 12px;
    border-radius: 6px;
    background: #ffffff;
    border: 1px solid var(--line);
  }
  .enqsection.ext {
    background: #fff8ea;
    border-color: #e8d9ad;
  }
  .enqlabel {
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: .6px;
    text-transform: uppercase;
    color: var(--slate);
    margin-bottom: 6px;
  }
  .mktlabel {
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: .6px;
    text-transform: uppercase;
    color: #8a6d1f;
    margin-bottom: 6px;
  }
  .enqsection .src {
    display: block;
    font-size: 11.5px;
    margin-top: 4px;
  }
  .enqsection .src a {
    color: var(--accent);
    text-decoration: underline;
  }

  /* Decision Log Card Styling */
  .decision-card {
    background: #ffffff;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 8px;
    font-size: 12px;
    transition: all 0.1s;
  }
  .decision-card:hover {
    border-color: var(--accent);
  }
</style>
<script>
  function tog(id) {
    var el = document.getElementById(id);
    if (el) el.classList.toggle('open');
  }
</script>
"""


@ui.page("/")
def main_page():
    ui.add_head_html(CUSTOM_CSS)

    store = get_store()
    triage_rows = get_cached_triage(store)
    active_cid = {"cid": triage_rows[0]["client_id"] if triage_rows else "CL-0012"}
    row_elements: dict[str, ui.element] = {}
    active_filter = {"filter": "ALL"}  # ALL | ACCEPT | MODIFY | REJECT

    # Root 3-column layout: Left Rail (300px) | Center Panel (flex) | Right Rail (340px)
    with ui.row().classes("w-full h-screen no-wrap m-0 p-0 items-stretch overflow-hidden"):
        # =========================================================================
        # Column 1: Left Rail (Triage Call List)
        # =========================================================================
        with ui.column().classes("w-[300px] h-screen overflow-y-auto p-[20px_16px] bg-[#fbfaf7] border-r border-[#e5e3dd] flex-shrink-0"):
            ui.html('<h1 class="serif-font text-[18px] font-semibold m-0 leading-tight">Wealth Intelligence</h1>')
            ui.html('<div class="text-[#5b6672] text-[12px] mb-[16px]">Priscilla Ong · Asia desk · today 26 Aug 2026</div>')
            ui.html('<div class="text-[11px] uppercase tracking-[.8px] text-[#5b6672] mb-[10px] font-medium">Who to call first</div>')

            triage_box = ui.column().classes("w-full gap-0")

            def select_client(cid: str):
                active_cid["cid"] = cid
                for c_id, el in row_elements.items():
                    if c_id == cid:
                        el.classes(add="active", remove="")
                    else:
                        el.classes(add="", remove="active")
                render_client_view(cid)

            with triage_box:
                for r in triage_rows:
                    cid = r["client_id"]
                    is_active = (cid == active_cid["cid"])
                    row_el = ui.column().classes(f"callrow w-full {'active' if is_active else ''}")
                    row_elements[cid] = row_el

                    with row_el:
                        ui.html(f"""
                        <div class="flex justify-between items-baseline gap-2 w-full">
                          <div class="font-semibold text-[13.5px]">
                            <span class="rank-badge">{r['rank']}</span>{r['client_name']}
                          </div>
                          <div class="serif-font text-[15.5px] text-[#8a6d3b] font-medium">{r['score']}</div>
                        </div>
                        <div class="text-[#5b6672] text-[11.5px] mt-1 leading-snug">{r['top_reason']}</div>
                        """)
                    row_el.on("click", lambda _, c=cid: select_client(c))

        # =========================================================================
        # Column 2: Center Panel (Client Insights View)
        # =========================================================================
        main_container = ui.column().classes("flex-grow h-screen overflow-y-auto p-[26px_34px] max-w-[860px] bg-[#f6f4ef]")

        # =========================================================================
        # Column 3: Right Rail (RM Decision Log & Meeting Prep)
        # =========================================================================
        right_container = ui.column().classes("w-[340px] h-screen overflow-y-auto p-[20px_16px] bg-[#ffffff] border-l border-[#e5e3dd] flex-shrink-0")

        def refresh_right_panel():
            right_container.clear()
            decisions = load_decisions()
            accepted_count = sum(1 for d in decisions if d.get("action") == "ACCEPT")
            modified_count = sum(1 for d in decisions if d.get("action") == "MODIFY")
            rejected_count = sum(1 for d in decisions if d.get("action") == "REJECT")

            with right_container:
                # Header
                ui.html("""
                <div class="border-b border-[#e5e3dd] pb-3 mb-3 w-full">
                  <div class="flex justify-between items-baseline">
                    <h2 class="serif-font text-[17px] font-semibold m-0">RM Decision Log</h2>
                    <span class="text-[10.5px] text-[#2e7d32] font-semibold bg-[#e8f5e9] px-2 py-0.5 rounded-full">● Saved to JSON</span>
                  </div>
                  <div class="text-[11.5px] text-[#5b6672] mt-0.5">Audit trail & Meeting Prep talking points</div>
                  <div class="text-[11px] text-[#5b6672] mt-1">
                    <b>""" + str(len(decisions)) + """</b> total · <span class="text-[#2e7d32]">""" + str(accepted_count) + """ accepted</span> · <span class="text-[#8a6d3b]">""" + str(modified_count) + """ modified</span>
                  </div>
                </div>
                """)

                # Filter buttons
                with ui.row().classes("w-full gap-1 mb-3"):
                    for flt, label in [("ALL", "All"), ("ACCEPT", "Accepted"), ("MODIFY", "Modified"), ("REJECT", "Rejected")]:
                        is_sel = (active_filter["filter"] == flt)
                        f_btn = ui.button(label).props("no-caps unelevated dense").classes(
                            f"text-[11px] px-2 py-0.5 rounded {'btn-dark' if is_sel else 'btn-outline'}"
                        )
                        def set_f(target=flt):
                            active_filter["filter"] = target
                            refresh_right_panel()
                        f_btn.on("click", set_f)

                # Filtered List
                curr_flt = active_filter["filter"]
                filtered = decisions if curr_flt == "ALL" else [d for d in decisions if d.get("action") == curr_flt]

                if not filtered:
                    ui.html("""
                    <div class="text-center text-[#5b6672] text-[12px] italic mt-12 px-4 leading-relaxed">
                      No decisions recorded under this view.<br>
                      Click <b>Accept</b>, <b>Modify</b>, or <b>Reject</b> on any client insight card to log decisions.
                    </div>
                    """)
                    return

                # Decision Cards
                for d in filtered:
                    d_id = d.get("decision_id", "")
                    act = d.get("action", "")
                    pill_color = "#2e7d32" if act == "ACCEPT" else "#8a6d3b" if act == "MODIFY" else "#5b6672"

                    with ui.column().classes("decision-card w-full gap-1"):
                        with ui.row().classes("w-full justify-between items-center no-wrap"):
                            ui.html(f"""
                            <div class="flex items-center gap-1.5">
                              <span style="background-color: {pill_color}; color: #ffffff; font-size: 9.5px; font-weight: 700; padding: 2px 7px; border-radius: 12px;">{act}</span>
                              <span class="text-[10px] text-[#5b6672]">{d.get('timestamp', '')[11:16]}</span>
                            </div>
                            """)
                            # Undo/Delete decision button
                            del_btn = ui.button("✕").props("no-caps flat dense").classes("text-[#5b6672] hover:text-[#8b2f2f] text-[11px] p-0 min-w-[16px]")
                            def undo_action(dec_id=d_id):
                                delete_decision_entry(dec_id)
                                ui.notify("Decision removed", type="info", color="#12161c")
                                refresh_right_panel()
                                render_client_view(active_cid["cid"])
                            del_btn.on("click", undo_action)

                        ui.html(f"""
                        <div class="font-semibold text-[12px] text-[#12161c] mt-0.5">{d.get('client_name')} <span class="text-[#5b6672] font-normal">({d.get('client_id')})</span></div>
                        <div class="text-[11.5px] text-[#232a32] leading-snug">{d.get('headline')}</div>
                        """)

                        if act == "ACCEPT":
                            ui.html(f"""
                            <div class="bg-[#f4f7f4] border-l-2 border-[#2e7d32] p-1.5 rounded-r text-[11px] text-[#1b5e20] mt-1">
                              <b>Meeting talking point:</b> {d.get('suggested_action')}
                            </div>
                            """)
                        elif act == "MODIFY":
                            ui.html(f"""
                            <div class="bg-[#faf6ed] border-l-2 border-[#8a6d3b] p-1.5 rounded-r text-[11px] text-[#6d4c13] mt-1">
                              <b>RM Custom Plan:</b> {d.get('rm_notes') or d.get('suggested_action')}
                            </div>
                            """)
                        elif act == "REJECT":
                            ui.html(f"""
                            <div class="bg-[#f5f5f5] border-l-2 border-[#5b6672] p-1.5 rounded-r text-[11px] text-[#424242] mt-1 italic">
                              <b>Reason:</b> {d.get('rm_notes') or 'Dismissed by RM'}
                            </div>
                            """)

        def render_client_view(cid: str):
            main_container.clear()
            c = store.client(cid)
            if not c:
                with main_container:
                    ui.label("Client not found").classes("text-[#5b6672]")
                return

            insights = get_cached_insights(store, cid)

            with main_container:
                # Client Header with safe missing-data / NaN checks
                aum_str = safe_aum(c.get("total_aum_usd"))
                age_s = safe_age(c.get("age"))
                age_part = f"{age_s} · " if age_s else ""
                risk = c.get("risk_profile") or "Standard"
                curr = c.get("base_currency") or "USD"
                objs = c.get("objectives") or "Capital preservation & growth"
                name = c.get("client_name") or cid

                header_html = f"""
                <div class="border-b border-[#e5e3dd] pb-4 mb-2 w-full">
                  <h2 class="serif-font text-[25px] font-semibold m-0 leading-tight">{name}</h2>
                  <div class="text-[#5b6672] text-[13px] mt-1">
                    {cid} · {age_part}{risk} mandate · {curr} · AUM {aum_str} USD
                  </div>
                  <div class="italic text-[#12161c] bg-[#f0ead9] p-[8px_12px] rounded-lg text-[12.5px] mt-3 leading-normal">
                    Objective — {objs}
                  </div>
                </div>
                """
                ui.html(header_html).classes("w-full")

                # Insights Cards
                if not insights:
                    ui.html('<div class="text-[#5b6672] mt-8">No flags for this client.</div>')
                    return

                for idx, ins in enumerate(insights):
                    ins_id = ins.id
                    existing_dec = get_decision_for(cid, ins_id)
                    current_status = existing_dec.get("action") if existing_dec else None

                    with ui.column().classes("card-box w-full"):
                        # Pill + Type + Confidence
                        sev_class = f"pill-{ins.severity}"
                        card_top_html = f"""
                        <div class="flex items-center gap-[10px] mb-2 w-full">
                          <span class="pill {sev_class}">{ins.severity}</span>
                          <span class="text-[11px] uppercase tracking-[.8px] text-[#5b6672] font-semibold">{ins.type}</span>
                          <span class="ml-auto text-[11px] text-[#5b6672]">confidence <b class="text-[#12161c]">{ins.confidence}</b></span>
                        </div>
                        <div class="serif-font text-[16.5px] font-semibold leading-snug my-1">{ins.headline}</div>
                        <div class="text-[13.5px] leading-relaxed text-[#232a32]">{ins.narrative}</div>
                        """
                        ui.html(card_top_html).classes("w-full")

                        # Chips (Contributions & Events)
                        chips_html = '<div class="my-3 flex flex-wrap gap-1 w-full">'
                        prov_html = ""

                        for c_idx, contrib in enumerate(ins.contributions):
                            pid = f"p_{idx}_{c_idx}"
                            val = contrib.value
                            neg_class = "neg" if isinstance(val, (int, float)) and val < 0 else ""
                            val_str = ("−" + format_val(abs(val))) if isinstance(val, (int, float)) and val < 0 else format_val(val)
                            unit_str = f" {contrib.unit}" if contrib.unit else ""

                            chips_html += f'<span class="chip {neg_class}" onclick="tog(\'{pid}\')">{contrib.label}: {val_str}{unit_str}</span>'
                            detail_txt = contrib.detail or "—"
                            prov_html += f'<div class="prov w-full" id="{pid}"><span class="k">how derived:</span> {detail_txt}</div>'

                        for e_idx, ev in enumerate(ins.evidence.event_refs or []):
                            eid = f"e_{idx}_{e_idx}"
                            short_ev = ev.split(":")[0]
                            chips_html += f'<span class="chip event" onclick="tog(\'{eid}\')">⚑ {short_ev}</span>'
                            prov_html += f'<div class="prov w-full" id="{eid}"><span class="k">event_log:</span> {ev}</div>'

                        chips_html += '</div>'
                        ui.html(chips_html + prov_html).classes("w-full")

                        # Caveats
                        if ins.caveats:
                            caveats_html = "".join(f'<div class="caveat">⚠︎ {cav}</div>' for cav in ins.caveats)
                            ui.html(caveats_html).classes("w-full")

                        # RM Note
                        if ins.evidence.rm_note:
                            note_html = f"""
                            <details class="rmnote w-full">
                              <summary>RM note on file</summary>
                              <p>{ins.evidence.rm_note}</p>
                            </details>
                            """
                            ui.html(note_html).classes("w-full")

                        # Action Area
                        with ui.column().classes("w-full mt-3 pt-3 border-t border-dashed border-[#e5e3dd]"):
                            ui.html(f"""
                            <div class="text-[11px] uppercase tracking-[.7px] text-[#5b6672] font-semibold">Suggested action</div>
                            <div class="text-[13px] my-1 leading-normal">{ins.suggested_action}</div>
                            """)

                            # Interactive Button Row
                            with ui.row().classes("gap-2 mt-2 items-center flex-wrap"):
                                is_acc = (current_status == "ACCEPT")
                                is_mod = (current_status == "MODIFY")
                                is_rej = (current_status == "REJECT")

                                btn_accept = ui.button(
                                    "Accepted ✓" if is_acc else "Accept"
                                ).props("no-caps unelevated").classes(
                                    f"btn-action {'btn-accepted' if is_acc else 'btn-dark'}"
                                )

                                btn_modify = ui.button(
                                    "Modified ✓" if is_mod else "Modify"
                                ).props("no-caps unelevated").classes(
                                    f"btn-action {'btn-modified' if is_mod else 'btn-outline'}"
                                )

                                btn_reject = ui.button(
                                    "Rejected ✕" if is_rej else "Reject"
                                ).props("no-caps unelevated").classes(
                                    f"btn-action {'btn-rejected' if is_rej else 'btn-outline'}"
                                )

                                btn_enquiry = ui.button("🔎 Market Context Enquiry").props("no-caps unelevated").classes(
                                    "btn-action btn-ghost"
                                )

                                def handle_accept(cur_ins=ins, client_n=name):
                                    record_decision(cid, client_n, cur_ins, "ACCEPT")
                                    ui.notify(f"Accepted & logged to decisions.json for {client_n}", type="positive", color="#2e7d32")
                                    refresh_right_panel()
                                    render_client_view(cid)

                                def open_modify_dialog(cur_ins=ins, client_n=name):
                                    with ui.dialog() as mod_dialog, ui.card().classes("w-[500px] p-5"):
                                        ui.html(f'<h3 class="serif-font text-[17px] font-semibold m-0">Modify Action: {client_n}</h3>')
                                        ui.html(f'<div class="text-[11.5px] text-[#5b6672] mt-0.5 mb-2">{cur_ins.headline}</div>')
                                        ui.html('<div class="text-[10.5px] uppercase tracking-[.6px] text-[#5b6672] font-semibold">Original System Suggestion:</div>')
                                        ui.html(f'<div class="text-[12px] bg-[#faf8f3] p-2.5 rounded border border-[#e5e3dd] my-1.5 leading-relaxed">{cur_ins.suggested_action}</div>')

                                        ui.html('<div class="text-[10.5px] uppercase tracking-[.6px] text-[#5b6672] font-semibold mt-2">RM Revised Instructions / Talking Points:</div>')
                                        existing_note = existing_dec.get("rm_notes") if existing_dec else ""
                                        custom_inp = ui.textarea(value=existing_note or cur_ins.suggested_action).classes("w-full mt-1 text-[12.5px]").props("outlined rows=3")

                                        with ui.row().classes("w-full justify-end gap-2 mt-4"):
                                            ui.button("Cancel", on_click=mod_dialog.close).props("no-caps unelevated").classes("btn-action btn-outline")

                                            def save_mod():
                                                note_val = custom_inp.value.strip()
                                                record_decision(cid, client_n, cur_ins, "MODIFY", note_val)
                                                mod_dialog.close()
                                                ui.notify(f"Modified action logged to decisions.json", type="positive", color="#8a6d3b")
                                                refresh_right_panel()
                                                render_client_view(cid)

                                            ui.button("Save Revision", on_click=save_mod).props("no-caps unelevated").classes("btn-action btn-dark")
                                    mod_dialog.open()

                                def handle_reject(cur_ins=ins, client_n=name):
                                    record_decision(cid, client_n, cur_ins, "REJECT", "Dismissed by RM (intentional mandate drift / prior agreement)")
                                    ui.notify(f"Rejected & logged to decisions.json", type="warning", color="#5b6672")
                                    refresh_right_panel()
                                    render_client_view(cid)

                                btn_accept.on("click", handle_accept)
                                btn_modify.on("click", open_modify_dialog)
                                btn_reject.on("click", handle_reject)

                            # Market Context Enquiry Collapsible Drawer
                            enquiry_drawer = ui.column().classes("askbox w-full")
                            enquiry_drawer.visible = False

                            def toggle_enquiry(d=enquiry_drawer):
                                d.visible = not d.visible

                            btn_enquiry.on("click", toggle_enquiry)

                            with enquiry_drawer:
                                with ui.row().classes("w-full gap-2 items-center no-wrap"):
                                    clean_head = ins.headline.replace('"', '&quot;')
                                    inp_q = ui.input(value=f'Tell me more about: "{clean_head}"').classes("flex-grow bg-white border border-[#e5e3dd] rounded-md px-2 py-1 text-[12px]").props("dense outlined")
                                    btn_ask = ui.button("Ask").props("no-caps unelevated").classes("btn-action btn-dark")

                                # Section 1: From computed data (Ask Why)
                                with ui.column().classes("enqsection w-full"):
                                    ui.html('<div class="enqlabel">From computed data</div>')
                                    ans_label = ui.html('<div class="text-[12.5px] leading-relaxed text-[#232a32]">—</div>').classes("w-full")

                                # Section 2: External market color (Web search)
                                with ui.column().classes("enqsection ext w-full"):
                                    ui.html('<div class="mktlabel">⚠ External market color — unverified, not used in the numbers above</div>')
                                    ui.html('<div class="text-[10.5px] text-[#8a6d1f] mb-1 italic">ℹ Synthetic IDs (e.g. PF-0019) are automatically mapped to underlying real-world asset class & sector news.</div>')
                                    mkt_label = ui.html('<div class="text-[12.5px] leading-relaxed text-[#232a32]">—</div>').classes("w-full")

                                async def run_enquiry(q_elem=inp_q, a_elem=ans_label, m_elem=mkt_label, cur_ins=ins):
                                    query_text = q_elem.value.strip()
                                    if not query_text:
                                        return
                                    a_elem.set_content('<span class="text-[#5b6672] italic">Asking computed engine…</span>')
                                    m_elem.set_content('<span class="text-[#5b6672] italic">Searching reputable financial press…</span>')

                                    # Async execution so the UI remains fluid
                                    async def fetch_chat():
                                        try:
                                            ans = await asyncio.to_thread(ask_why, store, cid, query_text)
                                            if ans:
                                                a_elem.set_content(f'<div class="text-[12.5px] leading-relaxed whitespace-pre-wrap">{ans}</div>')
                                            else:
                                                a_elem.set_content(
                                                    '<div class="text-[12px] text-[#5b6672] italic">'
                                                    'Ask Why is unavailable right now (no OPENAI_API_KEY configured, '
                                                    'or the model call failed) — the rest of the workbench is unaffected.</div>'
                                                )
                                        except Exception as e:
                                            a_elem.set_content(f'<div class="text-[12px] text-[#5b6672] italic">Ask Why unavailable: {e}</div>')

                                    async def fetch_market():
                                        try:
                                            search_q = build_smart_market_query(store, cid, cur_ins, query_text)
                                            mkt_res = await asyncio.to_thread(get_market_context, search_q)
                                            if mkt_res and mkt_res.get("summary"):
                                                cleaned_summary = clean_market_summary(mkt_res.get("summary", ""))
                                                src_links = "".join(
                                                    f'<span class="src">↳ <a href="{s["url"]}" target="_blank" rel="noopener">{s["title"]}</a></span>'
                                                    for s in mkt_res.get("sources", [])
                                                )
                                                m_elem.set_content(f'<div>{cleaned_summary}</div><div class="mt-2 pt-2 border-t border-[#e8d9ad]">{src_links}</div>')
                                            else:
                                                m_elem.set_content(
                                                    '<div class="text-[12px] text-[#8a6d1f] italic">'
                                                    'External financial press search found no matching reporting for this specific query. '
                                                    '(Note: Specific portfolio codes like PF-0019 are synthetic for this case study; try searching for broader real-world themes like "Global Equities" or "Corporate Bonds").</div>'
                                                )
                                        except Exception as e:
                                            m_elem.set_content(f'<div class="text-[12px] text-[#8a6d1f] italic">Market search unavailable: {e}</div>')

                                    await asyncio.gather(fetch_chat(), fetch_market())

                                btn_ask.on("click", run_enquiry)

        # Initial render of first client and right panel
        render_client_view(active_cid["cid"])
        refresh_right_panel()


def find_available_port(preferred_ports=(8080, 8000, 8081, 8888)) -> int:
    for port in preferred_ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 8080


if __name__ in {"__main__", "__mp_main__"}:
    port = find_available_port([8080, 8000, 8081])
    print(f"\n🚀 Launching RM Intelligence Workbench on http://localhost:{port} ...")
    ui.run(title="RM Intelligence Workbench", port=port, reload=False, show=True)
