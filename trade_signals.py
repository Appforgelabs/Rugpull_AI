"""
trade_signals.py — multi-timeframe technicals + a transparent long/short score.

build_trading_row() assembles everything the Trading tab shows for one ticker:
  • RSI across 1-day (intraday), daily, weekly, monthly, quarterly bars
  • session VWAP (intraday) + rolling VWAP
  • SMA 20/50/200/325, MACD, ATR, Bollinger, ADX, Stochastic, OBV, CCI, W%R
  • pivot points + Fibonacci levels
  • a rules-based long/short bias with an HONEST probability estimate

On the probability: it is NOT a backtested win-rate (that comes later). It is a
rules score — how many independent signals align long vs short — mapped to a
calibrated-ish probability band. Treat it as "how much do these indicators
agree," not "odds this trade wins." The structure leaves room to swap in a real
backtested hit-rate once trade_backtest is built.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

import ta_engine as TA


# ---- frame helpers ---------------------------------------------------------
def to_ohlcv(bars: list) -> pd.DataFrame:
    """FMP bars (intraday array, daily array, or {'historical':[...]}) -> tidy
    OHLCV, oldest first."""
    if isinstance(bars, dict) and "historical" in bars:
        bars = bars["historical"]
    if not bars or not isinstance(bars, list):
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    if "date" not in df:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample daily bars to W/M/Q for higher-timeframe RSI."""
    if df.empty:
        return df
    d = df.set_index("date")
    out = d.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                "close": "last", "volume": "sum"}).dropna()
    return out.reset_index()


# ---- the assembled row -----------------------------------------------------
def build_trading_row(daily_df: pd.DataFrame, intraday_df: pd.DataFrame | None,
                      intraday_session_df: pd.DataFrame | None) -> dict:
    """daily_df: ~2y daily bars. intraday_df: recent intraday (for 1-day RSI).
    intraday_session_df: just today's intraday bars (for session VWAP)."""
    if daily_df.empty:
        return {"ok": False}

    c = daily_df["close"]
    price = float(c.iloc[-1])

    wk = resample(daily_df, "W")
    mo = resample(daily_df, "ME")
    qt = resample(daily_df, "QE")

    rsi_intraday = (TA.rsi(intraday_df["close"]) if intraday_df is not None
                    and not intraday_df.empty else float("nan"))

    row = {
        "ok": True, "price": round(price, 2),
        # multi-timeframe RSI
        "rsi_1d": _r(rsi_intraday), "rsi_D": _r(TA.rsi(c)),
        "rsi_W": _r(TA.rsi(wk["close"])) if not wk.empty else None,
        "rsi_M": _r(TA.rsi(mo["close"])) if not mo.empty else None,
        "rsi_Q": _r(TA.rsi(qt["close"])) if not qt.empty else None,
        # VWAP
        "vwap_session": _r(TA.session_vwap(intraday_session_df)
                           if intraday_session_df is not None else float("nan")),
        "vwap_roll20": _r(TA.rolling_vwap(daily_df, 20)),
        # moving averages
        "sma20": _r(TA.sma(c, 20)), "sma50": _r(TA.sma(c, 50)),
        "sma200": _r(TA.sma(c, 200)), "sma325": _r(TA.sma(c, 325)),
        # macd / atr / bbands / adx
        "macd": {k: _r(v) for k, v in TA.macd(c).items()},
        "atr14": _r(TA.atr(daily_df, 14)),
        "bb": {k: _r(v) for k, v in TA.bollinger(c).items()},
        "adx": _r(TA.adx(daily_df, 14)),
        # oscillators
        "stoch": {k: _r(v) for k, v in TA.stochastic(daily_df).items()},
        "obv": {k: _r(v) for k, v in TA.obv(daily_df).items()},
        "cci": _r(TA.cci(daily_df)),
        "williams_r": _r(TA.williams_r(daily_df)),
        # levels
        "pivots": TA.pivot_points(daily_df),
        "fib": TA.fib_levels(daily_df),
    }
    row["signal"] = trade_signal(row)
    return row


