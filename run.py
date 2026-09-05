"""
run.py — one-command smoke test. Proves the engine works end to end, offline.

    python run.py                # insights for the default client + triage top 5
    python run.py CL-0002        # insights for a specific client

To narrate with a real model instead of the deterministic fallback:
    export OPENAI_API_KEY=...   then add --llm
"""
import sys
from src.ingest import get_store
from src.build import build_client_insights
from src.engine.prioritise import build_triage

DEFAULT = "CL-0012"   # the README's poster-child: 71, income mandate, bond drawdown


def show_client(store, cid, use_llm):
    print("=" * 78)
    print(f"CLIENT {cid} — {store.client_name(cid)}")
    c = store.client(cid)
    print(f"  {c.get('age')}y · {c.get('risk_profile')} · {c.get('base_currency')} · "
          f"AUM {c.get('total_aum_usd'):,.0f} USD")
    print(f"  Objective: {c.get('objectives')}")
    print("=" * 78)
    for ins in build_client_insights(store, cid, narrate_llm=use_llm):
        print(f"\n[{ins.severity.upper()}] {ins.type}  ·  confidence={ins.confidence}  ({ins.id})")
        print(f"  {ins.headline}")
        print(f"  NARRATIVE: {ins.narrative}")
        if ins.evidence.event_refs:
            print(f"  EVENTS: {ins.evidence.event_refs}")
        if ins.evidence.rm_note:
            print(f"  RM NOTE: {ins.evidence.rm_note[:110]}...")
        if ins.caveats:
            print(f"  CAVEATS: {ins.caveats}")


def show_triage(store, use_llm):
    print("\n" + "#" * 78)
    print("TRIAGE — who to call first")
    print("#" * 78)
    for r in build_triage(store, narrate_llm=use_llm)[:6]:
        print(f"  #{r['rank']:>2}  score {r['score']:>6.1f}  {r['client_id']}  "
              f"{r['client_name'][:22]:22}  {r['top_reason'][:60]}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_llm = "--llm" in sys.argv
    cid = args[0] if args else DEFAULT
    store = get_store()
    show_client(store, cid, use_llm)
    show_triage(store, use_llm)
