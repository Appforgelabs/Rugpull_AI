"""
prediction_tracker.py — the app grading itself.

Loop:
  1. RECORD  — each time data updates, log every ticker's calls for the day:
              overall direction, conviction, each trend vote, swing/day bias.
  2. SCORE   — once a prediction is >= `horizon_days` old and a newer snapshot
              price exists, mark each call right/wrong against the realized
              move. No extra API calls: scoring uses prices you already fetch.
  3. LEARN   — per-signal trailing hit-rates become vote WEIGHTS. Signals that
              have been right get louder; signals that have been wrong get
              quieter. Weights feed back into trade_signal() on the next update.

Ledger persists to the cloud (same Apps Script, key 'predictions') with a local
file fallback, so the learning survives reboots and follows you across machines.
"""

from __future__ import annotations
import json
import time
import datetime as dt
from pathlib import Path

LEDGER_KEY = "predictions"
LOCAL_PATH = Path(__file__).parent / "snapshots" / "_predictions.json"
HORIZON_DAYS = 7            # calendar days ≈ 5 trading days
MIN_N_FOR_WEIGHT = 8        # don't trust a hit-rate until this many scored calls
WEIGHT_FLOOR, WEIGHT_CEIL = 0.25, 1.75


# ---- persistence ------------------------------------------------------------
def load_ledger(cloud_url: str | None = None) -> dict:
    if cloud_url:
        try:
            import cloud_sync as CS
            blob = CS.load_blob(cloud_url, LEDGER_KEY)
            if blob:
                return blob
        except Exception:
            pass
    try:
        return json.loads(LOCAL_PATH.read_text())
    except Exception:
        return {"predictions": [], "stats": {}, "version": 1}


def save_ledger(ledger: dict, cloud_url: str | None = None) -> None:
    LOCAL_PATH.parent.mkdir(exist_ok=True)
    LOCAL_PATH.write_text(json.dumps(ledger))
    if cloud_url:
        try:
            import cloud_sync as CS
            CS.save_blob(cloud_url, LEDGER_KEY, ledger)
        except Exception:
            pass


# ---- record -----------------------------------------------------------------
def record(ledger: dict, sym: str, trading: dict) -> bool:
    """Log today's calls for one ticker. One record per ticker per day."""
    if not trading or not trading.get("ok"):
        return False
    sg = trading.get("signal") or {}
    today = dt.date.today().isoformat()
    for p in ledger["predictions"]:
        if p["symbol"] == sym and p["date"] == today:
            return False  # already recorded today
    votes = {v["signal"]: v["vote"]
             for v in sg.get("votes", []) if v.get("vote")}
    ledger["predictions"].append({
        "id": f"{sym}-{today}",
        "date": today, "ts": int(time.time()),
        "symbol": sym, "price": trading.get("price"),
        "direction": sg.get("direction"),
        "probability": sg.get("probability"),
        "votes": votes,
        "swing": (sg.get("swing") or {}).get("bias"),
        "day": (sg.get("day") or {}).get("bias"),
        "scored": False,
    })
    return True


# ---- score ------------------------------------------------------------------
def score_due(ledger: dict, latest_prices: dict[str, tuple[float, int]]) -> int:
    """Score predictions older than HORIZON_DAYS using newer snapshot prices.
    latest_prices: {SYM: (price, fetched_at_epoch)}. Returns # newly scored."""
    now = time.time()
    scored = 0
    stats = ledger.setdefault("stats", {})

    def bump(name, hit):
        s = stats.setdefault(name, {"n": 0, "hits": 0})
        s["n"] += 1
        s["hits"] += int(hit)

    for p in ledger["predictions"]:
        if p.get("scored"):
            continue
        if now - p["ts"] < HORIZON_DAYS * 86400:
            continue
        lp = latest_prices.get(p["symbol"])
        if not lp or not p.get("price"):
            continue
        new_price, fetched_at = lp
        if fetched_at - p["ts"] < HORIZON_DAYS * 86400 * 0.8:
            continue  # newer price isn't new enough yet
        ret = (new_price - p["price"]) / p["price"]
        up = ret > 0
        p["realized_ret_pct"] = round(ret * 100, 2)
        p["scored"] = True
        scored += 1

        if p.get("direction") in ("LONG", "SHORT"):
            hit = (p["direction"] == "LONG") == up
            p["correct"] = bool(hit)
            bump("OVERALL", hit)
        for name, vote in (p.get("votes") or {}).items():
            bump(name, (vote > 0) == up)
        for lane in ("swing", "day"):
            b = p.get(lane)
            if b in ("LONG", "SHORT"):
                bump(lane.upper(), (b == "LONG") == up)
    return scored


# ---- learn ------------------------------------------------------------------
def signal_weights(ledger: dict) -> dict[str, float]:
    """Per-signal vote weights from trailing hit-rates. 50% accuracy = weight
    1.0; better gets louder, worse gets quieter. Untested signals stay at 1.0."""
    weights = {}
    for name, s in (ledger.get("stats") or {}).items():
        if s["n"] < MIN_N_FOR_WEIGHT:
            continue
        hr = s["hits"] / s["n"]
        w = max(WEIGHT_FLOOR, min(WEIGHT_CEIL, 2 * hr))
        weights[name] = round(w, 2)
    return weights


def stats_table(ledger: dict) -> list[dict]:
    rows = []
    for name, s in sorted((ledger.get("stats") or {}).items()):
        hr = s["hits"] / s["n"] * 100 if s["n"] else 0
        rows.append({"Signal": name, "Scored": s["n"],
                     "Hit rate": f"{hr:.0f}%",
                     "Weight": signal_weights(ledger).get(name, 1.0)})
    return rows


# ---- the automatic cycle ------------------------------------------------------
def auto_cycle(syms: list[str], load_snapshot, cloud_url: str | None = None) -> dict:
    """Run after every data update: score what's due, record today's calls,
    persist. Returns a summary for the UI."""
    ledger = load_ledger(cloud_url)
    latest = {}
    trading_by_sym = {}
    for s in syms:
        snap = load_snapshot(s) or {}
        tr = snap.get("trading")
        if tr and tr.get("price"):
            latest[s] = (tr["price"], snap.get("fetched_at", 0))
            trading_by_sym[s] = tr
        elif (snap.get("result") or {}).get("price"):
            latest[s] = (snap["result"]["price"], snap.get("fetched_at", 0))

    n_scored = score_due(ledger, latest)
    n_recorded = sum(1 for s, tr in trading_by_sym.items()
                     if record(ledger, s, tr))
    save_ledger(ledger, cloud_url)
    pend = sum(1 for p in ledger["predictions"] if not p.get("scored"))
    return {"recorded": n_recorded, "scored": n_scored, "pending": pend,
            "total": len(ledger["predictions"]),
            "weights": signal_weights(ledger)}
