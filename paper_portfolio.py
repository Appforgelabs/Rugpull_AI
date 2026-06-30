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

PAPER_KEY = "paper"
START_CASH = 100_000.0
N_HOLD = 12
DROP_RANK = 15          # hysteresis: hold until it falls past this rank
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
    p["activity"].append({
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


def _due_for_rebalance(p) -> bool:
    if not p["last_rebalance"]:
        return True
    last = dt.date.fromisoformat(p["last_rebalance"])
    return (dt.date.today() - last).days >= p["settings"]["rebalance_days"]


def rebalance(p, ranked_syms: list[str], prices: dict, force=False) -> dict:
    """ranked_syms: tickers ordered best->worst by adjusted upside (from the
    Research screener). prices: {sym: live price}. Returns a summary."""
    if not force and not _due_for_rebalance(p):
        return {"acted": False, "reason": "not due (weekly cadence)"}
    if not ranked_syms or not prices:
        return {"acted": False, "reason": "no rankings/prices available"}

    held = set(p["positions"].keys())
    rank = {s: i + 1 for i, s in enumerate(ranked_syms)}
    top_n = [s for s in ranked_syms if s in prices][:N_HOLD]

    # 1) SELL holdings that fell past the hysteresis buffer OR lost their price
    for sym in list(held):
        r = rank.get(sym, 9999)
        if r > DROP_RANK or sym not in prices:
            pos = p["positions"].pop(sym)
            px = prices.get(sym) or pos["avg_cost"]
            proceeds = pos["shares"] * px * (1 - COMMISSION)
            p["cash"] += proceeds
            _log(p, "SELL", sym, pos["shares"], px,
                 f"dropped to rank {r} (>{DROP_RANK})")

    # 2) figure the target roster: keep held names still in top DROP_RANK,
    #    fill open slots from the top_n list
    keep = [s for s in p["positions"] if rank.get(s, 9999) <= DROP_RANK]
    open_slots = N_HOLD - len(keep)
    additions = [s for s in top_n if s not in keep][:open_slots]
    roster = keep + additions

    # 3) equal-weight target value per name (based on total equity)
    total_equity = portfolio_value(p, prices)
    target_each = total_equity / max(len(roster), 1)

    # 4) buy new names; trim/add existing to equal weight
    for sym in roster:
        px = prices.get(sym)
        if not px:
            continue
        cur_shares = p["positions"].get(sym, {}).get("shares", 0.0)
        cur_val = cur_shares * px
        delta_val = target_each - cur_val
        if abs(delta_val) < target_each * 0.02:   # within 2% — leave it
            continue
        delta_shares = delta_val / px
        if delta_shares > 0:                        # buy/add
            cost = delta_shares * px * (1 + COMMISSION)
            if cost > p["cash"]:                    # cap at available cash
                delta_shares = (p["cash"] / (px * (1 + COMMISSION)))
                cost = p["cash"]
            p["cash"] -= cost
            if sym in p["positions"]:
                pos = p["positions"][sym]
                tot = pos["shares"] + delta_shares
                pos["avg_cost"] = ((pos["avg_cost"] * pos["shares"]
                                    + px * delta_shares) / tot) if tot else px
                pos["shares"] = tot
                _log(p, "ADD", sym, delta_shares, px, "rebalance to equal weight")
            else:
                p["positions"][sym] = {"shares": delta_shares, "avg_cost": px}
                _log(p, "BUY", sym, delta_shares, px,
                     f"entered top {N_HOLD} (rank {rank.get(sym)})")
        else:                                       # trim
            sh = min(-delta_shares, p["positions"][sym]["shares"])
            p["positions"][sym]["shares"] -= sh
            p["cash"] += sh * px * (1 - COMMISSION)
            _log(p, "TRIM", sym, sh, px, "rebalance to equal weight")

    p["last_rebalance"] = dt.date.today().isoformat()

    # anchor SPY benchmark to the same net invested dollars on first rebalance
    spy_px = prices.get("SPY")
    if spy_px and p["spy_anchor"] is None:
        invested = total_equity
        p["spy_units"] = invested / spy_px
        p["spy_anchor"] = spy_px

    return {"acted": True, "roster": roster, "cash": round(p["cash"], 2),
            "target_each": round(target_each, 2)}


def snapshot_value(p, prices: dict) -> None:
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
        # SPY equivalent started at the same invested dollars (≈START_CASH)
        spy_ret = (spy_now / START_CASH - 1) * 100

    def window_ret(days):
        if len(p["history"]) < 2:
            return None
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        past = [h for h in p["history"] if h["date"] <= cutoff]
        base = past[-1] if past else p["history"][0]
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
