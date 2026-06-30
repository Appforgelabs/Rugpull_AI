"""
live_prices.py — lightweight 10-minute live price refresh (market hours only).

This is intentionally SEPARATE from and much cheaper than the full analysis
update. It pulls only what changes minute-to-minute:
  • a batched real-time quote for the whole watchlist in ONE call
    (/stable/batch-quote) — price, day change %, day high/low, volume
  • a small intraday sparkline per ticker (today's 5-min bars)

It does NOT recompute signals, corridor, conviction, or macro — those need the
full update and would only churn on noise if refreshed every 10 minutes.

Streamlit Cloud constraint stands: this only ticks while a browser tab is open.
Outside 9:30–16:00 ET on weekdays it idles and makes zero calls.
"""

from __future__ import annotations
import datetime as dt

REFRESH_SECONDS = 10 * 60


def market_open_now():
    """(is_open, now_et). Weekdays 9:30–16:00 US/Eastern."""
    try:
        from zoneinfo import ZoneInfo
        now = dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = dt.datetime.utcnow() - dt.timedelta(hours=4)  # rough ET fallback
    if now.weekday() >= 5:
        return False, now
    mins = now.hour * 60 + now.minute
    return (9 * 60 + 30) <= mins <= (16 * 60), now


def fetch_quotes(client, symbols: list[str]) -> dict:
    """One batched call for all symbols. Returns {SYM: {price, changePct,
    dayHigh, dayLow, volume}}. Falls back to per-symbol quote on failure."""
    out = {}
    if not symbols:
        return out
    syms = ",".join(sorted(set(symbols)))
    try:
        data = client._get(f"/stable/batch-quote?symbols={syms}")
        if isinstance(data, list) and data:
            for q in data:
                s = q.get("symbol")
                if not s:
                    continue
                out[s] = {
                    "price": _f(q.get("price")),
                    "changePct": _f(q.get("changePercentage") or q.get("changesPercentage")),
                    "dayHigh": _f(q.get("dayHigh")),
                    "dayLow": _f(q.get("dayLow")),
                    "volume": _f(q.get("volume")),
                }
            if out:
                return out
    except Exception:
        pass
    # fallback: short quote per symbol (still cheap-ish)
    for s in symbols:
        try:
            d = client._get(f"/stable/quote-short?symbol={s}")
            row = d[0] if isinstance(d, list) and d else {}
            if row.get("price") is not None:
                out[s] = {"price": _f(row.get("price")),
                          "changePct": _f(row.get("change")),
                          "dayHigh": None, "dayLow": None,
                          "volume": _f(row.get("volume"))}
        except Exception:
            continue
    return out


def fetch_sparkline(client, symbol: str, max_points: int = 40) -> list[float]:
    """Today's intraday closes (5-min bars), trimmed to the latest N points."""
    try:
        bars = client._get(f"/stable/historical-chart/5min?symbol={symbol}")
        if not isinstance(bars, list) or not bars:
            return []
        today = dt.date.today().isoformat()
        # bars are newest-first; take today's, oldest->newest
        todays = [b for b in bars if str(b.get("date", "")).startswith(today)]
        series = todays or bars[:max_points]
        closes = [float(b["close"]) for b in reversed(series) if b.get("close") is not None]
        return closes[-max_points:]
    except Exception:
        return []


def spark_svg(values: list[float], w: int = 120, h: int = 28) -> str:
    """Tiny inline SVG sparkline. Green if up on the day, red if down."""
    if not values or len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    up = values[-1] >= values[0]
    color = "#3fb37f" if up else "#d6504f"
    n = len(values)
    pts = " ".join(
        f"{round(i/(n-1)*w, 1)},{round(h - (v-lo)/rng*h, 1)}"
        for i, v in enumerate(values))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'preserveAspectRatio="none" style="display:block">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="1.5"/></svg>')


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
