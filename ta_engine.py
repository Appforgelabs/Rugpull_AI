"""
ta_engine.py — the indicator math for the Trading tab.

Pure numpy/pandas, no TA-Lib dependency (so it installs clean on Streamlit
Cloud). Every function takes an OHLCV DataFrame (columns: open/high/low/close/
volume, oldest first) and returns plain floats.

Indicators: RSI, rolling VWAP, SMA(20/50/200/325), EMA, MACD, ATR, Bollinger
Bands, ADX, Stochastic, OBV, CCI, Williams %R, pivot points, Fibonacci levels.

These describe momentum and structure. They are not signals to act on blindly;
the Trading tab combines them into a transparent rules score (see trade_signal).
"""

from __future__ import annotations
import numpy as np
import pandas as pd


# ---- core momentum ---------------------------------------------------------
def rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return float("nan")
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return float(out.iloc[-1])


def sma(close: pd.Series, period: int) -> float:
    if len(close) < period:
        return float("nan")
    return float(close.rolling(period).mean().iloc[-1])


def ema(close: pd.Series, period: int) -> float:
    if len(close) < period:
        return float("nan")
    return float(close.ewm(span=period, adjust=False).mean().iloc[-1])


def macd(close: pd.Series, fast=12, slow=26, signal=9) -> dict:
    if len(close) < slow + signal:
        return {"macd": float("nan"), "signal": float("nan"), "hist": float("nan")}
    ef = close.ewm(span=fast, adjust=False).mean()
    es = close.ewm(span=slow, adjust=False).mean()
    line = ef - es
    sig = line.ewm(span=signal, adjust=False).mean()
    return {"macd": float(line.iloc[-1]), "signal": float(sig.iloc[-1]),
            "hist": float((line - sig).iloc[-1])}


# ---- volatility / structure ------------------------------------------------
def atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return float("nan")
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def bollinger(close: pd.Series, period=20, k=2.0) -> dict:
    if len(close) < period:
        return {"upper": float("nan"), "mid": float("nan"), "lower": float("nan"),
                "pctb": float("nan")}
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std()
    up, lo = mid + k * sd, mid - k * sd
    price = close.iloc[-1]
    rng = (up.iloc[-1] - lo.iloc[-1]) or np.nan
    pctb = (price - lo.iloc[-1]) / rng
    return {"upper": float(up.iloc[-1]), "mid": float(mid.iloc[-1]),
            "lower": float(lo.iloc[-1]), "pctb": float(pctb)}


