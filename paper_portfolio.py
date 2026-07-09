"""
paper_portfolio.py — automated 12-ticker equal-weight paper portfolio.

Strategy (fixed, measurable):
  • Hold the top 12 names by adjusted-upside rank from the Research screener.
  • Equal weight (1/12 each).
  • Rebalance on a WEEKLY cadence — not every update — to avoid churning on
    noise. A hysteresis buffer protects held names: a current holding is only
    dropped if it falls below rank `DROP_RANK` (e.g. 15), not the instant it
    slips past #12. New names enter only if inside the top 12 AND a slot is open.
  • Daily mark-to-market value snapshot, benchmarked against SPY bought with the
    same dollars on the same dates (honest, dollar-matched comparison).

Every action (BUY/SELL/TRIM/ADD/REBALANCE) is logged to an activity feed.
State persists to the Sheet via Apps Script (key 'paper'), so reboots don't
wipe it — the same proven path the watchlist uses.

Nothing here is advice. It's a measurement instrument: does an equal-weight
basket of the app's top-ranked names actually beat SPY over time?
"""

from __future__ import annotations
import datetime as dt
import time

PAPER_KEY = "paper"
START_CASH = 100_000.0
N_HOLD = 12
DROP_RANK = 20          # leadership boundary (used WITH trend loss, not alone)
TARGET_N = 12           # target number of holdings
REBALANCE_DAYS = 7      # weekly roster + equal-weight reset
COMMISSION = 0.0005     # 5 bps per side, so paper P&L isn't artificially clean


def new_portfolio() -> dict:
    return {
        "version": 1,
        "created": dt.date.today().isoformat(),
        "cash": START_CASH,
        "positions": {},          # sym -> {shares, avg_cost}
        "history": [],            # [{date, value, spy_value}]
        "activity": [],           # [{date, action, symbol, shares, price, note}]
        "last_rebalance": None,
        "spy_anchor": None,       # SPY price when portfolio's SPY-equivalent began
        "spy_units": 0.0,         # SPY shares the same net cash would have bought
        "settings": {"n_hold": N_HOLD, "equal_weight": True,
                     "rebalance_days": REBALANCE_DAYS},
    }


def _log(p, action, symbol, shares, price, note=""):
    p.setdefault("activity", []).append({
        "date": dt.date.today().isoformat(),
        "action": action, "symbol": symbol,
        "shares": round(shares, 3) if shares else None,
        "price": round(price, 2) if price else None, "note": note,
    })


def portfolio_value(p, prices: dict) -> float:
    v = p["cash"]
    for sym, pos in p["positions"].items():
        px = prices.get(sym)
        if px:
            v += pos["shares"] * px
    return round(v, 2)


def _breakdown(sym: str, snap: dict | None, rank: dict) -> str | None:
    """Has THIS holding's thesis broken? Returns the reason, or None if the
    conviction stands. Mild rank shuffling alone is NOT a breakdown."""
    if not snap:
        return "no data visibility (removed/paused in the app)"
    trading = snap.get("trading") or {}
    result = snap.get("result") or {}
    fetched = trading.get("fetched_at") or result.get("fetched_at") or 0
    if fetched and (time.time() - fetched) > 7 * 86400:
        return "data stale >7 days — no visibility, no conviction"
    sg = trading.get("signal") or {}
    swing = (sg.get("swing") or {}).get("bias")
    prob = sg.get("probability") or 50
    if swing == "SHORT" and prob >= 60:
        return f"trend broke against it (SHORT @ {prob}% agreement)"
    comp = result.get("composite_score")
    if comp is not None and comp < 35:
        return f"quality collapsed (composite {comp})"
    sent = (result.get("sentiment") or {}).get("score")
    if sent is not None and sent < 30 and swing != "LONG":
        return f"sentiment collapsed ({sent:.0f}) with no trend support"
    r = rank.get(sym, 9999)
    if r > DROP_RANK and swing != "LONG":
        return f"fell out of leadership (rank {r}) and lost its trend"
    return None


