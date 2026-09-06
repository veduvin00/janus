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

from src.ingest import get_store, DataStore, TODAY
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
            f'class="inline-block bg-[#eaf2f8] text-[#1f71ac] hover:underline font-semibold text-[11px] px-1.5 py-0.5 rounded border border-[#d2e3f2] ml-1 mr-0.5 align-middle">'
            f'{domain} ↗</a>'
        )

    text = re.sub(r'\(?\[([^\]]+)\]\((https?://[^\)]+)\)\)?', replace_link, text)

    # 3. Format into clean paragraphs
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    html_parts = []
    for p in paragraphs:
        html_parts.append(f'<p class="mb-2 leading-relaxed text-[13px] text-[#202936]">{p}</p>')

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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --ink: #0c2340;          /* Julius Baer Primary Swiss Navy */
    --text: #202936;         /* Refined Charcoal body text */
    --slate: #5a6d85;        /* Julius Baer Corporate Slate */
    --line: #e2e8f0;         /* Modern crisp border */
    --paper: #f5f7fa;        /* Clean Swiss background */
    --card: #ffffff;         /* Pure white cards */
    --accent: #1f71ac;       /* Julius Baer Royal Accent Blue */
    --accent-soft: #eaf2f8;  /* Light Ice Blue tint */
    --accent-hover: #165684;
    --crit: #b91c1c;         /* Crisp Crimson */
    --high: #d97706;         /* Warm Amber */
    --med: #1f71ac;          /* Julius Baer Blue */
    --low: #5a6d85;          /* Corporate Slate */
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --heading: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  }
  body {
    margin: 0;
    font-family: var(--sans);
    color: var(--text);
    background-color: var(--paper) !important;
    -webkit-font-smoothing: antialiased;
  }
  .serif-font {
    font-family: var(--heading);
    letter-spacing: -0.02em;
  }
  .callrow {
    border: 1px solid var(--line);
    background: var(--card);
    border-radius: 8px;
    padding: 13px 13px;
    margin-bottom: 9px;
    cursor: pointer;
    transition: all 0.12s ease-in-out;
    width: 100%;
    box-sizing: border-box;
  }
  .callrow > nicegui-html,
  .callrow nicegui-html {
    width: 100% !important;
    display: block !important;
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
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: var(--ink);
    color: #ffffff;
    font-size: 11px;
    font-weight: 700;
    flex-shrink: 0;
  }
  .card-box {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 18px;
    box-shadow: 0 1px 3px rgba(12, 35, 64, 0.04);
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

  /* Structured Financial Metrics & Evidence Panels (Informational, NOT Buttons) */
  .metric-card {
    background: #f8fafc;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 10px 14px;
    margin: 8px 0;
  }
  .metric-grid-item {
    background: #ffffff;
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 8px 10px;
    text-align: center;
  }
  .event-badge {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    background: #f1f5f9;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 11.5px;
    color: #334155;
    margin: 3px 0;
  }
  .caveat {
    font-size: 12px;
    color: #92400e;
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 6px;
    padding: 7px 11px;
    margin: 8px 0;
  }
  .rmnote-box {
    background: #fffdf7;
    border: 1px solid #f1e5cd;
    border-left: 3px solid #d97706;
    border-radius: 0 6px 6px 0;
    padding: 9px 12px;
    margin: 8px 0;
    font-size: 12px;
  }
  .action-container {
    background: #f0f7fc;
    border: 1px solid #d2e3f2;
    border-radius: 8px;
    padding: 12px 14px;
    margin-top: 12px;
  }

  /* Explicit Julius Bär Private-Banking Button System */
  .btn-action {
    font-family: var(--sans) !important;
    font-size: 12px !important;
    font-weight: 500 !important;
    border-radius: 6px !important;
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
    background-color: #0c2340 !important;
    color: #ffffff !important;
    border: 1px solid #0c2340 !important;
  }
  .btn-action.btn-dark:hover {
    background-color: #163259 !important;
  }
  .btn-action.btn-outline {
    background-color: #ffffff !important;
    color: #0c2340 !important;
    border: 1px solid #cbd5e1 !important;
  }
  .btn-action.btn-outline:hover {
    border-color: #1f71ac !important;
    background-color: #f0f7fc !important;
  }
  .btn-action.btn-accepted,
  .q-btn.btn-action.btn-accepted,
  .q-btn.bg-primary.btn-accepted {
    background: #15803d !important;
    background-color: #15803d !important;
    color: #ffffff !important;
    border: 1px solid #15803d !important;
  }
  .btn-action.btn-accepted .q-btn__content {
    color: #ffffff !important;
  }
  .btn-action.btn-modified,
  .q-btn.btn-action.btn-modified,
  .q-btn.bg-primary.btn-modified {
    background: #1f71ac !important;
    background-color: #1f71ac !important;
    color: #ffffff !important;
    border: 1px solid #1f71ac !important;
  }
  .btn-action.btn-modified .q-btn__content {
    color: #ffffff !important;
  }
  .btn-action.btn-rejected,
  .q-btn.btn-action.btn-rejected,
  .q-btn.bg-primary.btn-rejected {
    background: #b91c1c !important;
    background-color: #b91c1c !important;
    color: #ffffff !important;
    border: 1px solid #b91c1c !important;
  }
  .btn-action.btn-rejected .q-btn__content {
    color: #ffffff !important;
  }
  .btn-action.btn-ghost {
    background-color: #ffffff !important;
    color: #5a6d85 !important;
    border: 1px solid #cbd5e1 !important;
  }
  .btn-action.btn-ghost:hover {
    color: #0c2340 !important;
    border-color: #1f71ac !important;
    background-color: #f0f7fc !important;
  }

  /* Market Context Enquiry Box Styling */
  .askbox {
    margin-top: 12px;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 12.5px;
    background: #f8fafc;
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
    background: #fffdf5;
    border-color: #fde68a;
  }
  .enqlabel {
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: .6px;
    text-transform: uppercase;
    color: var(--ink);
    margin-bottom: 6px;
  }
  .mktlabel {
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: .6px;
    text-transform: uppercase;
    color: #b45309;
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

  /* Analytics Dashboard Styling */
  .ana-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 16px;
    box-shadow: 0 1px 3px rgba(12, 35, 64, 0.04);
  }
  .ana-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  .ana-table th {
    background-color: #f8fafc;
    color: #5a6d85;
    font-size: 10.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 7px 10px;
    text-align: left;
    border-bottom: 1px solid #e2e8f0;
  }
  .ana-table td {
    padding: 9px 10px;
    border-bottom: 1px solid #f1f5f9;
    color: #0c2340;
    vertical-align: middle;
  }
  .ana-table tr:last-child td {
    border-bottom: none;
  }
  .ana-table tr:hover td {
    background-color: #f8fafc;
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
    active_center_tab = {"tab": "INSIGHTS"}  # "INSIGHTS" | "ANALYTICS"

    # Root 3-column layout: Left Rail (300px) | Center Panel (flex) | Right Rail (340px)
    with ui.row().classes("w-full h-screen no-wrap m-0 p-0 items-stretch overflow-hidden"):
        # =========================================================================
        # Column 1: Left Rail (Triage Call List)
        # =========================================================================
        with ui.column().classes("w-[320px] h-screen overflow-y-auto p-[20px_14px] bg-[#ffffff] border-r border-[#e2e8f0] flex-shrink-0"):
            ui.html("""
            <div class="flex items-center gap-2 mb-2">
              <div class="w-6 h-6 rounded bg-[#0c2340] text-white flex items-center justify-center font-bold text-[12px] tracking-wider">J</div>
              <span class="text-[14px] uppercase tracking-[1.5px] text-[#0c2340] font-bold">Janus</span>
            </div>
            <div class="text-[11px] uppercase tracking-[1.2px] text-[#5a6d85] font-semibold mb-3">Julius Bär</div>
            <h1 class="serif-font text-[18px] font-bold text-[#0c2340] m-0 leading-tight">Wealth Intelligence</h1>
            <div class="text-[#5a6d85] text-[12px] mt-1 mb-[16px]">Priscilla Ong · Asia desk · 26 Aug 2026</div>
            <div class="text-[11px] uppercase tracking-[.8px] text-[#5a6d85] mb-[10px] font-semibold">Priority Triage Queue</div>
            """)

            triage_box = ui.column().classes("w-full items-stretch gap-0")

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
                    row_el = ui.column().classes(f"callrow w-full items-stretch {'active' if is_active else ''}")
                    row_elements[cid] = row_el

                    with row_el:
                        ui.html(f"""
                        <div class="flex items-center justify-between w-full mb-1.5">
                          <span class="rank-badge">{r['rank']}</span>
                          <div class="flex items-baseline gap-1.5">
                            <span class="text-[9.5px] uppercase tracking-[0.6px] text-[#8492a6] font-medium">Urgency Score</span>
                            <span class="serif-font text-[14px] text-[#1f71ac] font-bold">{r['score']}</span>
                          </div>
                        </div>
                        <div class="font-semibold text-[13.5px] text-[#0c2340] leading-snug">{r['client_name']}</div>
                        <div class="text-[#6b7c93] text-[11.5px] mt-1.5 leading-snug">{r['top_reason']}</div>
                        """).classes("w-full")
                    row_el.on("click", lambda _, c=cid: select_client(c))

        # =========================================================================
        # Column 2: Center Panel (Client Insights View)
        # =========================================================================
        main_container = ui.column().classes("flex-grow h-screen overflow-y-auto p-[26px_34px] max-w-[860px] bg-[#f5f7fa]")

        # =========================================================================
        # Column 3: Right Rail (RM Decision Log & Meeting Prep)
        # =========================================================================
        right_container = ui.column().classes("w-[340px] h-screen overflow-y-auto p-[20px_16px] bg-[#ffffff] border-l border-[#e2e8f0] flex-shrink-0")

        def refresh_right_panel():
            right_container.clear()
            decisions = load_decisions()
            accepted_count = sum(1 for d in decisions if d.get("action") == "ACCEPT")
            modified_count = sum(1 for d in decisions if d.get("action") == "MODIFY")
            rejected_count = sum(1 for d in decisions if d.get("action") == "REJECT")

            with right_container:
                # Header
                ui.html("""
                <div class="border-b border-[#e2e8f0] pb-3 mb-3 w-full">
                  <h2 class="serif-font text-[17px] font-bold text-[#0c2340] m-0">RM Decision Log</h2>
                  <div class="text-[11.5px] text-[#5a6d85] mt-0.5">Audit trail & Meeting Prep talking points</div>
                  <div class="text-[11px] text-[#5a6d85] mt-1">
                    <b>""" + str(len(decisions)) + """</b> total · <span class="text-[#15803d]">""" + str(accepted_count) + """ accepted</span> · <span class="text-[#1f71ac]">""" + str(modified_count) + """ modified</span> · <span class="text-[#b91c1c]">""" + str(rejected_count) + """ rejected</span>
                  </div>
                </div>
                """)

                # Filter buttons
                with ui.row().classes("w-full gap-1 mb-3"):
                    for flt, label in [
                        ("ALL", f"All ({len(decisions)})"),
                        ("ACCEPT", f"Accepted ({accepted_count})"),
                        ("MODIFY", f"Modified ({modified_count})"),
                        ("REJECT", f"Rejected ({rejected_count})"),
                    ]:
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
                    <div class="text-center text-[#5a6d85] text-[12px] italic mt-12 px-4 leading-relaxed">
                      No decisions recorded under this view.<br>
                      Click <b>Accept</b>, <b>Modify</b>, or <b>Reject</b> on any client insight card to log decisions.
                    </div>
                    """)
                    return

                # Decision Cards
                for d in filtered:
                    d_id = d.get("decision_id", "")
                    act = d.get("action", "")
                    pill_color = "#15803d" if act == "ACCEPT" else "#1f71ac" if act == "MODIFY" else "#b91c1c"

                    with ui.column().classes("decision-card w-full gap-1"):
                        with ui.row().classes("w-full justify-between items-center no-wrap"):
                            ui.html(f"""
                            <div class="flex items-center gap-1.5">
                              <span style="background-color: {pill_color}; color: #ffffff; font-size: 9.5px; font-weight: 700; padding: 2px 7px; border-radius: 12px;">{act}</span>
                              <span class="text-[10px] text-[#5a6d85]">{d.get('timestamp', '')[11:16]}</span>
                            </div>
                            """)
                            # Undo/Delete decision button
                            del_btn = ui.button("✕").props("no-caps flat dense").classes("text-[#5a6d85] hover:text-[#b91c1c] text-[11px] p-0 min-w-[16px]")
                            def undo_action(dec_id=d_id):
                                delete_decision_entry(dec_id)
                                ui.notify("Decision removed", type="info", color="#0c2340")
                                refresh_right_panel()
                                render_client_view(active_cid["cid"])
                            del_btn.on("click", undo_action)

                        ui.html(f"""
                        <div class="font-semibold text-[12px] text-[#0c2340] mt-0.5">{d.get('client_name')} <span class="text-[#5a6d85] font-normal">({d.get('client_id')})</span></div>
                        <div class="text-[11.5px] text-[#202936] leading-snug">{d.get('headline')}</div>
                        """)

                        if act == "ACCEPT":
                            ui.html(f"""
                            <div class="bg-[#f0fdf4] border-l-2 border-[#15803d] p-1.5 rounded-r text-[11px] text-[#166534] mt-1">
                              <b>Meeting talking point:</b> {d.get('suggested_action')}
                            </div>
                            """)
                        elif act == "MODIFY":
                            ui.html(f"""
                            <div class="bg-[#eaf2f8] border-l-2 border-[#1f71ac] p-1.5 rounded-r text-[11px] text-[#0c2340] mt-1">
                              <b>RM Custom Plan:</b> {d.get('rm_notes') or d.get('suggested_action')}
                            </div>
                            """)
                        elif act == "REJECT":
                            ui.html(f"""
                            <div class="bg-[#fef2f2] border-l-2 border-[#b91c1c] p-1.5 rounded-r text-[11px] text-[#991b1b] mt-1 italic">
                              <b>Reason:</b> {d.get('rm_notes') or 'Dismissed by RM'}
                            </div>
                            """)

        def render_analytics_view(cid: str):
            c = store.client(cid)
            client_name = c.get("client_name") or cid

            # Desk Metrics
            total_desk_aum = store.clients["total_aum_usd"].sum()
            client_count = len(store.clients)

            crit_count = 0
            for _, cr_row in store.credit.iterrows():
                buf = float(cr_row['margin_call_ltv_pct']) - float(cr_row['ltv_pct_2026-08-26'])
                if buf < 2.0:
                    crit_count += 1

            uncalled_pe = store.commitments["uncalled"].sum()
            total_cash_needs = store.cash_needs["amount"].sum()

            # 1. Desk & Book Overview Header Banner
            ui.html(f"""
            <div class="border-b border-[#e2e8f0] pb-3 mb-4 w-full">
              <div class="flex justify-between items-start">
                <div>
                  <h2 class="serif-font text-[22px] font-bold text-[#0c2340] m-0 leading-tight">Desk & Book Overview</h2>
                  <div class="text-[#5a6d85] text-[12.5px] mt-1">
                    Whole-book exposure across {client_count} private client relationships · Priscilla Ong (Asia Desk) · 26 Aug 2026
                  </div>
                </div>
                <span class="text-[11px] uppercase tracking-wider font-semibold text-[#15803d] bg-[#f0fdf4] px-2.5 py-1 rounded-full border border-[#bbf7d0]">Live Book Status</span>
              </div>
            </div>
            """).classes("w-full")

            # 2. Four KPI Stat Panels on Top
            ui.html(f"""
            <div class="grid grid-cols-4 gap-3 w-full mb-5">
              <div class="bg-white p-3.5 rounded-lg border border-[#e2e8f0] shadow-sm">
                <div class="text-[10px] uppercase font-bold tracking-[0.6px] text-[#5a6d85]">Total Desk AUM</div>
                <div class="serif-font text-[21px] font-bold text-[#0c2340] mt-0.5">${total_desk_aum/1e6:.1f}M</div>
                <div class="text-[11px] text-[#15803d] font-semibold mt-0.5">{client_count} Active Relationships</div>
              </div>
              <div class="bg-white p-3.5 rounded-lg border border-[#e2e8f0] shadow-sm">
                <div class="text-[10px] uppercase font-bold tracking-[0.6px] text-[#5a6d85]">Lombard Credit Facilities</div>
                <div class="serif-font text-[21px] font-bold text-[#0c2340] mt-0.5">{len(store.credit)} Active</div>
                <div class="text-[11px] text-[#b91c1c] font-semibold mt-0.5">{crit_count} Near Margin Call (&lt;2%)</div>
              </div>
              <div class="bg-white p-3.5 rounded-lg border border-[#e2e8f0] shadow-sm">
                <div class="text-[10px] uppercase font-bold tracking-[0.6px] text-[#5a6d85]">Uncalled PE Commitments</div>
                <div class="serif-font text-[21px] font-bold text-[#0c2340] mt-0.5">${uncalled_pe/1e6:.1f}M</div>
                <div class="text-[11px] text-[#1f71ac] font-semibold mt-0.5">{len(store.commitments)} Institutional Funds</div>
              </div>
              <div class="bg-white p-3.5 rounded-lg border border-[#e2e8f0] shadow-sm">
                <div class="text-[10px] uppercase font-bold tracking-[0.6px] text-[#5a6d85]">Scheduled Cash Outflows</div>
                <div class="serif-font text-[21px] font-bold text-[#0c2340] mt-0.5">${total_cash_needs/1e6:.1f}M</div>
                <div class="text-[11px] text-[#5a6d85] font-semibold mt-0.5">{len(store.cash_needs)} Events on File</div>
              </div>
            </div>
            """).classes("w-full")

            # 3. Asset Class Allocation for the CURRENTLY SELECTED client
            holdings_c = store.holdings[(store.holdings['client_id'] == cid) & (store.holdings['snapshot_date'] == TODAY)]
            ac_sum = holdings_c.groupby('asset_class')['market_value_usd'].sum().sort_values(ascending=False)
            total_client_mv = float(ac_sum.sum())

            ac_colors = {
                "Equity": "#1f71ac",
                "Fixed Income": "#0c2340",
                "Alternatives": "#0d9488",
                "Cash and Equivalents": "#15803d",
                "Commodities": "#d97706",
                "Structured Products": "#7c3aed"
            }

            if total_client_mv > 0:
                bar_segments = ""
                for ac, val in ac_sum.items():
                    pct = (val / total_client_mv * 100)
                    color = ac_colors.get(ac, "#64748b")
                    # flex-grow proportional to holding value guarantees 100% bar fill with zero rounding wrap
                    bar_segments += f'<div style="flex: {val:.2f} 0 0%; height: 100%; background-color: {color};" title="{ac}: {pct:.1f}% (${val:,.0f})"></div>'

                table_rows = ""
                for ac, val in ac_sum.items():
                    pct = (val / total_client_mv * 100)
                    color = ac_colors.get(ac, "#64748b")
                    table_rows += f"""
                    <tr>
                      <td class="font-medium text-[#0c2340]">
                        <span class="inline-block w-2.5 h-2.5 rounded-sm mr-2" style="background-color: {color};"></span>
                        {ac}
                      </td>
                      <td class="text-left font-semibold text-[#0c2340]">${val:,.0f}</td>
                      <td class="text-left font-bold text-[#1f71ac]">{pct:.1f}%</td>
                      <td class="w-[140px]">
                        <div class="w-full bg-[#f1f5f9] h-2 rounded-full overflow-hidden">
                          <div style="width: {pct:.1f}%; background-color: {color}; height: 100%;"></div>
                        </div>
                      </td>
                    </tr>
                    """

                ui.html(f"""
                <div class="ana-card w-full">
                  <div class="flex justify-between items-center mb-2.5">
                    <div>
                      <h3 class="serif-font text-[16px] font-bold text-[#0c2340] m-0">Asset Class Allocation — {client_name}</h3>
                      <div class="text-[11.5px] text-[#5a6d85] mt-0.5">Portfolio exposure for {client_name} ({cid}) · Total ${total_client_mv:,.0f} USD</div>
                    </div>
                    <span class="text-[11px] font-semibold text-[#1f71ac] bg-[#eaf2f8] px-2.5 py-0.5 rounded border border-[#d2e3f2]">100% Capital Accounted</span>
                  </div>
                  
                  <div class="w-full h-3.5 rounded-full overflow-hidden flex flex-nowrap mb-3.5 bg-[#e2e8f0]">
                    {bar_segments}
                  </div>

                  <table class="ana-table">
                    <thead>
                      <tr>
                        <th>Asset Class</th>
                        <th class="text-left">Market Value (USD)</th>
                        <th class="text-left">Share (%)</th>
                        <th>Distribution</th>
                      </tr>
                    </thead>
                    <tbody>
                      {table_rows}
                    </tbody>
                  </table>
                </div>
                """).classes("w-full")
            else:
                ui.html(f"""
                <div class="ana-card w-full">
                  <h3 class="serif-font text-[16px] font-bold text-[#0c2340] m-0">Asset Class Allocation — {client_name}</h3>
                  <div class="text-[#5a6d85] text-[12px] mt-2 italic">No active holdings on file for this client as of 26 Aug 2026.</div>
                </div>
                """).classes("w-full")

        def render_client_view(cid: str):
            main_container.clear()
            c = store.client(cid)
            if not c:
                with main_container:
                    ui.label("Client not found").classes("text-[#5b6672]")
                return

            with main_container:
                # Top Navigation Tabs: Client Insights & Decisions vs Desk & Portfolio Analytics
                is_insights = (active_center_tab["tab"] == "INSIGHTS")
                is_analytics = (active_center_tab["tab"] == "ANALYTICS")

                with ui.row().classes("w-full justify-between items-center border-b border-[#e2e8f0] pb-3 mb-4 no-wrap"):
                    with ui.row().classes("bg-[#e2e8f0] p-1 rounded-lg gap-1 items-center"):
                        if is_insights:
                            tab_ins = ui.button("Client Insights & Decisions").props("no-caps unelevated dense").classes(
                                "px-3.5 py-1.5 rounded-md text-[12px] font-bold bg-[#0c2340] text-white shadow-sm"
                            )
                        else:
                            tab_ins = ui.button("Client Insights & Decisions").props("no-caps unelevated dense").classes(
                                "px-3.5 py-1.5 rounded-md text-[12px] font-semibold bg-white border border-[#cbd5e1] hover:bg-[#f8fafc]"
                            ).style("color: #0c2340 !important;")

                        if is_analytics:
                            tab_ana = ui.button("Desk & Portfolio Analytics").props("no-caps unelevated dense").classes(
                                "px-3.5 py-1.5 rounded-md text-[12px] font-bold bg-[#0c2340] text-white shadow-sm"
                            )
                        else:
                            tab_ana = ui.button("Desk & Portfolio Analytics").props("no-caps unelevated dense").classes(
                                "px-3.5 py-1.5 rounded-md text-[12px] font-semibold bg-white border border-[#cbd5e1] hover:bg-[#f8fafc]"
                            ).style("color: #0c2340 !important;")

                        def switch_to_ins():
                            active_center_tab["tab"] = "INSIGHTS"
                            render_client_view(cid)

                        def switch_to_ana():
                            active_center_tab["tab"] = "ANALYTICS"
                            render_client_view(cid)

                        tab_ins.on("click", switch_to_ins)
                        tab_ana.on("click", switch_to_ana)

                    ui.html(f'<div class="text-[11.5px] text-[#5a6d85]">Selected Client: <b class="text-[#0c2340]">{c.get("client_name")}</b></div>')

                # If in Analytics tab mode, render the desk & client asset allocation view and return
                if is_analytics:
                    render_analytics_view(cid)
                    return

                insights = get_cached_insights(store, cid)

                # Client Header with safe missing-data / NaN checks
                aum_str = safe_aum(c.get("total_aum_usd"))
                age_s = safe_age(c.get("age"))
                age_part = f"{age_s} · " if age_s else ""
                risk = c.get("risk_profile") or "Standard"
                curr = c.get("base_currency") or "USD"
                objs = c.get("objectives") or "Capital preservation & growth"
                name = c.get("client_name") or cid

                header_html = f"""
                <div class="border-b border-[#e2e8f0] pb-4 mb-2 w-full">
                  <div class="flex justify-between items-start">
                    <div>
                      <h2 class="serif-font text-[24px] font-bold text-[#0c2340] m-0 leading-tight">{name}</h2>
                      <div class="text-[#5a6d85] text-[13px] mt-1">
                        {cid} · {age_part}{risk} mandate · {curr} · AUM {aum_str} USD
                      </div>
                    </div>
                    <span class="text-[11px] uppercase tracking-wider font-semibold text-[#1f71ac] bg-[#eaf2f8] px-2.5 py-1 rounded-full border border-[#d2e3f2]">Julius Bär Mandate</span>
                  </div>
                  <div class="text-[#0c2340] bg-[#eaf2f8] border-l-4 border-[#1f71ac] p-[9px_13px] rounded-r-md text-[12.5px] mt-3 leading-normal font-medium">
                    <span class="text-[10.5px] uppercase tracking-[0.7px] text-[#1f71ac] font-bold block mb-0.5">Mandate Objective</span>
                    {objs}
                  </div>
                </div>
                """
                ui.html(header_html).classes("w-full")

                # Insights Cards
                if not insights:
                    ui.html('<div class="text-[#5a6d85] mt-8">No flags for this client.</div>')
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
                          <span class="text-[11px] uppercase tracking-[.8px] text-[#5a6d85] font-semibold">{ins.type}</span>
                          <span class="ml-auto text-[11px] text-[#5a6d85]">confidence <b class="text-[#0c2340]">{ins.confidence}</b></span>
                        </div>
                        <div class="serif-font text-[16px] font-bold text-[#0c2340] leading-snug my-1">{ins.headline}</div>
                        """
                        ui.html(card_top_html).classes("w-full")

                        # 1. Computed Metrics & Breakdown (Structured Financial Data, NOT Buttons)
                        contribs = ins.contributions or []
                        metrics_html = ""
                        if len(contribs) == 1:
                            c = contribs[0]
                            val = c.value
                            val_color = "text-[#b91c1c]" if isinstance(val, (int, float)) and val < 0 else "text-[#0c2340]"
                            val_str = ("−" + format_val(abs(val))) if isinstance(val, (int, float)) and val < 0 else format_val(val)
                            unit_str = f"{c.unit}" if c.unit == "%" else f" {c.unit}" if c.unit else ""
                            detail_txt = c.detail or "Mandate & portfolio position exposure"

                            metrics_html = f"""
                            <div class="metric-card flex items-center justify-between w-full my-2.5" title="{c.label}: {detail_txt}">
                              <div class="min-w-0 flex-1 pr-3">
                                <div class="text-[10px] uppercase tracking-wider font-bold text-[#5a6d85]">Exposure & Position Factor</div>
                                <div class="text-[13.5px] font-bold text-[#0c2340] mt-0.5 leading-snug break-words">{c.label}</div>
                                <div class="text-[11.5px] text-[#5a6d85] mt-0.5 leading-normal break-words">{detail_txt}</div>
                              </div>
                              <div class="text-right pl-4 border-l border-[#e2e8f0] flex-shrink-0">
                                <div class="text-[20px] font-bold {val_color} leading-none">{val_str}{unit_str}</div>
                                <div class="text-[10px] uppercase tracking-wider text-[#5a6d85] font-semibold mt-1">Portfolio Weight</div>
                              </div>
                            </div>
                            """
                        elif len(contribs) > 1:
                            grid_items = []
                            # If 4 or more items (e.g. 6-item attribution), use 2-column wide cards so long fund names fit cleanly without truncation
                            if len(contribs) >= 4:
                                for c in contribs:
                                    val = c.value
                                    is_neg = isinstance(val, (int, float)) and val < 0
                                    is_pos = isinstance(val, (int, float)) and val > 0 and c.unit == "USD"
                                    val_color = "text-[#b91c1c]" if is_neg else "text-[#15803d]" if is_pos else "text-[#0c2340]"
                                    val_str = ("−" + format_val(abs(val))) if is_neg else ("+" + format_val(val)) if is_pos else format_val(val)
                                    unit_str = f"{c.unit}" if c.unit == "%" else f" {c.unit}" if c.unit else ""
                                    detail_txt = c.detail or "Decomposition factor"

                                    grid_items.append(f"""
                                    <div class="metric-grid-item p-3 flex justify-between items-center gap-3 text-left" title="{c.label}: {detail_txt}">
                                      <div class="min-w-0 flex-1">
                                        <div class="text-[11px] uppercase tracking-wider font-bold text-[#0c2340] leading-snug break-words">{c.label}</div>
                                        <div class="text-[11px] text-[#5a6d85] mt-0.5 leading-normal break-words">{detail_txt}</div>
                                      </div>
                                      <div class="text-right flex-shrink-0 pl-3 border-l border-[#e2e8f0]">
                                        <div class="text-[15px] font-bold {val_color} leading-none">{val_str}{unit_str}</div>
                                      </div>
                                    </div>
                                    """)
                                grid_container = f'<div class="grid grid-cols-1 md:grid-cols-2 gap-2.5 w-full">{"".join(grid_items)}</div>'
                            else:
                                # 2 or 3 items: clean balanced multi-column KPI cards with full text wrap
                                cols = len(contribs)
                                for c in contribs:
                                    val = c.value
                                    is_neg = isinstance(val, (int, float)) and val < 0
                                    is_pos = isinstance(val, (int, float)) and val > 0 and c.unit == "USD"
                                    val_color = "text-[#b91c1c]" if is_neg else "text-[#15803d]" if is_pos else "text-[#0c2340]"
                                    val_str = ("−" + format_val(abs(val))) if is_neg else ("+" + format_val(val)) if is_pos else format_val(val)
                                    unit_str = f"{c.unit}" if c.unit == "%" else f" {c.unit}" if c.unit else ""
                                    detail_txt = c.detail or "Decomposition factor"

                                    grid_items.append(f"""
                                    <div class="metric-grid-item p-2.5 flex flex-col justify-between text-center" title="{c.label}: {detail_txt}">
                                      <div class="text-[10.5px] uppercase tracking-wider font-bold text-[#5a6d85] leading-snug break-words">{c.label}</div>
                                      <div class="text-[16px] font-bold {val_color} my-1 leading-tight">{val_str}{unit_str}</div>
                                      <div class="text-[10.5px] text-[#5a6d85] leading-tight break-words">{detail_txt}</div>
                                    </div>
                                    """)
                                grid_container = f'<div class="grid grid-cols-{cols} gap-2.5 w-full">{"".join(grid_items)}</div>'

                            metrics_html = f"""
                            <div class="metric-card w-full my-2.5">
                              <div class="text-[10px] uppercase tracking-wider font-bold text-[#5a6d85] mb-2.5">Performance & Exposure Decomposition</div>
                              {grid_container}
                            </div>
                            """

                        # 2. Event Log & Evidence Trail (Informational, NOT Buttons, Full Text Wrap)
                        events_html = ""
                        if ins.evidence.event_refs:
                            ev_badges = []
                            for ev in ins.evidence.event_refs:
                                ev_badges.append(
                                    f'<div class="event-badge w-full leading-normal">'
                                    f'<span class="font-bold text-[#0c2340] flex-shrink-0">⚑ Event Log:</span> '
                                    f'<span class="break-words text-[#334155]">{ev}</span>'
                                    f'</div>'
                                )
                            events_html = f'<div class="my-2 flex flex-col gap-1.5 w-full">{"".join(ev_badges)}</div>'

                        # 3. Caveats
                        caveats_html = ""
                        if ins.caveats:
                            caveats_html = "".join(f'<div class="caveat">⚠︎ {cav}</div>' for cav in ins.caveats)

                        # 4. RM Note on File (Prominent, uncollapsed memo card)
                        rm_note_html = ""
                        if ins.evidence.rm_note:
                            rm_note_html = f"""
                            <div class="rmnote-box w-full">
                              <div class="text-[10.5px] uppercase tracking-wider font-bold text-[#8a5b13] mb-0.5">
                                RM Note on File
                              </div>
                              <div class="text-[#202936] italic leading-normal">{ins.evidence.rm_note}</div>
                            </div>
                            """

                        ui.html(metrics_html + events_html + caveats_html + rm_note_html).classes("w-full")

                        # 5. Recommendation & Decision Action Box
                        with ui.column().classes("action-container w-full gap-0"):
                            ui.html(f"""
                            <div class="text-[10px] uppercase tracking-wider font-bold text-[#1f71ac] mb-1">Recommended Next Step</div>
                            <div class="text-[13px] font-semibold text-[#0c2340] leading-snug mb-3">{ins.suggested_action}</div>
                            <div class="w-full border-t border-[#d2e3f2] mb-3"></div>
                            """)

                            # Interactive Button Row
                            with ui.row().classes("gap-2 mt-2 items-center flex-wrap"):
                                is_acc = (current_status == "ACCEPT")
                                is_mod = (current_status == "MODIFY")
                                is_rej = (current_status == "REJECT")

                                btn_accept = ui.button(
                                    "Accepted ✓" if is_acc else "Accept"
                                ).props("no-caps unelevated").classes(
                                    f"btn-action {'btn-accepted' if is_acc else 'btn-outline'}"
                                )
                                if is_acc:
                                    btn_accept.style("background: #15803d !important; background-color: #15803d !important; color: #ffffff !important; border: 1px solid #15803d !important;")

                                btn_modify = ui.button(
                                    "Modified ✓" if is_mod else "Modify"
                                ).props("no-caps unelevated").classes(
                                    f"btn-action {'btn-modified' if is_mod else 'btn-outline'}"
                                )
                                if is_mod:
                                    btn_modify.style("background: #1f71ac !important; background-color: #1f71ac !important; color: #ffffff !important; border: 1px solid #1f71ac !important;")

                                btn_reject = ui.button(
                                    "Rejected ✕" if is_rej else "Reject"
                                ).props("no-caps unelevated").classes(
                                    f"btn-action {'btn-rejected' if is_rej else 'btn-outline'}"
                                )
                                if is_rej:
                                    btn_reject.style("background: #b91c1c !important; background-color: #b91c1c !important; color: #ffffff !important; border: 1px solid #b91c1c !important;")

                                btn_enquiry = ui.button("🔎 Market Context Enquiry").props("no-caps unelevated").classes(
                                    "btn-action btn-ghost"
                                )

                                def handle_accept(cur_ins=ins, client_n=name):
                                    record_decision(cid, client_n, cur_ins, "ACCEPT")
                                    ui.notify(f"Accepted & logged to decisions.json for {client_n}", type="positive", color="#15803d")
                                    refresh_right_panel()
                                    render_client_view(cid)

                                def open_modify_dialog(cur_ins=ins, client_n=name):
                                    with ui.dialog() as mod_dialog, ui.card().classes("w-[500px] p-5 border border-[#e2e8f0] rounded-xl shadow-lg"):
                                        ui.html(f'<h3 class="serif-font text-[17px] font-bold text-[#0c2340] m-0">Modify Action: {client_n}</h3>')
                                        ui.html(f'<div class="text-[11.5px] text-[#5a6d85] mt-0.5 mb-2">{cur_ins.headline}</div>')
                                        ui.html('<div class="text-[10.5px] uppercase tracking-[.6px] text-[#5a6d85] font-semibold">Original System Suggestion:</div>')
                                        ui.html(f'<div class="text-[12px] bg-[#f8fafc] p-2.5 rounded border border-[#e2e8f0] my-1.5 leading-relaxed text-[#202936]">{cur_ins.suggested_action}</div>')

                                        ui.html('<div class="text-[10.5px] uppercase tracking-[.6px] text-[#5a6d85] font-semibold mt-2">RM Revised Instructions / Talking Points:</div>')
                                        existing_note = existing_dec.get("rm_notes") if existing_dec else ""
                                        custom_inp = ui.textarea(value=existing_note or cur_ins.suggested_action).classes("w-full mt-1 text-[12.5px]").props("outlined rows=3")

                                        with ui.row().classes("w-full justify-end gap-2 mt-4"):
                                            ui.button("Cancel", on_click=mod_dialog.close).props("no-caps unelevated").classes("btn-action btn-outline")

                                            def save_mod():
                                                note_val = custom_inp.value.strip()
                                                record_decision(cid, client_n, cur_ins, "MODIFY", note_val)
                                                mod_dialog.close()
                                                ui.notify(f"Modified action logged to decisions.json", type="positive", color="#1f71ac")
                                                refresh_right_panel()
                                                render_client_view(cid)

                                            ui.button("Save Revision", on_click=save_mod).props("no-caps unelevated").classes("btn-action btn-dark")
                                    mod_dialog.open()

                                def handle_reject(cur_ins=ins, client_n=name):
                                    record_decision(cid, client_n, cur_ins, "REJECT", "Dismissed by RM (intentional mandate drift / prior agreement)")
                                    ui.notify(f"Rejected & logged to decisions.json", type="negative", color="#b91c1c", icon="close")
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
                                    inp_q = ui.input(value=f'Tell me more about: "{clean_head}"').classes("flex-grow text-[12px]").props("dense outlined bg-color=white")
                                    btn_ask = ui.button("Ask").props("no-caps unelevated").classes("btn-action btn-dark")

                                # Section 1: From computed data (Ask Why)
                                with ui.column().classes("enqsection w-full"):
                                    ui.html('<div class="enqlabel">From computed data</div>')
                                    ans_label = ui.html('<div class="text-[12.5px] leading-relaxed text-[#202936]">—</div>').classes("w-full")

                                # Section 2: External market color (Web search)
                                with ui.column().classes("enqsection ext w-full"):
                                    ui.html('<div class="mktlabel">⚠ External market color — unverified, not used in the numbers above</div>')
                                    ui.html('<div class="text-[10.5px] text-[#b45309] mb-1 italic">ℹ Synthetic IDs (e.g. PF-0019) are automatically mapped to underlying real-world asset class & sector news.</div>')
                                    mkt_label = ui.html('<div class="text-[12.5px] leading-relaxed text-[#202936]">—</div>').classes("w-full")

                                async def run_enquiry(q_elem=inp_q, a_elem=ans_label, m_elem=mkt_label, cur_ins=ins):
                                    query_text = q_elem.value.strip()
                                    if not query_text:
                                        return
                                    a_elem.set_content('<span class="text-[#5a6d85] italic">Asking computed engine…</span>')
                                    m_elem.set_content('<span class="text-[#5a6d85] italic">Searching reputable financial press…</span>')

                                    # Async execution so the UI remains fluid
                                    async def fetch_chat():
                                        try:
                                            ans = await asyncio.to_thread(ask_why, store, cid, query_text)
                                            if ans:
                                                a_elem.set_content(f'<div class="text-[12.5px] leading-relaxed whitespace-pre-wrap">{ans}</div>')
                                            else:
                                                a_elem.set_content(
                                                    '<div class="text-[12px] text-[#5a6d85] italic">'
                                                    'Ask Why is unavailable right now (no OPENAI_API_KEY configured, '
                                                    'or the model call failed) — the rest of the workbench is unaffected.</div>'
                                                )
                                        except Exception as e:
                                            a_elem.set_content(f'<div class="text-[12px] text-[#5a6d85] italic">Ask Why unavailable: {e}</div>')

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
                                                m_elem.set_content(f'<div>{cleaned_summary}</div><div class="mt-2 pt-2 border-t border-[#fed7aa]">{src_links}</div>')
                                            else:
                                                m_elem.set_content(
                                                    '<div class="text-[12px] text-[#b45309] italic">'
                                                    'External financial press search found no matching reporting for this specific query. '
                                                    '(Note: Specific portfolio codes like PF-0019 are synthetic for this case study; try searching for broader real-world themes like "Global Equities" or "Corporate Bonds").</div>'
                                                )
                                        except Exception as e:
                                            m_elem.set_content(f'<div class="text-[12px] text-[#b45309] italic">Market search unavailable: {e}</div>')

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
    ui.run(title="Janus | Julius Bär — Wealth Intelligence", port=port, reload=False, show=True)
