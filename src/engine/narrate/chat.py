"""
chat.py — "Ask Why": a tool-use loop scoped to exactly ONE open client.

Guardrails enforced here (see CLAUDE.md):
  - The model never sees raw data. It only ever receives the return values of
    the tools below — already-computed facts from the deterministic engine.
  - It may not state a number, date, or cause that didn't come back from a
    tool call made THIS turn (enforced by the system prompt, not by code —
    there is no raw data in scope for it to fabricate from in the first place).
  - Every tool below is closed over one client_id at call time. There is no
    tool that can address another client, so the model has no path to leak
    one client's data into another's conversation.
  - Any error, missing key, exhausted rounds, or bad client_id returns None —
    never raises. The API layer turns None into a clean "chat unavailable".

Call -> run tool locally -> feed result back -> repeat, capped at MAX_ROUNDS.
Uses OpenAI's chat-completions function-calling API.
"""
from __future__ import annotations
import json
import os

from src.ingest import DataStore, SNAPSHOTS, TODAY
from src.build import build_client_insights
from src.engine import attribution, mandate, liquidity

MAX_ROUNDS = 5
MODEL = "gpt-4o"

SYSTEM_PROMPT_TMPL = (
    "You are 'Ask Why', a grounded explanation assistant for a private-bank "
    "Relationship Manager reviewing exactly one client: {client_name} ({client_id}). "
    "Your tools return facts already computed by the bank's deterministic engine "
    "for this client only — you have no other source of data whatsoever.\n\n"
    "STRICT RULES, no exceptions:\n"
    "1. Every number, date, holding name, or cause you state MUST come from a "
    "tool result returned in THIS conversation. Never state a figure from memory "
    "or from general knowledge of markets or geopolitics.\n"
    "2. If you cite an event as a cause, name the specific event description and "
    "date exactly as a tool returned it — never a vague 'market conditions'.\n"
    "3. If a holding has no linked event in a tool result, say it is UNEXPLAINED. "
    "Never guess a plausible-sounding cause for it.\n"
    "4. If the RM's notes (get_rm_notes) say something that contradicts what the "
    "computed data shows (get_portfolio_attribution / get_insights), point out "
    "the contradiction explicitly — do not silently pick one.\n"
    "5. You are scoped to {client_name} only. If asked about any other client, "
    "refuse and say so.\n"
    "6. If no tool result is relevant to the question, say so honestly instead "
    "of filling the gap.\n\n"
    "Call tools as needed, then answer in 2-5 plain-prose sentences."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_attribution",
            "description": (
                "Exact trading/price/FX reconciliation for this client's holdings "
                "between two snapshot dates: trading_effect + price_effect + fx_effect "
                "== the observed USD change, for every holding. Includes which "
                "holdings are grounded to a matching event and which are UNEXPLAINED "
                "(no matching event — do not guess a cause for these). This is the "
                "only source of attribution numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "t0": {"type": "string",
                           "description": f"start snapshot date, one of {SNAPSHOTS}"},
                    "t1": {"type": "string",
                           "description": f"end snapshot date, one of {SNAPSHOTS}"},
                },
                "required": ["t0", "t1"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rm_notes",
            "description": "This client's relationship-manager notes, verbatim, most recent first.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_insights",
            "description": (
                "All current computed insights for this client (attribution, mandate, "
                "concentration, collateral, liquidity), each with its own evidence, "
                "confidence, and caveats."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_mandate_bands",
            "description": "This client's mandate allocation-band and single-position limit breaches, if any.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_liquidity",
            "description": "This client's near-term liquidity demand vs. readily sellable assets.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _make_tool_runner(store: DataStore, client_id: str):
    """Every closure here is bound to client_id — no tool can be redirected
    to another client's data, regardless of what the model asks for."""

    def run(name: str, tool_input: dict) -> dict:
        if name == "get_portfolio_attribution":
            t0 = tool_input.get("t0") or SNAPSHOTS[0]
            t1 = tool_input.get("t1") or TODAY
            return attribution.reconcile(store, client_id, t0, t1)
        if name == "get_rm_notes":
            return {"notes": store.notes_of(client_id)}
        if name == "get_insights":
            insights = build_client_insights(store, client_id, narrate_llm=False)
            return {"insights": [i.to_dict() for i in insights]}
        if name == "check_mandate_bands":
            return {"breaches": mandate.check_client(store, client_id)}
        if name == "check_liquidity":
            return liquidity.check_client(store, client_id)
        return {"error": f"unknown tool: {name}"}

    return run


def ask_why(store: DataStore, client_id: str, question: str) -> str | None:
    """Answer one RM question about one client, grounded entirely via tool
    calls. Returns None on no key, any error, or an unresolved conversation —
    never raises. The caller (api/main.py) turns None into "chat unavailable"."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    client_row = store.client(client_id)
    if not client_row:
        return None

    try:
        import openai
        try:
            import truststore
            truststore.inject_into_ssl()
        except Exception:
            pass  # no corporate proxy / truststore unavailable — plain SSL is fine

        api = openai.OpenAI()
        run_tool = _make_tool_runner(store, client_id)
        system = SYSTEM_PROMPT_TMPL.format(
            client_name=client_row.get("client_name", client_id), client_id=client_id,
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]

        for _ in range(MAX_ROUNDS):
            resp = api.chat.completions.create(
                model=MODEL, max_tokens=800, tools=TOOLS, messages=messages,
            )
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))

            if not msg.tool_calls:
                text = (msg.content or "").strip()
                return text or None

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    result = run_tool(tc.function.name, args)
                except Exception as e:
                    result = {"error": str(e)}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })

        return None  # exhausted MAX_ROUNDS without a final answer
    except Exception:
        return None
