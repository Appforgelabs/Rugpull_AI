"""
macro_engine.py — the regime layer. The "tide" under every single-stock move.

This is the closest thing in the system to a LEADING read, because most
single-name moves are beta: the stock goes where the index and the macro regime
go. Knowing the tide is more forward-useful than any price-derived oscillator on
one ticker.

Honest framing baked in:
  • Every signal is tagged leading / coincident / lagging so you stop mistaking
    a description of the past for a prediction of the future.
  • The regime produces a risk multiplier (risk-on >1, risk-off <1) that
    re-weights per-stock LONG conviction. In risk-off, a bullish stock signal
    is fighting the tide and gets discounted.
  • Nothing here predicts. It contextualizes. The yield curve "leads" recessions
    by quarters with huge error bars; VIX term structure "leads" equity stress
    by days at best. Leading ≠ certain.

Inputs pulled from FMP: treasury rates (10y/2y), SPY & QQQ history (trend +
your stock's relative strength), ^VIX (fear), and an equal-weight vs cap-weight
breadth proxy (RSP vs SPY).
"""

from __future__ import annotations
import numpy as np
import pandas as pd

import trade_signals as TS  # reuse to_ohlcv
import ta_engine as TA


# tag vocabulary
LEADING = "leading"
COINCIDENT = "coincident"
LAGGING = "lagging"


def _trend(closes: pd.Series) -> dict:
    """Simple trend read: price vs 50/200 SMA + 1-month slope."""
    if len(closes) < 200:
        return {"dir": 0, "note": "insufficient history"}
    last = float(closes.iloc[-1])
    sma50 = float(closes.rolling(50).mean().iloc[-1])
    sma200 = float(closes.rolling(200).mean().iloc[-1])
    r1m = last / float(closes.iloc[-21]) - 1 if len(closes) > 21 else 0
    score = (last > sma50) + (last > sma200) + (sma50 > sma200) + (r1m > 0)
    return {"dir": 1 if score >= 3 else -1 if score <= 1 else 0,
            "above_200": last > sma200, "r1m": round(r1m, 3),
            "last": round(last, 2)}


def build_macro(client) -> dict:
    """Assemble the regime snapshot. Degrades gracefully if any feed is missing."""
    signals = []   # each: {name, value, read, bias, lag}

    def add(name, value, read, bias, lag):
        signals.append({"name": name, "value": value, "read": read,
                        "bias": bias, "lag": lag})

    # ---- yield curve (LEADING) ----
    y10 = y2 = spread = None
    try:
        rates = client.fetch("treasury", "")
        latest = rates[0] if isinstance(rates, list) and rates else {}
        y10 = _f(latest.get("year10"))
        y2 = _f(latest.get("year2") or latest.get("month2"))
        if y10 is not None and y2 is not None:
            spread = round(y10 - y2, 2)
            if spread < 0:
                add("Yield curve 10y-2y", spread, "INVERTED", "risk-off", LEADING)
            elif spread < 0.5:
                add("Yield curve 10y-2y", spread, "FLAT", "neutral", LEADING)
            else:
                add("Yield curve 10y-2y", spread, "STEEP", "risk-on", LEADING)
    except Exception:
        pass

    # ---- SPY trend (COINCIDENT — the tide itself) ----
    spy_closes = _closes(client, "SPY")
    spy_trend = _trend(spy_closes) if spy_closes is not None else {"dir": 0}
    if spy_trend.get("dir"):
        add("S&P 500 (SPY) trend", spy_trend.get("last"),
            "UPTREND" if spy_trend["dir"] > 0 else "DOWNTREND",
            "risk-on" if spy_trend["dir"] > 0 else "risk-off", COINCIDENT)

    # ---- QQQ trend (COINCIDENT, growth/risk appetite) ----
    qqq_closes = _closes(client, "QQQ")
    qqq_trend = _trend(qqq_closes) if qqq_closes is not None else {"dir": 0}
    if qqq_trend.get("dir"):
        add("Nasdaq (QQQ) trend", qqq_trend.get("last"),
            "UPTREND" if qqq_trend["dir"] > 0 else "DOWNTREND",
            "risk-on" if qqq_trend["dir"] > 0 else "risk-off", COINCIDENT)

    # ---- VIX level + trend (LEADING-ish for stress) ----
    vix_closes = _closes(client, "^VIX")
    vix_level = None
    if vix_closes is not None and len(vix_closes) > 5:
        vix_level = round(float(vix_closes.iloc[-1]), 1)
        vix_5d = float(vix_closes.iloc[-6])
        rising = vix_level > vix_5d
        if vix_level >= 25:
            add("VIX (volatility)", vix_level, "ELEVATED FEAR", "risk-off", LEADING)
        elif vix_level <= 15:
            add("VIX (volatility)", vix_level, "COMPLACENT/CALM",
                "risk-on", LEADING)
        else:
            add("VIX (volatility)", vix_level,
                f"MODERATE {'rising' if rising else 'falling'}",
                "risk-off" if rising else "risk-on", LEADING)

    # ---- breadth proxy: RSP (equal-weight) vs SPY (cap-weight) (LEADING) ----
    rsp_closes = _closes(client, "RSP")
    if rsp_closes is not None and spy_closes is not None and \
            len(rsp_closes) > 21 and len(spy_closes) > 21:
        rsp_r = float(rsp_closes.iloc[-1] / rsp_closes.iloc[-21] - 1)
        spy_r = float(spy_closes.iloc[-1] / spy_closes.iloc[-21] - 1)
        diff = round((rsp_r - spy_r) * 100, 2)
        # equal-weight lagging cap-weight = narrow, fragile rally (bearish breadth)
        if diff < -1.5:
            add("Breadth (RSP vs SPY, 1m)", diff, "NARROW/FRAGILE",
                "risk-off", LEADING)
        elif diff > 1.0:
            add("Breadth (RSP vs SPY, 1m)", diff, "BROAD/HEALTHY",
                "risk-on", LEADING)
        else:
            add("Breadth (RSP vs SPY, 1m)", diff, "NEUTRAL", "neutral", LEADING)

    # ---- DXY proxy: UUP dollar ETF trend (COINCIDENT) ----
    uup_closes = _closes(client, "UUP")
    if uup_closes is not None and len(uup_closes) > 21:
        uup_r = float(uup_closes.iloc[-1] / uup_closes.iloc[-21] - 1)
        # strong rising dollar = headwind for risk assets
        if uup_r > 0.02:
            add("US Dollar (UUP, 1m)", round(uup_r, 3), "STRENGTHENING",
                "risk-off", COINCIDENT)
        elif uup_r < -0.02:
            add("US Dollar (UUP, 1m)", round(uup_r, 3), "WEAKENING",
                "risk-on", COINCIDENT)

    # ---- tally regime ----
    on = sum(1 for s in signals if s["bias"] == "risk-on")
    off = sum(1 for s in signals if s["bias"] == "risk-off")
    net = on - off
    total = on + off or 1

    if net >= 2:
        regime = "RISK-ON"
    elif net <= -2:
        regime = "RISK-OFF"
    else:
        regime = "MIXED / NEUTRAL"

    # multiplier from LEADING signals only (curve, VIX, breadth). SPY/QQQ trend
    # is excluded here because the stocks' own MA votes already embed the index
    # move — including it again would double-count the same information and
    # inflate conviction most at tops.
    lead_on = sum(1 for s in signals
                  if s["bias"] == "risk-on" and s["lag"] == LEADING)
    lead_off = sum(1 for s in signals
                   if s["bias"] == "risk-off" and s["lag"] == LEADING)
    lead_net = lead_on - lead_off
    if lead_net >= 1:
        mult = 1.12
    elif lead_net <= -1:
        mult = 0.85
    else:
        mult = 1.0

    return {
        "ok": bool(signals),
        "regime": regime, "risk_multiplier": mult,
        "risk_on_count": on, "risk_off_count": off, "net": net,
        "spread_10y_2y": spread, "y10": y10, "y2": y2, "vix": vix_level,
        "spy_dir": spy_trend.get("dir", 0), "qqq_dir": qqq_trend.get("dir", 0),
        "signals": signals,
        "note": "Regime sets the tide. risk_multiplier (from LEADING signals "
                "only — curve/VIX/breadth) re-weights single-stock conviction "
                "without double-counting the index trend already in each "
                "stock's MAs. Leading != certain.",
    }


