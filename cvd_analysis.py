"""
cvd_analysis.py — intraday Cumulative Volume Delta (CVD) from FMP bars.

TRUE CVD needs tick-level aggressor data (which side of the bid/ask each trade
hit). FMP's intraday chart endpoint gives OHLCV bars, so per-bar delta is
APPROXIMATED — the same honest proxy this repo already uses in
volume_profile.py:

    delta = volume × (2·(close−low)/(high−low) − 1)        ("location")

close near the bar's high ⇒ buyers controlled the bar (+volume); near the low
⇒ sellers (−volume); dead mid-bar ⇒ ~0. A second method signs the WHOLE bar by
direction:

    delta = +volume if close ≥ open else −volume           ("direction")

"location" is the default: it weights conviction by where price settled inside
the range instead of calling every up-bar 100% buying.

What you get from one fetch:
  • cumulative CVD across the whole fetched window (multi-day)
  • session-anchored CVD (resets at each day's first bar — the intraday
    standard, so overnight positioning doesn't smear today's read)
  • price↔CVD divergence flags (transparent two-half comparison, rule-based)
  • per-session flow table + the biggest single-bar delta prints
  • a small synthesis of reads — labeled as reads, NOT probabilities

INTERVAL / PLAN NOTE: FMP gates intraday intervals by plan. If your plan lacks
1-minute bars, just pick 5min+ — fetch_cvd() also CASCADES automatically to a
coarser interval when the requested one errors or returns nothing, and reports
which interval was actually used.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

import trade_signals as TS          # to_ohlcv() — FMP bars -> tidy frame
import volume_profile as VP         # bar_delta() — the repo's one delta proxy

# Coarser and coarser fallbacks when a plan gate or an empty response hits.
FALLBACK_ORDER = ["1min", "5min", "15min", "30min", "1hour", "4hour"]

DELTA_METHODS = {
    "location":  "Close-location (close's position inside high–low × volume)",
    "direction": "Bar direction (full volume signed by close vs open)",
}

# reads tuning
_SLOPE_BARS = 40        # bars in the trailing CVD-slope window
_SLOPE_THRESH = 0.15    # |slope| in avg-bar-volumes per bar to call a side
_DIVERG_BARS = 60       # bars in the divergence lookback (split into halves)


# ---- per-bar delta ----------------------------------------------------------
def bar_delta_direction(df: pd.DataFrame) -> pd.Series:
    """Sign the whole bar by close vs open. Cruder, but easy to reason about."""
    sign = np.where(df["close"] >= df["open"], 1.0, -1.0)
    return df["volume"] * sign


def compute(df: pd.DataFrame, method: str = "location") -> dict:
    """All CVD math on a tidy intraday OHLCV frame (oldest first)."""
    if df is None or df.empty or len(df) < 10 or "volume" not in df:
        return {"ok": False, "note": "need >=10 intraday bars with volume"}

    d = df.dropna(subset=["close", "volume"]).reset_index(drop=True)
    d = d[d["volume"] > 0].reset_index(drop=True)
    if len(d) < 10:
        return {"ok": False, "note": "too few bars with non-zero volume"}

    if method == "direction":
        d["delta"] = bar_delta_direction(d)
    else:
        method = "location"
        d["delta"] = VP.bar_delta(d)

    d["cvd"] = d["delta"].cumsum()                          # multi-day cumulative
    d["sess"] = d["date"].dt.strftime("%Y-%m-%d")
    # session-anchored: cumsum restarts at each day's first bar
    d["cvds"] = d.groupby("sess")["delta"].cumsum()

    pts = [{
        "t": t.strftime("%Y-%m-%d %H:%M"),
        "c": round(float(c), 4),
        "d": int(round(de)),
        "cv": int(round(cv)),
        "cs": int(round(cs)),
        "s": s,
    } for t, c, de, cv, cs, s in zip(
        d["date"], d["close"], d["delta"], d["cvd"], d["cvds"], d["sess"])]

    if len(pts) > 1600:
        stride = -(-len(pts) // 1600)           # ceil division
        pts = pts[::stride][:-1] + [pts[-1]]     # keep the latest point exact

    out = {
        "ok": True,
        "method": method,
        "bars": len(d),
        "first": pts[0]["t"], "last": pts[-1]["t"],
        "points": pts,
        "sessions": _sessions(d),
        "stats": _stats(d),
        "slope": _slope_read(d),
        "divergence": _divergence(d),
    }
    out["synthesis"] = _synthesis(out)
    return out


# ---- pieces -----------------------------------------------------------------
def _sessions(d: pd.DataFrame) -> list:
    """One row per trading day: net flow, session CVD close, price move."""
    rows = []
    for s, g in d.groupby("sess"):
        net = float(g["delta"].sum())
        p0, p1 = float(g["close"].iloc[0]), float(g["close"].iloc[-1])
        rows.append({
            "date": s, "bars": int(len(g)),
            "net_delta": int(round(net)),
            "cvd_close": int(round(float(g["cvds"].iloc[-1]))),
            "price_chg_pct": round((p1 - p0) / p0 * 100, 2) if p0 else None,
            "agree": bool((net >= 0) == (p1 >= p0)),
        })
    return rows


def _stats(d: pd.DataFrame) -> dict:
    pos = d.loc[d["delta"] > 0, "delta"].sum()
    neg = -d.loc[d["delta"] < 0, "delta"].sum()
    tot = pos + neg
    p0, p1 = float(d["close"].iloc[0]), float(d["close"].iloc[-1])
    big = d.reindex(d["delta"].abs().sort_values(ascending=False).index)[:5]
    return {
        "net_delta": int(round(float(d["delta"].sum()))),
        "buy_vol": int(round(float(pos))),
        "sell_vol": int(round(float(neg))),
        "buy_share": round(float(pos) / float(tot), 3) if tot else None,
        "price_chg_pct": round((p1 - p0) / p0 * 100, 2) if p0 else None,
        "avg_bar_vol": int(round(float(d["volume"].mean()))),
        "top_bars": [{
            "t": r["date"].strftime("%Y-%m-%d %H:%M"),
            "delta": int(round(float(r["delta"]))),
            "close": round(float(r["close"]), 2),
            "side": "buy" if r["delta"] >= 0 else "sell",
        } for _, r in big.iterrows()],
    }


def _slope_read(d: pd.DataFrame, bars: int = _SLOPE_BARS) -> dict:
    """Trailing CVD slope, normalized by average bar volume so the threshold
    means something across symbols. + = net accumulation pressure."""
    g = d.tail(bars)
    if len(g) < 10:
        return {"ok": False}
    x = np.arange(len(g))
    slope = float(np.polyfit(x, g["cvd"].values, 1)[0])
    avgv = float(g["volume"].mean()) or 1.0
    norm = slope / avgv
    read = ("ACCUMULATING" if norm >= _SLOPE_THRESH
            else "DISTRIBUTING" if norm <= -_SLOPE_THRESH else "NEUTRAL")
    return {"ok": True, "bars": int(len(g)), "norm": round(norm, 3),
            "per_bar": int(round(slope)), "read": read}


def _divergence(d: pd.DataFrame, lookback: int = 60) -> dict:
    """Price/flow divergence, measured AT the price extremes (the classic
    construction): a new price high whose cumulative CVD is materially LOWER
    than at the prior high = bearish (the push wasn't paid for with real
    buying); mirrored for lows. Comparing window-half maxima — as naive
    implementations do — fails on cumulative series, because the later half
    inherits the earlier half's level."""
    g = d.tail(lookback).reset_index(drop=True)
    if len(g) < 20:
        return {"ok": False, "kind": "none", "note": "insufficient bars"}
    h = len(g) // 2
    a, b = g.iloc[:h], g.iloc[h:]

    eps = 0.0015
    c_eps = 2.0 * float(g["volume"].mean() or 1.0)

    i_hi1, i_hi2 = a["high"].idxmax(), b["high"].idxmax()
    i_lo1, i_lo2 = a["low"].idxmin(), b["low"].idxmin()
    p_hi1, p_hi2 = float(g["high"][i_hi1]), float(g["high"][i_hi2])
    p_lo1, p_lo2 = float(g["low"][i_lo1]), float(g["low"][i_lo2])
    c_at_hi1, c_at_hi2 = float(g["cvd"][i_hi1]), float(g["cvd"][i_hi2])
    c_at_lo1, c_at_lo2 = float(g["cvd"][i_lo1]), float(g["cvd"][i_lo2])

    bearish = (p_hi2 > p_hi1 * (1 + eps)) and (c_at_hi2 < c_at_hi1 - c_eps)
    bullish = (p_lo2 < p_lo1 * (1 - eps)) and (c_at_lo2 > c_at_lo1 + c_eps)

    if bearish and not bullish:
        return {"ok": True, "kind": "bearish",
                "note": (f"Price made a higher high "
                         f"({p_hi1:.2f} → {p_hi2:.2f}) but cumulative flow at "
                         f"the new high was LOWER — the push wasn't paid for "
                         f"with net buying. Classic warning, not a signal.")}
    if bullish and not bearish:
        return {"ok": True, "kind": "bullish",
                "note": (f"Price made a lower low "
                         f"({p_lo1:.2f} → {p_lo2:.2f}) but cumulative flow at "
                         f"the new low was HIGHER — selling pressure faded "
                         f"into the flush. Constructive, not a signal.")}
    return {"ok": True, "kind": "none",
            "note": "No divergence — flow confirmed the price extremes over "
                    "this window."}