def adx(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period * 2:
        return float("nan")
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return float(dx.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


# ---- oscillators -----------------------------------------------------------
def stochastic(df: pd.DataFrame, k=14, d=3) -> dict:
    if len(df) < k + d:
        return {"k": float("nan"), "d": float("nan")}
    low_k = df["low"].rolling(k).min()
    high_k = df["high"].rolling(k).max()
    pct_k = 100 * (df["close"] - low_k) / (high_k - low_k).replace(0, np.nan)
    pct_d = pct_k.rolling(d).mean()
    return {"k": float(pct_k.iloc[-1]), "d": float(pct_d.iloc[-1])}


def obv(df: pd.DataFrame) -> dict:
    if len(df) < 20:
        return {"obv": float("nan"), "slope": float("nan")}
    direction = np.sign(df["close"].diff()).fillna(0)
    o = (direction * df["volume"]).cumsum()
    # normalized 20-bar slope to read accumulation/distribution
    recent = o.tail(20)
    slope = float(np.polyfit(range(len(recent)), recent.values, 1)[0])
    return {"obv": float(o.iloc[-1]), "slope": slope}


def cci(df: pd.DataFrame, period=20) -> float:
    if len(df) < period:
        return float("nan")
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma_tp = tp.rolling(period).mean()
    md = (tp - sma_tp).abs().rolling(period).mean()
    return float(((tp - sma_tp) / (0.015 * md)).iloc[-1])


def williams_r(df: pd.DataFrame, period=14) -> float:
    if len(df) < period:
        return float("nan")
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    return float((-100 * (hh - df["close"]) / (hh - ll).replace(0, np.nan)).iloc[-1])


def rolling_vwap(df: pd.DataFrame, window: int = 20) -> float:
    """Rolling VWAP. For true session VWAP, feed intraday bars of one session."""
    if len(df) < window or "volume" not in df:
        return float("nan")
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).rolling(window).sum()
    vv = df["volume"].rolling(window).sum()
    return float((pv / vv).iloc[-1])


def session_vwap(df: pd.DataFrame) -> float:
    """True VWAP over all bars passed (use one intraday session's bars)."""
    if df.empty or "volume" not in df or df["volume"].sum() == 0:
        return float("nan")
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return float((tp * df["volume"]).sum() / df["volume"].sum())


# ---- levels ----------------------------------------------------------------
def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> dict:
    """
    Supertrend: ATR-banded trend follower. Returns the line value, direction
    (1 = uptrend/long, -1 = downtrend/short), and distance of price from the
    line in ATR units (how much room before a flip).
    """
    if len(df) < period + 1:
        return {"value": float("nan"), "dir": 0, "atr_dist": float("nan")}
    h, l, c = df["high"], df["low"], df["close"]
    hl2 = (h + l) / 2
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()
    upper = hl2 + multiplier * atr_
    lower = hl2 - multiplier * atr_

    fu = upper.copy()
    fl = lower.copy()
    st = pd.Series(index=df.index, dtype=float)
    dir_ = pd.Series(index=df.index, dtype=int)

    for i in range(len(df)):
        if i == 0:
            st.iloc[i] = upper.iloc[i]
            dir_.iloc[i] = -1
            continue
        fu.iloc[i] = (min(upper.iloc[i], fu.iloc[i-1])
                      if c.iloc[i-1] <= fu.iloc[i-1] else upper.iloc[i])
        fl.iloc[i] = (max(lower.iloc[i], fl.iloc[i-1])
                      if c.iloc[i-1] >= fl.iloc[i-1] else lower.iloc[i])
        if c.iloc[i] > fu.iloc[i-1]:
            dir_.iloc[i] = 1
        elif c.iloc[i] < fl.iloc[i-1]:
            dir_.iloc[i] = -1
        else:
            dir_.iloc[i] = dir_.iloc[i-1]
        st.iloc[i] = fl.iloc[i] if dir_.iloc[i] == 1 else fu.iloc[i]

    line = float(st.iloc[-1])
    d = int(dir_.iloc[-1])
    a = float(atr_.iloc[-1]) or float("nan")
    dist = abs(float(c.iloc[-1]) - line) / a if a == a and a else float("nan")
    return {"value": round(line, 2), "dir": d,
            "atr_dist": round(dist, 2) if dist == dist else None}


def pivot_points(df: pd.DataFrame) -> dict:
    """Classic floor-trader pivots from the most recent completed bar."""
    if len(df) < 2:
        return {}
    bar = df.iloc[-2]  # last completed bar
    h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
    p = (h + l + c) / 3
    return {"P": round(p, 2),
            "R1": round(2 * p - l, 2), "S1": round(2 * p - h, 2),
            "R2": round(p + (h - l), 2), "S2": round(p - (h - l), 2),
            "R3": round(h + 2 * (p - l), 2), "S3": round(l - 2 * (h - p), 2)}


def fib_levels(df: pd.DataFrame, lookback: int = 90) -> dict:
    """Fibonacci retracements over the lookback swing high/low."""
    if len(df) < 2:
        return {}
    w = df.tail(min(lookback, len(df)))
    hi, lo = float(w["high"].max()), float(w["low"].min())
    diff = hi - lo
    up = df["close"].iloc[-1] >= (lo + hi) / 2  # crude trend direction
    def lvl(r):
        return round(hi - diff * r, 2) if up else round(lo + diff * r, 2)
    return {"high": round(hi, 2), "low": round(lo, 2), "dir": "up" if up else "down",
            "0.236": lvl(0.236), "0.382": lvl(0.382), "0.5": lvl(0.5),
            "0.618": lvl(0.618), "0.786": lvl(0.786)}