# ---- the transparent rules score ------------------------------------------
def trade_signal(r: dict) -> dict:
    """
    Tally independent bull/bear votes across momentum, trend, and oscillators.
    Returns direction, the vote split, and an HONEST probability band.

    Each check appends +1 (bullish), -1 (bearish), or 0 (neutral). The net is
    mapped to a probability that leans away from 50% as agreement grows — but
    capped well short of certainty, because indicator agreement is weak evidence.
    """
    votes = []  # (name, vote, note)
    price = r["price"]

    def add(name, vote, note):
        votes.append({"signal": name, "vote": vote, "note": note})

    # --- trend: price vs MAs (the backbone for long/short bias) ---
    for ma_key, label in [("sma20", "SMA20"), ("sma50", "SMA50"),
                          ("sma200", "SMA200"), ("sma325", "SMA325")]:
        ma = r.get(ma_key)
        if ma:
            v = 1 if price > ma else -1
            add(f"Price vs {label}", v, f"{'above' if v>0 else 'below'} {label}")

    # golden/death structure
    if r.get("sma50") and r.get("sma200"):
        v = 1 if r["sma50"] > r["sma200"] else -1
        add("SMA50 vs SMA200", v, "golden" if v > 0 else "death")

    # --- momentum: MACD histogram ---
    h = r.get("macd", {}).get("hist")
    if h is not None:
        add("MACD hist", 1 if h > 0 else -1, f"hist {h}")

    # --- ADX gates trend strength (doesn't vote direction, scales confidence) ---
    adx = r.get("adx")
    trend_strong = adx is not None and adx >= 25

    # --- oscillators: contrarian at extremes ---
    rsi_d = r.get("rsi_D")
    if rsi_d is not None:
        if rsi_d < 30:
            add("RSI(D)", 1, "oversold <30")
        elif rsi_d > 70:
            add("RSI(D)", -1, "overbought >70")
        else:
            add("RSI(D)", 0, f"neutral {rsi_d}")

    st = r.get("stoch", {}).get("k")
    if st is not None:
        if st < 20:
            add("Stochastic", 1, "oversold")
        elif st > 80:
            add("Stochastic", -1, "overbought")
        else:
            add("Stochastic", 0, "mid")

    wr = r.get("williams_r")
    if wr is not None:
        if wr < -80:
            add("Williams %R", 1, "oversold")
        elif wr > -20:
            add("Williams %R", -1, "overbought")
        else:
            add("Williams %R", 0, "mid")

    cci = r.get("cci")
    if cci is not None:
        if cci < -100:
            add("CCI", 1, "oversold")
        elif cci > 100:
            add("CCI", -1, "overbought")
        else:
            add("CCI", 0, "mid")

    # --- VWAP: above = intraday strength ---
    vw = r.get("vwap_session") or r.get("vwap_roll20")
    if vw:
        add("VWAP", 1 if price > vw else -1,
            f"{'above' if price > vw else 'below'} VWAP")

    # --- OBV slope: accumulation/distribution ---
    obv_slope = r.get("obv", {}).get("slope")
    if obv_slope is not None:
        add("OBV slope", 1 if obv_slope > 0 else -1,
            "accumulation" if obv_slope > 0 else "distribution")

    net = sum(v["vote"] for v in votes)
    active = [v for v in votes if v["vote"] != 0]
    n = len(active) or 1
    bull = sum(1 for v in active if v["vote"] > 0)
    bear = sum(1 for v in active if v["vote"] < 0)

    direction = "LONG" if net > 0 else "SHORT" if net < 0 else "NEUTRAL"
    agreement = abs(net) / n  # 0..1

    # honest probability: base 50, lean by agreement, dampened, ADX-scaled,
    # hard-capped at 72% because indicator agreement is weak evidence.
    lean = agreement * (0.30 if trend_strong else 0.18)
    prob = 50 + lean * 100   # confidence magnitude in the chosen direction
    prob = min(72.0, max(50.0, prob))

    return {
        "direction": direction,
        "probability": round(prob, 0),
        "net_score": net, "bull_votes": bull, "bear_votes": bear,
        "adx": adx, "trend_strength": "strong" if trend_strong else "weak/none",
        "votes": votes,
        "note": "Rules score = how much indicators agree, NOT a backtested "
                "win-rate. Capped at 72%. Strong ADX (>=25) widens confidence.",
    }


def _r(x, n=2):
    if x is None:
        return None
    try:
        if x != x:  # NaN
            return None
    except TypeError:
        return x
    return round(float(x), n)
