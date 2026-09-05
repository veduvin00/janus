"""
ingest.py — load every file once into a DataStore.

Deliberately boring. No cleverness here: just load, type, and expose the frames
plus the five snapshot dates. All downstream engines read from this.

We work in USD throughout (holdings.market_value_usd is provided), so we avoid
re-deriving FX. Where a pure price effect is needed we isolate it in local ccy
and convert with the holding's own implied FX — see engine/attribution.py.
"""
from __future__ import annotations
import json
import os
import pandas as pd

# The five dated snapshots, in order. The whole challenge lives in the comparison.
SNAPSHOTS = ["2025-12-31", "2026-02-27", "2026-03-31", "2026-06-30", "2026-08-26"]
TODAY = "2026-08-26"

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


class DataStore:
    def __init__(self, data_dir: str = _DATA_DIR):
        d = data_dir
        self.clients = pd.read_csv(os.path.join(d, "clients.csv"))
        self.portfolios = pd.read_csv(os.path.join(d, "portfolios.csv"))
        self.holdings = pd.read_csv(os.path.join(d, "holdings.csv"))
        self.instruments = pd.read_csv(os.path.join(d, "instruments.csv"))
        self.mandates = pd.read_csv(os.path.join(d, "mandates.csv"))
        self.transactions = pd.read_csv(os.path.join(d, "transactions.csv"))
        self.credit = pd.read_csv(os.path.join(d, "credit_facilities.csv"))
        self.commitments = pd.read_csv(os.path.join(d, "commitments.csv"))
        self.cash_needs = pd.read_csv(os.path.join(d, "planned_cash_needs.csv"))
        self.market = pd.read_csv(os.path.join(d, "market_context.csv"))
        self.events = pd.read_csv(os.path.join(d, "event_log.csv"))
        with open(os.path.join(d, "rm_notes.json")) as f:
            self.rm_notes = json.load(f)

        self.holdings["snapshot_date"] = self.holdings["snapshot_date"].astype(str)
        self.transactions["trade_date"] = pd.to_datetime(self.transactions["trade_date"])
        self.events["event_date"] = pd.to_datetime(self.events["event_date"])

    # --- small convenience accessors -------------------------------------------------
    def client(self, client_id: str) -> dict:
        row = self.clients[self.clients.client_id == client_id]
        return row.iloc[0].to_dict() if len(row) else {}

    def client_name(self, client_id: str) -> str:
        return self.client(client_id).get("client_name", client_id)

    def client_ids(self) -> list[str]:
        return list(self.clients.client_id)

    def portfolios_of(self, client_id: str) -> pd.DataFrame:
        return self.portfolios[self.portfolios.client_id == client_id]

    def holdings_of(self, client_id: str, snapshot: str | None = None) -> pd.DataFrame:
        h = self.holdings[self.holdings.client_id == client_id]
        if snapshot:
            h = h[h.snapshot_date == snapshot]
        return h

    def notes_of(self, client_id: str) -> list[dict]:
        return [n for n in self.rm_notes if n["client_id"] == client_id]

    def facilities_of(self, client_id: str) -> pd.DataFrame:
        return self.credit[self.credit.client_id == client_id]


_STORE: DataStore | None = None


def get_store() -> DataStore:
    """Cached singleton so the API loads the data once."""
    global _STORE
    if _STORE is None:
        _STORE = DataStore()
    return _STORE
