"""
watchlist.py — the single shared ticker list both tools read.

Stored in tickers.json at the repo root. The Streamlit analyzer reads the
symbols; the embedded corridor chart gets seeded from the same list. One place
to manage what you track.
"""

from __future__ import annotations
import json
from pathlib import Path

WATCHLIST_PATH = Path(__file__).parent / "tickers.json"

DEFAULT = [
    {"symbol": "NVDA", "name": "NVIDIA"},
    {"symbol": "AMD",  "name": "AMD"},
    {"symbol": "INTC", "name": "Intel"},
    {"symbol": "TSLA", "name": "Tesla"},
]


def _normalize(items) -> list[dict]:
    out, seen = [], set()
    for it in items or []:
        sym = str(it.get("symbol", "")).upper().strip()
        if sym and sym not in seen:
            seen.add(sym)
            out.append({"symbol": sym, "name": (it.get("name") or sym).strip()})
    return out


def load_watchlist() -> list[dict]:
    try:
        data = json.loads(WATCHLIST_PATH.read_text())
        items = _normalize(data.get("watchlist", []))
        return items or list(DEFAULT)
    except Exception:
        return list(DEFAULT)


def save_watchlist(items) -> list[dict]:
    """Write tickers.json. Works locally; on Streamlit Cloud this persists only
    for the session (see README), so commit the file to GitHub for permanence."""
    norm = _normalize(items)
    WATCHLIST_PATH.write_text(json.dumps({"watchlist": norm}, indent=2) + "\n")
    return norm


def symbols() -> list[str]:
    return [it["symbol"] for it in load_watchlist()]
