"""
volume_profile.py — volume-at-price zones with an approximated CVD split.

The idea (disposition effect): heavy-volume price zones are where cost basis
lives. Trapped buyers above price create overhead supply (resistance); heavy
zones below price are defended (support). This module maps those zones from
daily bars.

HONEST LIMITS, baked into the labels:
  • True CVD needs tick-level aggressor data. From OHLCV bars we APPROXIMATE
    each bar's delta as volume × (2·(close−low)/(high−low) − 1): close near the
    high ⇒ buyers were in control. It's a money-flow-style proxy, not real CVD.
  • Zones decay: bag holders capitulate, taxes and stops break the breakeven
    story. Volume is time-weighted with a ~6-month half-life so stale zones
    fade.
  • Output is REACTION ZONES — places price is likely to respond — not a
    forecast of direction.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

HALF_LIFE_DAYS = 126          # ~6 trading months
HVN_MULT = 1.5                # bin ≥ 1.5× mean = high-volume node (shelf)
LVN_MULT = 0.5                # bin ≤ 0.5× mean = low-volume gap


def bar_delta(df: pd.DataFrame) -> pd.Series:
    """Approximate per-bar volume delta (positive = buyer-controlled)."""
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    frac = (2 * (df["close"] - df["low"]) / rng - 1).clip(-1, 1).fillna(0.0)
    return df["volume"] * frac


def build_profile(df: pd.DataFrame, bins: int = 30,
                  half_life: int = HALF_LIFE_DAYS) -> dict:
    """Time-decayed volume profile over the supplied daily bars."""
    if df is None or df.empty or len(df) < 40 or "volume" not in df:
        return {"ok": False, "note": "need >=40 daily bars with volume"}

    d = df.reset_index(drop=True)
    n = len(d)
    lo = float(d["low"].min())
    hi = float(d["high"].max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return {"ok": False, "note": "bad price range"}

    edges = np.linspace(lo, hi, bins + 1)
    tp = ((d["high"] + d["low"] + d["close"]) / 3).values
    idx = np.clip(np.digitize(tp, edges) - 1, 0, bins - 1)

    age = (n - 1) - np.arange(n)
    w = 0.5 ** (age / half_life)
    vol_w = d["volume"].values * w
    delta_w = bar_delta(d).values * w

    vol_bin = np.zeros(bins)
    delta_bin = np.zeros(bins)
    np.add.at(vol_bin, idx, vol_w)
    np.add.at(delta_bin, idx, delta_w)

    total = vol_bin.sum() or 1.0
    mean_nz = vol_bin[vol_bin > 0].mean() if (vol_bin > 0).any() else 0.0
    price = float(d["close"].iloc[-1])

    out_bins = []
    for b in range(bins):
        out_bins.append({
            "lo": round(float(edges[b]), 2), "hi": round(float(edges[b+1]), 2),
            "vol": round(float(vol_bin[b] / total), 4),       # share of total
            "delta": round(float(delta_bin[b] / total), 4),   # signed share
            "hvn": bool(mean_nz and vol_bin[b] >= HVN_MULT * mean_nz),
            "lvn": bool(mean_nz and 0 < vol_bin[b] <= LVN_MULT * mean_nz),
        })

    poc_i = int(np.argmax(vol_bin))
    poc = round(float((edges[poc_i] + edges[poc_i+1]) / 2), 2)

    # nearest strong shelves relative to current price
    support = resistance = None
    for b in range(bins):
        mid = (edges[b] + edges[b+1]) / 2
        if not out_bins[b]["hvn"]:
            continue
        if mid <= price and (support is None or mid > support["mid"]):
            support = {"mid": round(float(mid), 2),
                       "lo": out_bins[b]["lo"], "hi": out_bins[b]["hi"],
                       "delta": out_bins[b]["delta"]}
        if mid > price and (resistance is None or mid < resistance["mid"]):
            resistance = {"mid": round(float(mid), 2),
                          "lo": out_bins[b]["lo"], "hi": out_bins[b]["hi"],
                          "delta": out_bins[b]["delta"]}

    overhead = float(vol_bin[(edges[:-1] + edges[1:]) / 2 > price].sum() / total)

    return {
        "ok": True, "price": round(price, 2),
        "bins": out_bins, "poc": poc,
        "support_shelf": support, "resistance_shelf": resistance,
        "overhead_supply_pct": round(overhead * 100, 1),
        "half_life_days": half_life,
        "note": "Time-decayed volume-at-price. Delta is a bar APPROXIMATION of "
                "CVD, not tick data. Zones are reaction levels, not forecasts.",
    }