def relative_strength(stock_closes: list, spy_closes_list: list,
                      lookback: int = 63) -> dict:
    """
    Is the stock outperforming SPY over the lookback? Positive RS that is rising
    is one of the few genuinely LEADING single-name tells — it can show
    institutional accumulation before it's obvious in the headline price.
    """
    if not stock_closes or not spy_closes_list:
        return {"ok": False}
    s = pd.Series(stock_closes)
    m = pd.Series(spy_closes_list)
    n = min(len(s), len(m), lookback + 1)
    if n < 21:
        return {"ok": False}
    s, m = s.iloc[-n:].reset_index(drop=True), m.iloc[-n:].reset_index(drop=True)
    stock_r = float(s.iloc[-1] / s.iloc[0] - 1)
    spy_r = float(m.iloc[-1] / m.iloc[0] - 1)
    rs = round((stock_r - spy_r) * 100, 2)
    # is RS line rising over last 21 bars?
    ratio = (s / m)
    rising = bool(ratio.iloc[-1] > ratio.iloc[-min(21, len(ratio))])
    return {"ok": True, "rs_vs_spy": rs, "rising": rising,
            "read": ("OUTPERFORMING" if rs > 0 else "LAGGING"),
            "lag": LEADING,
            "note": "RS rising + positive = stock stronger than market (early "
                    "accumulation tell). RS is leading-ish, not a guarantee."}


def apply_regime_to_conviction(base_prob: float, direction: str,
                               risk_multiplier: float) -> float:
    """
    Re-weight a per-stock conviction by regime. A LONG in risk-off gets
    discounted; a LONG in risk-on gets a modest boost. SHORT is the mirror.
    Still hard-capped at 72 (agreement is weak evidence, regime doesn't change
    that ceiling).
    """
    if direction == "NEUTRAL" or base_prob is None:
        return base_prob
    edge = base_prob - 50.0
    if direction == "LONG":
        edge *= risk_multiplier
    elif direction == "SHORT":
        edge *= (2 - risk_multiplier)  # risk-off boosts shorts
    return round(min(72.0, max(50.0, 50.0 + edge)), 0)


# ---- helpers ---------------------------------------------------------------
def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _closes(client, symbol):
    """Fetch a symbol's daily closes as a Series, or None on failure."""
    try:
        hist = client.history(symbol)
        df = TS.to_ohlcv(hist)
        return df["close"] if not df.empty else None
    except Exception:
        return None
