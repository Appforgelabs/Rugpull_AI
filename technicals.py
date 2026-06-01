"""
technicals.py — price-derived signals and RULE-BASED entry/exit levels.

This is where "entry and exit" actually come from — not from a predicted price,
but from defensible levels: VWAP standard-deviation bands (the extensions you
already use), ATR-based stops, and recent swing support/resistance. These are
levels you can reason about and that don't pretend to know the future.

Also includes naive_forecast() — read its docstring before trusting it. It is
deliberately humble.
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def to_frame(history: list) -> pd.DataFrame:
    """FMP history -> tidy DataFrame, oldest first."""
    if not history:
        return pd.DataFrame()
    df = pd.DataFrame(history)
    # field names vary; normalize the ones we need
    rename = {"close": "close", "high": "high", "low": "low",
              "open": "open", "volume": "volume", "date": "date"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def momentum_score(df: pd.DataFrame) -> dict:
    """
    Trend + momentum, blended to 0–100. Uses 50/200 SMA structure and
    multi-horizon returns. Momentum has weak but real persistence; this is
    a regime read, not a guarantee.
    """
    if len(df) < 200:
        return {"score": 50.0, "note": "need ~200 bars for full momentum read"}
    c = df["close"]
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    last = c.iloc[-1]

    above_50  = last > sma50.iloc[-1]
    above_200 = last > sma200.iloc[-1]
    golden    = sma50.iloc[-1] > sma200.iloc[-1]

    r1m = last / c.iloc[-21] - 1 if len(c) > 21 else 0
    r3m = last / c.iloc[-63] - 1 if len(c) > 63 else 0
    r6m = last / c.iloc[-126] - 1 if len(c) > 126 else 0

    trend = (above_50 * 0.2 + above_200 * 0.3 + golden * 0.2)
    ret_component = _clamp((np.tanh(4 * (0.4 * r1m + 0.35 * r3m + 0.25 * r6m)) + 1) / 2) * 0.3
    score = (trend + ret_component) * 100
    return {"score": round(score, 1),
            "above_50dma": bool(above_50), "above_200dma": bool(above_200),
            "golden_cross": bool(golden),
            "ret_1m": round(r1m, 3), "ret_3m": round(r3m, 3), "ret_6m": round(r6m, 3)}


def vwap_bands(df: pd.DataFrame, window: int = 20, k: float = 2.0) -> dict:
    """
    Rolling VWAP with ±k standard-deviation bands (your VWAP std-dev extension).
    Price near/below the lower band = potential entry zone; near/above upper =
    extended. 'sigmas' tells you how stretched price is right now.
    """
    if len(df) < window or "volume" not in df:
        return {"note": "insufficient data/volume for VWAP bands"}
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).rolling(window).sum()
    vv = df["volume"].rolling(window).sum()
    vwap = pv / vv
    # std of typical price weighted around vwap
    dev = (tp - vwap)
    sigma = dev.rolling(window).std()
    last_v, last_s = vwap.iloc[-1], sigma.iloc[-1]
    price = df["close"].iloc[-1]
    sigmas = (price - last_v) / last_s if last_s else 0.0
    return {
        "vwap": round(last_v, 2),
        "upper": round(last_v + k * last_s, 2),
        "lower": round(last_v - k * last_s, 2),
        "price": round(price, 2),
        "sigmas_from_vwap": round(float(sigmas), 2),
        "k": k, "window": window,
    }


def atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return float("nan")
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def support_resistance(df: pd.DataFrame, lookback: int = 60) -> dict:
    """Recent swing low/high as rough support/resistance."""
    if len(df) < lookback:
        lookback = len(df)
    window = df.tail(lookback)
    return {"support": round(window["low"].min(), 2),
            "resistance": round(window["high"].max(), 2)}


def entry_exit_levels(df: pd.DataFrame) -> dict:
    """
    Combine the rule-based pieces into actionable levels. These are SUGGESTIONS
    derived from price structure, not predictions. Position sizing and final
    judgment are yours.
    """
    price = df["close"].iloc[-1]
    a = atr(df)
    sr = support_resistance(df)
    bands = vwap_bands(df)

    suggested_stop = round(price - 2 * a, 2) if a == a else None
    return {
        "price": round(price, 2),
        "atr_14": None if a != a else round(a, 2),
        "support": sr["support"], "resistance": sr["resistance"],
        "vwap_lower_band": bands.get("lower"), "vwap_upper_band": bands.get("upper"),
        "suggested_stop_2atr": suggested_stop,
        "note": "Entry near support/lower band, exit near resistance/upper band, "
                "stop ~2*ATR below entry. Adjust to your risk per trade.",
    }


def naive_forecast(df: pd.DataFrame, horizon_days: int = 21) -> dict:
    """
    A DELIBERATELY HUMBLE projection. It is NOT alpha.

    It extrapolates recent log-return drift and draws a volatility cone (the
    range price could wander into by chance). Use it to size expectations and
    set realistic targets/stops — NOT as a directional bet. The honest takeaway
    from most such models is 'the cone is wide,' which is the correct lesson.
    """
    if len(df) < 60:
        return {"note": "need >60 bars"}
    logret = np.log(df["close"] / df["close"].shift()).dropna()
    mu, sigma = logret.tail(126).mean(), logret.tail(126).std()
    price = df["close"].iloc[-1]
    drift = price * np.exp(mu * horizon_days)
    band = price * np.exp(mu * horizon_days) * np.array([
        np.exp(-1.96 * sigma * np.sqrt(horizon_days)),
        np.exp(+1.96 * sigma * np.sqrt(horizon_days)),
    ])
    return {
        "horizon_days": horizon_days,
        "central_projection": round(float(drift), 2),
        "ci95_low": round(float(band[0]), 2),
        "ci95_high": round(float(band[1]), 2),
        "daily_vol": round(float(sigma), 4),
        "warning": "Random-walk cone. The width, not the center, is the real signal.",
    }


def _clamp(x, lo=0.0, hi=1.0):
    if x != x:
        return 0.5
    return max(lo, min(hi, x))