def decide(p, ranked_syms: list[str], snaps: dict, macro: dict | None,
           prices: dict, force=False) -> dict:
    """Conviction-driven decisions — no calendar. Acts only when the data
    changes: per-holding thesis breakdowns, macro regime shifts, or open
    capacity with qualified candidates. Otherwise SITS ON HANDS (and logs it).
    Exited names have a re-entry cooldown so the engine can't churn."""
    today = dt.date.today().isoformat()
    rank = {s: i + 1 for i, s in enumerate(ranked_syms)}
    mult = (macro or {}).get("risk_multiplier", 1.0)
    regime = (macro or {}).get("regime", "?")
    prev_mult = p.get("last_risk_mult")
    acted = False
    decisions = []

    def _note(kind, sym, why):
        decisions.append({"date": today, "kind": kind, "symbol": sym, "why": why})

    cooldown = p.setdefault("cooldown", {})     # sym -> exit date
    def _cooling(sym):
        d0 = cooldown.get(sym)
        return bool(d0) and (dt.date.fromisoformat(today)
                             - dt.date.fromisoformat(d0)).days < 5

    # ---- 1) per-holding thesis check (incl. removed/paused tickers) ----
    for sym in list(p["positions"]):
        why = _breakdown(sym, snaps.get(sym), rank)
        if why:
            pos = p["positions"][sym]
            px = prices.get(sym) or pos.get("last_price") or pos["avg_cost"]
            proceeds = pos["shares"] * px * (1 - COMMISSION)
            p["cash"] += proceeds
            _log(p, "SELL", sym, pos["shares"], px, why)
            _note("EXIT", sym, why)
            cooldown[sym] = today
            del p["positions"][sym]
            acted = True

    # ---- 2) macro regime shift ----
    if prev_mult is not None and mult < 1.0 <= prev_mult and p["positions"]:
        # deterioration: trim the two weakest holdings, hold the cash
        def _strength(sym):
            r0 = (snaps.get(sym) or {}).get("result") or {}
            return r0.get("composite_score") or 0
        weakest = sorted(p["positions"], key=_strength)[:2]
        for sym in weakest:
            pos = p["positions"][sym]
            px = prices.get(sym) or pos.get("last_price") or pos["avg_cost"]
            p["cash"] += pos["shares"] * px * (1 - COMMISSION)
            _log(p, "SELL", sym, pos["shares"], px,
                 f"macro risk-off trim ({regime})")
            _note("TRIM", sym, f"macro turned risk-off ({regime}) — raising cash")
            cooldown[sym] = today
            del p["positions"][sym]
            acted = True
    p["last_risk_mult"] = mult

    # ---- 3) deploy capacity into qualified candidates (risk-on/neutral) ----
    open_slots = TARGET_N - len(p["positions"])
    if open_slots > 0 and (mult >= 1.0 or force):
        cands = []
        for sym in ranked_syms:
            if sym in p["positions"] or _cooling(sym) or sym not in prices:
                continue
            snap = snaps.get(sym) or {}
            res = snap.get("result") or {}
            sg = ((snap.get("trading") or {}).get("signal") or {})
            comp = res.get("composite_score") or 0
            swing = (sg.get("swing") or {}).get("bias")
            sent = (res.get("sentiment") or {}).get("score")
            if comp >= 45 and swing == "LONG" and (sent is None or sent >= 40):
                cands.append(sym)
            if len(cands) >= open_slots:
                break
        if cands:
            per = (p["cash"] * 0.98) / max(len(cands), 1)
            for sym in cands:
                px = prices[sym]
                shares = (per * (1 - COMMISSION)) / px
                if shares * px < 100:      # ignore dust
                    continue
                p["cash"] -= shares * px * (1 + COMMISSION)
                pos = p["positions"].setdefault(
                    sym, {"shares": 0.0, "avg_cost": px})
                pos["shares"] += shares
                pos["avg_cost"] = px
                pos["entry_date"] = today
                _log(p, "BUY", sym, shares, px,
                     f"qualified entry (rank {rank.get(sym)}, "
                     f"regime {regime})")
                _note("BUY", sym, f"rank {rank.get(sym)}, LONG trend, "
                                  f"quality ok — deploying open capacity")
                acted = True

    if not acted:
        _note("HOLD", "—", f"no thesis broke, regime steady ({regime}) — "
                           f"sitting on hands")

    dl = p.setdefault("decisions", [])
    dl.extend(decisions)
    del dl[:-120]                       # keep the last ~120 decisions
    p["last_decide"] = today
    return {"acted": acted, "decisions": decisions,
            "roster": list(p["positions"])}