def _synthesis(out: dict) -> list:
    """Plain-English reads. Described as evidence, never as a win-rate."""
    reads = []
    st, sl, dv = out["stats"], out["slope"], out["divergence"]

    if st.get("buy_share") is not None:
        pct = f"{st['buy_share']*100:.0f}%"
        if st["buy_share"] >= 0.55:
            reads.append(f"Over the whole window, buyers controlled the flow "
                         f"({pct} of signed volume on the buy side).")
        elif st["buy_share"] <= 0.45:
            reads.append(f"Over the whole window, sellers controlled the flow "
                         f"({100 - st['buy_share']*100:.0f}% of signed volume "
                         f"on the sell side).")
        else:
            reads.append(f"Over the whole window, flow was balanced "
                         f"({pct} buy-side — no clean control either way).")

    if sl.get("ok"):
        reads.append(f"Recent CVD trend ({sl['bars']} bars): "
                     f"{sl['read']} — net {sl['per_bar']:+,} shares of delta "
                     f"per bar ({sl['norm']:+.2f}× an average bar's volume).")

    if dv.get("ok") and dv["kind"] != "none":
        reads.append("⚠ Divergence: " + dv["note"])

    chg, net = st.get("price_chg_pct"), st.get("net_delta")
    if chg is not None and net is not None:
        agree = (chg >= 0) == (net >= 0)
        verdict = ("confirm each other" if agree else
                   "DISAGREE — the more interesting case: someone is "
                   "absorbing the other side")
        reads.append(
            f"Price {'rose' if chg >= 0 else 'fell'} {abs(chg):.2f}% while net "
            f"delta was {'positive' if net >= 0 else 'negative'} — flow and "
            f"price {verdict}.")
    return reads


