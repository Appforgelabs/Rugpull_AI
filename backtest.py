"""
backtest.py — does any of this actually have edge? The honest test.

DESIGN PRINCIPLE #1: no lookahead. Every indicator here is causal (rolling /
ewm / cumulative), so its value at bar i depends only on bars <= i. Decisions
are made at the CLOSE of bar i and filled at the OPEN of bar i+1. You can never
trade on information you wouldn't have had.

DESIGN PRINCIPLE #2: costs are real. Commission + slippage applied on every
entry and exit. A backtest without costs is a fantasy.

DESIGN PRINCIPLE #3: the benchmark is buy-and-hold. A strategy that returns 40%
is worthless if buy-and-hold returned 60% over the same window. The number that
matters is EXCESS return vs just holding.

DESIGN PRINCIPLE #4: sample size honesty. 8 trades prove nothing. Results flag
themselves as not-significant below 30 trades.

What this tests: daily swing/trend strategies. It does NOT test the intraday
day-trade setups (no multi-year intraday history available).
"""

from __future__ import annotations
import numpy as np
import pandas as pd


# ---- causal indicator frame ------------------------------------------------
def indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Precompute all causal indicator columns once. Value at row i uses only
    data <= i, so stepping through this frame == recomputing at each bar, but
    O(n) instead of O(n^2)."""
    d = df.copy().reset_index(drop=True)
    c, h, l = d["close"], d["high"], d["low"]

    d["sma20"] = c.rolling(20).mean()
    d["sma50"] = c.rolling(50).mean()
    d["sma200"] = c.rolling(200).mean()
    d["sma325"] = c.rolling(325).mean()

    # RSI (Wilder)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    # MACD hist
    ef = c.ewm(span=12, adjust=False).mean()
    es = c.ewm(span=26, adjust=False).mean()
    macd_line = ef - es
    d["macd_hist"] = macd_line - macd_line.ewm(span=9, adjust=False).mean()

    # ATR
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()

    # Supertrend direction (causal cumulative)
    d["st_dir"] = _supertrend_dir(d, 10, 3.0)

    # Causal weekly RSI: RSI on a 5-trading-day resample, forward-filled to each
    # daily bar. Uses only data <= i (the resample is expanding, not future).
    wk_close = c.groupby(np.arange(len(c)) // 5).last()
    wdelta = wk_close.diff()
    wgain = wdelta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    wloss = (-wdelta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    wrsi = 100 - 100 / (1 + wgain / wloss.replace(0, np.nan))
    # map each daily bar to its week bucket's RSI
    d["rsi_w"] = wrsi.reindex(np.arange(len(c)) // 5).values
    return d


def _supertrend_dir(d: pd.DataFrame, period: int, mult: float) -> pd.Series:
    h, l, c = d["high"], d["low"], d["close"]
    hl2 = (h + l) / 2
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    fu, fl = upper.copy(), lower.copy()
    dirn = pd.Series(index=d.index, dtype=float)
    for i in range(len(d)):
        if i == 0:
            dirn.iloc[i] = -1
            continue
        fu.iloc[i] = (min(upper.iloc[i], fu.iloc[i-1])
                      if c.iloc[i-1] <= fu.iloc[i-1] else upper.iloc[i])
        fl.iloc[i] = (max(lower.iloc[i], fl.iloc[i-1])
                      if c.iloc[i-1] >= fl.iloc[i-1] else lower.iloc[i])
        if c.iloc[i] > fu.iloc[i-1]:
            dirn.iloc[i] = 1
        elif c.iloc[i] < fl.iloc[i-1]:
            dirn.iloc[i] = -1
        else:
            dirn.iloc[i] = dirn.iloc[i-1]
    return dirn


# ---- strategies: row -> desired signal (+1 long / -1 short / 0 flat) -------
def strat_supertrend(d, i):
    v = d["st_dir"].iloc[i]
    return int(v) if v == v else 0


def strat_sma_cross(d, i):
    a, b = d["sma50"].iloc[i], d["sma200"].iloc[i]
    if a != a or b != b:
        return 0
    return 1 if a > b else -1


def strat_rsi_meanrev(d, i):
    r = d["rsi"].iloc[i]
    if r != r:
        return 0
    if r < 30:
        return 1     # oversold -> long
    if r > 70:
        return -1    # overbought -> short
    return 0


def strat_swing_composite(d, i):
    """Replays the DASHBOARD's exact swing logic via the shared core in
    trade_signals, so backtest verdicts are about the real signal, not a
    cousin. Weekly RSI is approximated from a 5-bar resample up to bar i."""
    import trade_signals as TS
    price = d["close"].iloc[i]
    st_dir = d["st_dir"].iloc[i]
    sma50 = d["sma50"].iloc[i]
    sma200 = d["sma200"].iloc[i]
    macd_hist = d["macd_hist"].iloc[i]
    rsi_w = d["rsi_w"].iloc[i] if "rsi_w" in d.columns else float("nan")
    core = TS.swing_bias_core(price, st_dir, sma50, sma200, macd_hist, rsi_w)
    return 1 if core["bias"] == "LONG" else -1 if core["bias"] == "SHORT" else 0


STRATEGIES = {
    "Supertrend trend-follow": strat_supertrend,
    "SMA 50/200 cross": strat_sma_cross,
    "RSI mean-reversion": strat_rsi_meanrev,
    "Swing composite (dashboard logic)": strat_swing_composite,
}


# ---- the engine ------------------------------------------------------------
def run_backtest(df: pd.DataFrame, strategy_fn, *, allow_short=False,
                 atr_stop=2.0, commission=0.0005, slippage=0.0005,
                 max_hold=0, regime: pd.Series | None = None,
                 warmup=200) -> dict:
    """
    df: daily OHLCV (oldest first). strategy_fn(d, i) -> +1/-1/0.
    Costs are fractions (0.0005 = 5 bps each side). atr_stop in ATR multiples
    (0 disables). max_hold in bars (0 disables). regime: optional bool Series
    (True = risk-on) aligned to df; when given, longs only taken if regime True,
    shorts only if False.
    """
    d = indicator_frame(df)
    n = len(d)
    if n < warmup + 30:
        return {"ok": False, "note": f"need >{warmup+30} bars, have {n}"}

    cash = 1.0
    equity = []          # equity curve (mark-to-market)
    pos = 0              # +1/-1/0
    entry_px = 0.0
    entry_i = 0
    stop_px = None
    qty = 0.0
    trades = []
    last_signal = 0

    o = d["open"].values
    c = d["close"].values
    hi = d["high"].values
    lo = d["low"].values
    atr = d["atr"].values

    def cost(px):  # round-trip applied per side
        return px * (commission + slippage)

    for i in range(warmup, n - 1):
        sig = strategy_fn(d, i)

        # regime filter
        if regime is not None and i < len(regime):
            ron = bool(regime.iloc[i]) if regime.iloc[i] == regime.iloc[i] else True
            if sig > 0 and not ron:
                sig = 0
            if sig < 0 and ron:
                sig = 0

        # mark-to-market equity at close i
        if pos != 0:
            mtm = cash + pos * qty * (c[i] - entry_px)
        else:
            mtm = cash
        equity.append(mtm)

        # ---- manage open position (check stop on bar i, then exits) ----
        exit_now = False
        exit_px = None
        reason = None
        if pos != 0:
            # stop check using bar i's range (conservative: stop could trigger)
            if stop_px is not None:
                if pos > 0 and lo[i] <= stop_px:
                    exit_now, exit_px, reason = True, stop_px, "stop"
                elif pos < 0 and hi[i] >= stop_px:
                    exit_now, exit_px, reason = True, stop_px, "stop"
            # signal flip / flat -> exit at next open
            if not exit_now and (sig == -pos or sig == 0):
                exit_now, exit_px, reason = True, o[i+1], "signal"
            # max hold
            if not exit_now and max_hold and (i - entry_i) >= max_hold:
                exit_now, exit_px, reason = True, o[i+1], "max_hold"

        if exit_now:
            gross = pos * qty * (exit_px - entry_px)
            fees = qty * (cost(entry_px) + cost(exit_px))
            cash += gross - fees
            trades.append({
                "dir": "L" if pos > 0 else "S",
                "entry_date": str(d["date"].iloc[entry_i].date()),
                "exit_date": str(d["date"].iloc[i+1].date()),
                "entry": round(entry_px, 2), "exit": round(exit_px, 2),
                "ret_pct": round((exit_px/entry_px - 1) * 100 * (1 if pos > 0 else -1), 2),
                "bars": i + 1 - entry_i, "reason": reason,
            })
            pos = 0
            stop_px = None

        # ---- new entry at next open (only if flat and signal fresh) ----
        if pos == 0 and sig != 0 and (allow_short or sig > 0):
            # require signal to have refreshed (avoid instant re-entry after stop)
            if sig != last_signal or reason == "signal":
                entry_px = o[i+1]
                entry_i = i + 1
                qty = cash / entry_px
                pos = sig
                if atr_stop and atr[i] == atr[i]:
                    stop_px = (entry_px - atr_stop * atr[i] if pos > 0
                               else entry_px + atr_stop * atr[i])
        last_signal = sig

    # close any open position at last close
    if pos != 0:
        exit_px = c[-1]
        gross = pos * qty * (exit_px - entry_px)
        fees = qty * (cost(entry_px) + cost(exit_px))
        cash += gross - fees
        trades.append({
            "dir": "L" if pos > 0 else "S",
            "entry_date": str(d["date"].iloc[entry_i].date()),
            "exit_date": str(d["date"].iloc[-1].date()),
            "entry": round(entry_px, 2), "exit": round(exit_px, 2),
            "ret_pct": round((exit_px/entry_px - 1) * 100 * (1 if pos > 0 else -1), 2),
            "bars": n - 1 - entry_i, "reason": "close",
        })
    equity.append(cash)

    return _metrics(d, equity, trades, warmup)


# ---- honest metrics --------------------------------------------------------
def _metrics(d, equity, trades, warmup) -> dict:
    eq = pd.Series(equity)
    n_days = len(eq)
    total_ret = (eq.iloc[-1] - 1) * 100

    # buy & hold over the same tradeable window
    px = d["close"].iloc[warmup:].reset_index(drop=True)
    bh_ret = (px.iloc[-1] / px.iloc[0] - 1) * 100 if len(px) > 1 else 0.0

    years = n_days / 252 or 1
    cagr = ((eq.iloc[-1]) ** (1/years) - 1) * 100 if eq.iloc[-1] > 0 else -100

    # daily returns -> Sharpe
    rets = eq.pct_change().dropna()
    sharpe = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() else 0.0

    # max drawdown
    roll_max = eq.cummax()
    dd = ((eq - roll_max) / roll_max).min() * 100

    wins = [t for t in trades if t["ret_pct"] > 0]
    losses = [t for t in trades if t["ret_pct"] <= 0]
    nt = len(trades)
    win_rate = len(wins) / nt * 100 if nt else 0.0
    avg_win = np.mean([t["ret_pct"] for t in wins]) if wins else 0.0
    avg_loss = np.mean([t["ret_pct"] for t in losses]) if losses else 0.0
    expectancy = np.mean([t["ret_pct"] for t in trades]) if nt else 0.0
    gross_win = sum(t["ret_pct"] for t in wins)
    gross_loss = abs(sum(t["ret_pct"] for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss else float("inf")
    time_in = sum(t["bars"] for t in trades) / max(n_days, 1) * 100

    return {
        "ok": True,
        "total_return_pct": round(total_ret, 1),
        "buy_hold_pct": round(bh_ret, 1),
        "excess_vs_hold_pct": round(total_ret - bh_ret, 1),
        "beat_hold": bool(total_ret > bh_ret),
        "cagr_pct": round(cagr, 1),
        "sharpe": round(float(sharpe), 2),
        "max_drawdown_pct": round(float(dd), 1),
        "num_trades": nt,
        "win_rate_pct": round(win_rate, 1),
        "avg_win_pct": round(float(avg_win), 2),
        "avg_loss_pct": round(float(avg_loss), 2),
        "expectancy_pct": round(float(expectancy), 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "time_in_market_pct": round(time_in, 1),
        "significant": bool(nt >= 30),
        "equity_curve": [round(float(x), 4) for x in equity],
        "buy_hold_curve": [round(float(x), 4) for x in (px / px.iloc[0]).tolist()] if len(px) > 1 else [],
        "trades": trades,
        "verdict": _verdict(total_ret, bh_ret, nt, sharpe, dd),
    }


def run_split(df: pd.DataFrame, strategy_fn, split=0.6, **kw) -> dict:
    """
    The honesty test: run the SAME strategy on an in-sample (first `split`) and
    out-of-sample (rest) period. If edge only appears in-sample, it's noise.
    """
    n = len(df)
    cut = int(n * split)
    warmup = kw.get("warmup", 200)
    if cut < warmup + 60 or (n - cut) < warmup + 60:
        return {"ok": False, "note": "not enough history for a meaningful split"}

    ins = run_backtest(df.iloc[:cut].reset_index(drop=True), strategy_fn, **kw)
    oos = run_backtest(df.iloc[cut - warmup:].reset_index(drop=True), strategy_fn, **kw)
    if not ins.get("ok") or not oos.get("ok"):
        return {"ok": False, "note": "split period too short after warmup"}

    holds = bool(oos["beat_hold"] and oos["num_trades"] >= 15 and oos["sharpe"] > 0)
    return {
        "ok": True, "in_sample": ins, "out_sample": oos, "holds_up": holds,
        "verdict": ("EDGE HELD out-of-sample (beat hold, positive Sharpe). The "
                    "most encouraging thing a backtest can show — still one "
                    "ticker, one period, so stay skeptical." if holds else
                    "EDGE DID NOT HOLD out-of-sample. The in-sample result was "
                    "likely curve-fit or luck. This is the normal, healthy "
                    "outcome and exactly why you split-test."),
    }


def _verdict(total, bh, nt, sharpe, dd) -> str:
    if nt < 30:
        return ("INSUFFICIENT SAMPLE — {} trades. Not statistically meaningful; "
                "treat as anecdote.".format(nt))
    if total <= bh:
        return ("NO EDGE — underperformed buy & hold ({:.0f}% vs {:.0f}%). "
                "The strategy added work and risk for nothing.".format(total, bh))
    if sharpe < 0.5:
        return ("WEAK — beat hold but poor risk-adjusted return (Sharpe "
                "{:.2f}). Likely not worth the drawdowns.".format(sharpe))
    return ("PLAUSIBLE EDGE in-sample — beat hold by {:.0f}pts, Sharpe {:.2f}. "
            "Now test OUT-of-sample before believing it.".format(total - bh, sharpe))
