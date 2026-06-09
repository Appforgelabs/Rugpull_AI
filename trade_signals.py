"""
trade_signals.py — multi-timeframe technicals + a transparent long/short score.

FIX-PASS DESIGN (v2):
  • Trend-following and mean-reversion are SEPARATE scores. They are different
    philosophies that bet against each other; blending them into one ballot
    cancels real signal. Direction/conviction come from the TREND block; the
    mean-reversion block is reported alongside as a stretch read ("uptrend but
    short-term stretched" is actionable; a blended 52% is not).
  • Votes are DEDUPLICATED. The old version counted price-vs-MA five times and
    four near-identical oscillators once each — conviction was secretly
    dominated by redundancy. Now: one consolidated MA-structure vote, one
    consolidated oscillator-stretch read.
  • SHORT requires positive bearish evidence, not just absence of bullish
    evidence. Symmetric scoring for both setups.
  • R:R is COMPUTED from actual entry/stop/target, never a hardcoded label.
  • swing_bias_core() is the single source of truth for the swing logic; the
    backtest imports and replays this exact function, so backtest verdicts are
    about the real dashboard logic, not a cousin of it.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

import ta_engine as TA

# How forward-looking each signal is. Almost all price-derived indicators are
# LAGGING (they transform past prices). This map drives the UI labels.
SIGNAL_LAG = {
    "MA structure": "lagging", "Supertrend": "lagging", "MACD hist": "lagging",
    "OBV slope": "coincident", "VWAP": "coincident",
    "Oscillator stretch": "lagging",
}


def lag_of(name: str) -> str:
    return SIGNAL_LAG.get(name, "lagging")


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
    """Resample daily bars to W/M/Q. Frequency aliases changed across pandas
    versions ('M'->'ME', 'Q'->'QE' in 2.2+); try new, fall back to legacy."""
    if df.empty:
        return df
    d = df.set_index("date")
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    fallbacks = {"ME": "M", "QE": "Q", "W": "W"}
    for code in (rule, fallbacks.get(rule, rule)):
        try:
            out = d.resample(code).agg(agg).dropna()
            return out.reset_index()
        except (ValueError, KeyError):
            continue
    return pd.DataFrame()


# ---- the assembled row -----------------------------------------------------
def build_trading_row(daily_df: pd.DataFrame, intraday_df: pd.DataFrame | None,
                      intraday_session_df: pd.DataFrame | None) -> dict:
    if daily_df.empty:
        return {"ok": False}

    c = daily_df["close"]
    price = float(c.iloc[-1])

    wk = resample(daily_df, "W")
    mo = resample(daily_df, "ME")
    qt = resample(daily_df, "QE")

    rsi_intraday = (TA.rsi(intraday_df["close"]) if intraday_df is not None
                    and not intraday_df.empty else float("nan"))
    stv = TA.supertrend(daily_df)

    row = {
        "ok": True, "price": round(price, 2),
        "rsi_1d": _r(rsi_intraday), "rsi_D": _r(TA.rsi(c)),
        "rsi_W": _r(TA.rsi(wk["close"])) if not wk.empty else None,
        "rsi_M": _r(TA.rsi(mo["close"])) if not mo.empty else None,
        "rsi_Q": _r(TA.rsi(qt["close"])) if not qt.empty else None,
        "vwap_session": _r(TA.session_vwap(intraday_session_df)
                           if intraday_session_df is not None else float("nan")),
        "vwap_roll20": _r(TA.rolling_vwap(daily_df, 20)),
        "sma20": _r(TA.sma(c, 20)), "sma50": _r(TA.sma(c, 50)),
        "sma200": _r(TA.sma(c, 200)), "sma325": _r(TA.sma(c, 325)),
        "macd": {k: _r(v) for k, v in TA.macd(c).items()},
        "atr14": _r(TA.atr(daily_df, 14)),
        "bb": {k: _r(v) for k, v in TA.bollinger(c).items()},
        "adx": _r(TA.adx(daily_df, 14)),
        "supertrend": {"value": _r(stv["value"]), "dir": stv["dir"],
                       "atr_dist": stv["atr_dist"]},
        "stoch": {k: _r(v) for k, v in TA.stochastic(daily_df).items()},
        "obv": {k: _r(v) for k, v in TA.obv(daily_df).items()},
        "cci": _r(TA.cci(daily_df)),
        "williams_r": _r(TA.williams_r(daily_df)),
        "pivots": TA.pivot_points(daily_df),
        "fib": TA.fib_levels(daily_df),
    }
    row["signal"] = trade_signal(row)
    return row


# ---- mean-reversion stretch (one consolidated read) -------------------------
def oscillator_stretch(r: dict) -> dict:
    """Average normalized stretch across RSI/Stoch/W%R/CCI (they measure nearly
    the same thing, so they get ONE consolidated read, not four votes).
    Returns stretch in [-1, +1]: +1 = very overbought, -1 = very oversold."""
    comps = []
    rsi = r.get("rsi_D")
    if rsi is not None:
        comps.append((rsi - 50) / 50)
    k = (r.get("stoch") or {}).get("k")
    if k is not None:
        comps.append((k - 50) / 50)
    wr = r.get("williams_r")
    if wr is not None:
        comps.append((wr + 50) / 50)          # -100..0 -> -1..+1
    cci = r.get("cci")
    if cci is not None:
        comps.append(max(-1.0, min(1.0, cci / 200)))
    if not comps:
        return {"stretch": None, "state": "no data", "n": 0}
    s = float(np.mean(comps))
    if s >= 0.5:
        state = "STRETCHED UP (overbought zone)"
    elif s <= -0.5:
        state = "STRETCHED DOWN (oversold zone)"
    else:
        state = "neutral"
    return {"stretch": round(s, 2), "state": state, "n": len(comps)}


# ---- the trend score (deduplicated votes) -----------------------------------
def trade_signal(r: dict) -> dict:
    votes = []
    price = r["price"]

    def add(name, vote, note):
        votes.append({"signal": name, "vote": vote, "note": note,
                      "lag": lag_of(name)})

    # 1) MA structure — ONE consolidated vote from three sub-checks
    sub = []
    if r.get("sma50") is not None:
        sub.append(1 if price > r["sma50"] else -1)
    if r.get("sma200") is not None:
        sub.append(1 if price > r["sma200"] else -1)
    if r.get("sma50") is not None and r.get("sma200") is not None:
        sub.append(1 if r["sma50"] > r["sma200"] else -1)
    if sub:
        s = sum(sub)
        v = 1 if s >= 2 else -1 if s <= -2 else 0
        add("MA structure", v,
            f"{sum(1 for x in sub if x>0)}/{len(sub)} bullish "
            f"(price vs 50/200, golden/death)")

    # 2) Supertrend — one vote
    stt = r.get("supertrend") or {}
    if stt.get("dir") is not None and stt.get("dir") != 0:
        add("Supertrend", 1 if stt["dir"] > 0 else -1,
            f"{'uptrend' if stt['dir']>0 else 'downtrend'}, "
            f"{stt.get('atr_dist')} ATR from flip")

    # 3) MACD histogram — one vote
    h = (r.get("macd") or {}).get("hist")
    if h is not None:
        add("MACD hist", 1 if h > 0 else -1 if h < 0 else 0, f"hist {h}")

    # 4) OBV slope — one vote (volume can lead price slightly)
    obv_slope = (r.get("obv") or {}).get("slope")
    if obv_slope is not None:
        add("OBV slope", 1 if obv_slope > 0 else -1,
            "accumulation" if obv_slope > 0 else "distribution")

    # 5) VWAP — one vote
    vw = r.get("vwap_session")
    if vw is None:
        vw = r.get("vwap_roll20")
    if vw is not None:
        add("VWAP", 1 if price > vw else -1,
            f"{'above' if price > vw else 'below'} VWAP")

    # mean-reversion: consolidated stretch, reported but NOT in the trend net
    mr = oscillator_stretch(r)
    if mr["stretch"] is not None:
        add("Oscillator stretch", 0, f"{mr['state']} ({mr['stretch']:+.2f}, "
                                     f"info only — not in trend score)")

    trend_votes = [v for v in votes if v["signal"] != "Oscillator stretch"]
    net = sum(v["vote"] for v in trend_votes)
    active = [v for v in trend_votes if v["vote"] != 0]
    n = len(active) or 1
    bull = sum(1 for v in active if v["vote"] > 0)
    bear = sum(1 for v in active if v["vote"] < 0)

    direction = "LONG" if net > 0 else "SHORT" if net < 0 else "NEUTRAL"
    agreement = abs(net) / n

    adx = r.get("adx")
    trend_strong = adx is not None and adx >= 25
    lean = agreement * (0.30 if trend_strong else 0.18)
    prob = min(72.0, max(50.0, 50 + lean * 100))

    swing = _swing_setup(r)
    day = _day_setup(r)

    return {
        "direction": direction,
        "probability": round(prob, 0),
        "net_score": net, "bull_votes": bull, "bear_votes": bear,
        "adx": adx, "trend_strength": "strong" if trend_strong else "weak/none",
        "votes": votes,
        "meanrev": mr,
        "swing": swing, "day": day,
        "note": "Conviction = TREND-vote agreement (deduplicated), NOT a "
                "backtested win-rate. Capped 72%. Oscillator stretch is shown "
                "separately — it's a different philosophy, not extra votes.",
    }


# ---- shared swing core (the backtest replays THIS exact function) -----------
def swing_bias_core(price, st_dir, sma50, sma200, macd_hist, rsi_w) -> dict:
    """Symmetric, evidence-based swing bias. Five bullish checks AND five
    bearish checks; a side must score >=4 with the other side <=1.
    NaNs/None are skipped on both sides."""
    bull = 0
    bear = 0
    if st_dir is not None and st_dir == st_dir and st_dir != 0:
        bull += st_dir > 0
        bear += st_dir < 0
    if _ok(sma50) and _ok(sma200):
        bull += sma50 > sma200
        bear += sma50 < sma200
    if _ok(price) and _ok(sma50):
        bull += price > sma50
        bear += price < sma50
    if macd_hist is not None and macd_hist == macd_hist:
        bull += macd_hist > 0
        bear += macd_hist < 0
    if rsi_w is not None and rsi_w == rsi_w:
        bull += 40 <= rsi_w <= 70      # healthy momentum, not euphoric
        bear += rsi_w < 40             # weak momentum = bearish evidence
    if bull >= 4 and bear <= 1:
        bias = "LONG"
    elif bear >= 4 and bull <= 1:
        bias = "SHORT"
    else:
        bias = "WAIT"
    return {"bias": bias, "bull": int(bull), "bear": int(bear), "max": 5}


def _swing_setup(r: dict) -> dict:
    price = r["price"]
    st = r.get("supertrend") or {}
    atr = r.get("atr14")
    core = swing_bias_core(price, st.get("dir"), r.get("sma50"),
                           r.get("sma200"), (r.get("macd") or {}).get("hist"),
                           r.get("rsi_W"))
    bias = core["bias"]
    setup = None
    if atr is not None and bias in ("LONG", "SHORT"):
        if bias == "LONG":
            raw_stop = (st.get("value") if st.get("dir", 0) and st["dir"] > 0
                        else price - 2 * atr)
            # never tighter than 1 ATR — a stop on top of price gets hit on noise
            stop = round(min(raw_stop, price - atr), 2)
            target = round(price + 3 * atr, 2)
        else:
            raw_stop = (st.get("value") if st.get("dir", 0) and st["dir"] < 0
                        else price + 2 * atr)
            stop = round(max(raw_stop, price + atr), 2)
            target = round(price - 3 * atr, 2)
        setup = {"entry": price, "stop": stop, "target": target,
                 "rr": _rr(price, stop, target)}
    score = core["bull"] if bias != "SHORT" else core["bear"]
    return {"bias": bias, "score": score, "max": core["max"], "setup": setup,
            "bull": core["bull"], "bear": core["bear"],
            "basis": "trend MAs + Supertrend + MACD + weekly RSI (evidence "
                     "required BOTH to enter and to short)"}


def _day_setup(r: dict) -> dict:
    """Intraday bias. Symmetric: three bullish checks, three bearish mirrors;
    requires full 3/3 alignment to call a side (day trades need tight setups)."""
    price = r["price"]
    vwap = r.get("vwap_session")
    rsi_1d = r.get("rsi_1d")
    atr = r.get("atr14")
    sma20 = r.get("sma20")

    have_intraday = vwap is not None or rsi_1d is not None
    bull = 0
    bear = 0
    if vwap is not None and _ok(price):
        bull += price > vwap
        bear += price < vwap
    if sma20 is not None and _ok(price):
        bull += price > sma20
        bear += price < sma20
    if rsi_1d is not None:
        bull += rsi_1d >= 50
        bear += rsi_1d < 50

    if bull == 3:
        bias = "LONG"
    elif bear == 3:
        bias = "SHORT"
    else:
        bias = "WAIT"

    setup = None
    if bias in ("LONG", "SHORT") and vwap is not None and atr is not None:
        if bias == "LONG":
            stop = round(vwap - 0.5 * atr, 2)
            target = round(price + 1.5 * atr, 2)
        else:
            stop = round(vwap + 0.5 * atr, 2)
            target = round(price - 1.5 * atr, 2)
        setup = {"entry": price, "stop": stop, "target": target,
                 "rr": _rr(price, stop, target)}
    score = bull if bias != "SHORT" else bear
    return {"bias": bias if have_intraday else "NO DATA", "score": score,
            "max": 3, "setup": setup, "bull": bull, "bear": bear,
            "basis": "VWAP + SMA20 + intraday RSI (3/3 alignment required)",
            "have_intraday": have_intraday}


def _rr(entry, stop, target) -> str | None:
    """Real reward:risk from the actual levels, not a hardcoded label."""
    try:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0:
            return None
        return f"{round(reward / risk, 2)}R"
    except (TypeError, ZeroDivisionError):
        return None


def _ok(x) -> bool:
    return x is not None and x == x and x != 0


def _r(x, n=2):
    if x is None:
        return None
    try:
        if x != x:  # NaN
            return None
    except TypeError:
        return x
    return round(float(x), n)
