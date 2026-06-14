"""
fmp_client.py — thin, cached client for Financial Modeling Prep.

FMP serves its primary data through `/stable/` routes now, with `/v3/` and
`/v1/` kept as legacy fallbacks. Endpoint paths are centralized in ENDPOINTS
below so that if FMP renames one, you change it in exactly one place.

lonevv
Set your key via env var:  export FMP_API_KEY="..."
"""

from __future__ import annotations

import os
import time
import json
import hashlib
from pathlib import Path
from typing import Any

import requests

BASE = "https://financialmodelingprep.com"

# All endpoint paths in one place. {sym} is substituted at call time.
# If a /stable/ path ever 404s, swap it for the legacy /api/v3/ equivalent.
ENDPOINTS = {
    "profile":        "/stable/profile?symbol={sym}",
    "quote":          "/stable/quote?symbol={sym}",
    "income":         "/stable/income-statement?symbol={sym}&period=annual&limit=6",
    "balance":        "/stable/balance-sheet-statement?symbol={sym}&period=annual&limit=6",
    "cashflow":       "/stable/cash-flow-statement?symbol={sym}&period=annual&limit=6",
    "ratios":         "/stable/ratios?symbol={sym}&period=annual&limit=6",
    "earnings":       "/stable/earnings?symbol={sym}&limit=40",
    "key_metrics":    "/stable/key-metrics?symbol={sym}&period=annual&limit=6",
    "ratios_ttm":     "/stable/ratios-ttm?symbol={sym}",
    "history":        "/stable/historical-price-eod/full?symbol={sym}",
    "news_sentiment": "/stable/news/stock?symbols={sym}&limit=50",
    "estimates":      "/stable/analyst-estimates?symbol={sym}&period=quarter&limit=12",
    "intraday":       "/stable/historical-chart/{interval}?symbol={sym}",
    # macro proxies (treasury + economic indicators live under stable too)
    "treasury":       "/stable/treasury-rates",
    "econ":           "/stable/economic-indicators?name={sym}",
}


class FMPError(RuntimeError):
    pass


class FMPClient:
    def __init__(self, api_key: str | None = None, cache_dir: str = ".fmp_cache",
                 cache_ttl: int = 3600, throttle: float = 0.25):
        self.api_key = api_key or os.environ.get("FMP_API_KEY")
        if not self.api_key:
            raise FMPError("No FMP API key. Set FMP_API_KEY env var or pass api_key=...")
        self.cache = Path(cache_dir)
        self.cache.mkdir(exist_ok=True)
        self.ttl = cache_ttl
        self.throttle = throttle
        self._last_call = 0.0
        self._session = requests.Session()

    # ---- internal -------------------------------------------------------
    def _cache_path(self, url: str) -> Path:
        h = hashlib.md5(url.encode()).hexdigest()[:16]
        return self.cache / f"{h}.json"

    def _get(self, path: str) -> Any:
        sep = "&" if "?" in path else "?"
        url = f"{BASE}{path}{sep}apikey={self.api_key}"
        cp = self._cache_path(url)

        if cp.exists() and (time.time() - cp.stat().st_mtime) < self.ttl:
            return json.loads(cp.read_text())

        # simple client-side throttle to stay polite on rate limits
        wait = self.throttle - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)

        r = self._session.get(url, timeout=20)
        self._last_call = time.time()
        if r.status_code == 401:
            raise FMPError("401 — bad/expired FMP key or endpoint not on your plan.")
        if r.status_code == 429:
            raise FMPError("429 — rate limited. Raise throttle= or upgrade FMP plan.")
        if r.status_code == 404:
            raise FMPError(f"404 — endpoint moved: {path}. Try the legacy /api/v3 route.")
        r.raise_for_status()
        data = r.json()
        cp.write_text(json.dumps(data))
        return data

    def fetch(self, key: str, sym: str) -> Any:
        if key not in ENDPOINTS:
            raise FMPError(f"Unknown endpoint key '{key}'. Known: {list(ENDPOINTS)}")
        return self._get(ENDPOINTS[key].format(sym=sym.upper()))

    # ---- convenience ----------------------------------------------------
    def profile(self, sym):        return _first(self.fetch("profile", sym))
    def quote(self, sym):          return _first(self.fetch("quote", sym))
    def income(self, sym):         return self.fetch("income", sym)
    def balance(self, sym):        return self.fetch("balance", sym)
    def cashflow(self, sym):       return self.fetch("cashflow", sym)
    def ratios(self, sym):         return self.fetch("ratios", sym)
    def earnings(self, sym):       return self.fetch("earnings", sym)
    def ratios_ttm(self, sym):     return _first(self.fetch("ratios_ttm", sym))
    def key_metrics(self, sym):    return self.fetch("key_metrics", sym)
    def history(self, sym):        return _normalize_history(self.fetch("history", sym))
    def news(self, sym):           return self.fetch("news_sentiment", sym)
    def estimates(self, sym):      return self.fetch("estimates", sym)

    def intraday(self, sym, interval="5min", days_back=5):
        """Intraday OHLCV bars. interval in {1min,5min,15min,30min,1hour,4hour}."""
        import datetime as _dt
        to = _dt.date.today()
        frm = to - _dt.timedelta(days=days_back)
        path = (f"/stable/historical-chart/{interval}?symbol={sym.upper()}"
                f"&from={frm}&to={to}")
        return self._get(path)


def _first(data):
    if isinstance(data, list):
        return data[0] if data else {}
    return data or {}


def _normalize_history(data):
    """FMP may return {'historical': [...]} or a bare list depending on route."""
    if isinstance(data, dict) and "historical" in data:
        return data["historical"]
    return data if isinstance(data, list) else []