# ---- network ----------------------------------------------------------------
def fetch_cvd(client, sym: str, interval: str = "5min", days_back: int = 5,
              method: str = "location") -> dict:
    """Fetch intraday bars and compute CVD. If the requested interval errors
    (plan gate, moved endpoint) or returns no bars, cascade down FALLBACK_ORDER
    to a coarser interval and say so in the result."""
    interval = interval if interval in FALLBACK_ORDER else "5min"
    start = FALLBACK_ORDER.index(interval)
    tried, last_err = [], None
    # minimum sensible window per interval, so a fallback to coarse bars
    # still yields enough bars to compute on
    _min_days = {"1min": 2, "5min": 5, "15min": 10, "30min": 15,
                 "1hour": 30, "4hour": 60}

    for iv in FALLBACK_ORDER[start:]:
        tried.append(iv)
        try:
            _days = max(days_back, _min_days.get(iv, days_back))
            bars = client.intraday(sym, interval=iv, days_back=_days)
            df = TS.to_ohlcv(bars)
            if df.empty:
                last_err = "no bars returned"
                continue
            out = compute(df, method=method)
            if not out.get("ok"):
                last_err = out.get("note", "compute failed")
                continue
            out.update({
                "symbol": sym.upper(),
                "interval_requested": interval,
                "interval_used": iv,
                "days_back": _days,
                "fetched_at": pd.Timestamp.now(tz="UTC").strftime(
                    "%Y-%m-%d %H:%M UTC"),
                "fallback": iv != interval,
            })
            return out
        except Exception as e:                      # FMPError, network, etc.
            last_err = f"{type(e).__name__}: {e}"
            continue

    return {"ok": False, "symbol": sym.upper(),
            "interval_requested": interval, "tried": tried,
            "note": f"no usable intraday data (last: {last_err}). If your FMP "
                    f"plan lacks this interval, the cascade already tried the "
                    f"coarser ones."}
