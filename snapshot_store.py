"""
snapshot_store.py — the fix for both the lag and the "store until I hit Update".

Instead of re-fetching FMP every rerun, each ticker's full analysis is computed
ONCE and frozen to disk as a snapshot (snapshots/SYMBOL.json). The UI reads the
frozen snapshot instantly on every interaction. Data only refreshes when you
explicitly hit Update for that ticker (or Update all).

This means:
  • no network calls on tab switches, slider drags, or expander clicks
  • the corridor chart and analyzer read the same frozen numbers
  • you control exactly when data changes
"""

from __future__ import annotations
import json
import time
from pathlib import Path

SNAP_DIR = Path(__file__).parent / "snapshots"
SNAP_DIR.mkdir(exist_ok=True)


def _path(sym: str) -> Path:
    return SNAP_DIR / f"{sym.upper()}.json"


def has_snapshot(sym: str) -> bool:
    return _path(sym).exists()


def load_snapshot(sym: str) -> dict | None:
    p = _path(sym)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def save_snapshot(sym: str, result: dict, price_series: list | None = None,
                  trading: dict | None = None) -> dict:
    """Freeze a computed analysis + (optional) price series + trading row."""
    existing = load_snapshot(sym) or {}
    payload = {
        "symbol": sym.upper(),
        "fetched_at": int(time.time()),
        "result": result if result is not None else existing.get("result"),
        "prices": price_series if price_series is not None else existing.get("prices", []),
        "trading": trading if trading is not None else existing.get("trading"),
    }
    _path(sym).write_text(json.dumps(payload))
    return payload


def save_trading(sym: str, trading: dict) -> dict:
    """Update just the trading slice of a snapshot, preserving analyzer data."""
    return save_snapshot(sym, result=None, price_series=None, trading=trading)


MACRO_PATH = SNAP_DIR / "_macro.json"


def save_macro(macro: dict) -> dict:
    payload = {"fetched_at": int(time.time()), "macro": macro}
    MACRO_PATH.write_text(json.dumps(payload))
    return payload


def load_macro() -> dict | None:
    if not MACRO_PATH.exists():
        return None
    try:
        return json.loads(MACRO_PATH.read_text())
    except Exception:
        return None


def age_str(snap: dict) -> str:
    if not snap or "fetched_at" not in snap:
        return "never"
    secs = int(time.time()) - snap["fetched_at"]
    if secs < 90:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h ago"
    return f"{hrs // 24}d ago"


def all_snapshots() -> dict[str, dict]:
    out = {}
    for p in SNAP_DIR.glob("*.json"):
        try:
            out[p.stem] = json.loads(p.read_text())
        except Exception:
            continue
    return out


def delete_snapshot(sym: str) -> None:
    p = _path(sym)
    if p.exists():
        p.unlink()