def rebalance(p, ranked_syms, prices, force=False):
    """Backward-compat shim for older callers: conviction engine without
    snapshots/macro degrades to visibility+rank checks only."""
    return decide(p, ranked_syms, {}, None, prices, force=force)


def snapshot_value(p, prices: dict) -> None:
    for _s, _pos in p.get("positions", {}).items():
        if prices.get(_s):
            _pos["last_price"] = prices[_s]
    """Append today's mark-to-market value + the dollar-matched SPY value."""
    today = dt.date.today().isoformat()
    val = portfolio_value(p, prices)
    spy_val = None
    spy_px = prices.get("SPY")
    if spy_px and p["spy_units"]:
        spy_val = round(p["spy_units"] * spy_px, 2)
    # one row per day (overwrite same-day)
    p["history"] = [h for h in p["history"] if h["date"] != today]
    p["history"].append({"date": today, "value": val, "spy_value": spy_val})
    p["history"].sort(key=lambda h: h["date"])


def performance(p, prices: dict) -> dict:
    """Return total return, vs-SPY, and windowed returns for the chart."""
    val = portfolio_value(p, prices)
    total_ret = (val / START_CASH - 1) * 100
    spy_ret = None
    spy_px = prices.get("SPY")
    if spy_px and p["spy_units"]:
        spy_now = p["spy_units"] * spy_px
        spy_basis = p.get("spy_basis") or START_CASH
        spy_ret = (spy_now / spy_basis - 1) * 100

    def window_ret(days):
        if len(p["history"]) < 2:
            return None
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        past = [h for h in p["history"] if h["date"] <= cutoff]
        if not past:
            return None   # not enough history for this window — show "—",
                          # never silently substitute since-inception
        base = past[-1]
        if not base["value"]:
            return None
        return round((val / base["value"] - 1) * 100, 2)

    return {
        "value": val, "total_return_pct": round(total_ret, 2),
        "spy_return_pct": round(spy_ret, 2) if spy_ret is not None else None,
        "vs_spy": round(total_ret - spy_ret, 2) if spy_ret is not None else None,
        "ret_7d": window_ret(7), "ret_30d": window_ret(30),
        "ret_ytd": window_ret((dt.date.today()
                               - dt.date(dt.date.today().year, 1, 1)).days),
        "ret_1y": window_ret(365),
        "positions": len(p["positions"]),
        "cash_pct": round(p["cash"] / val * 100, 1) if val else 100,
    }


# ---- persistence (same proven path as the watchlist) -----------------------
def load_portfolio(cloud_url: str | None):
    """Returns (portfolio_or_None, status). status is 'loaded', 'empty'
    (cloud reachable but nothing saved), or 'error' (load FAILED — caller must
    NOT auto-save over the cloud, or it could wipe good data)."""
    if not cloud_url:
        return None, "error"
    try:
        import cloud_sync as CS
        blob = CS.load_blob(cloud_url, PAPER_KEY)
    except Exception:
        return None, "error"        # transient failure — do NOT overwrite
    if blob and blob.get("version"):
        return blob, "loaded"
    return None, "empty"            # genuinely nothing saved yet


def save_portfolio(p, cloud_url: str | None) -> dict:
    status = {"cloud": False, "error": None}
    if cloud_url:
        try:
            import cloud_sync as CS
            CS.save_blob(cloud_url, PAPER_KEY, p)
            status["cloud"] = True
        except Exception as e:
            status["error"] = f"CLOUD SAVE FAILED: {e}"
    else:
        status["error"] = "no cloud URL"
    return status
