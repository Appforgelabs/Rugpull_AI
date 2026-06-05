# stock_analyzer

A transparent, decision-support engine for evaluating stocks. It fetches data
from Financial Modeling Prep, models the fundamentals, reads price structure,
blends in macro regime and sentiment, and produces a **composite score with a
visible component breakdown** plus **rule-based entry/exit levels**.

It is deliberately *not* a black-box price predictor. See "Design philosophy."

## Run it — three ways

### 1. CLI (local or GitHub Codespaces terminal)
```bash
pip install -r requirements.txt
export FMP_API_KEY="your_fmp_key"
python analyze.py NVDA AMD INTC TSLA
```

### 2. Web UI locally
```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add your key
streamlit run streamlit_app.py
```

### 3. Web UI hosted free (from your GitHub repo)
1. Push this repo to GitHub.
2. Go to share.streamlit.io, sign in with GitHub, "Create app".
3. Point it at this repo, branch, and `streamlit_app.py`.
4. In **Advanced settings → Secrets**, paste:
   ```toml
   FMP_API_KEY = "your_fmp_key"
   ```
5. Deploy. You get a live `*.streamlit.app` URL that redeploys on every push.

**Do NOT** use GitHub Pages for this — Pages is static-only (no Python server),
and a browser-side rewrite would expose your FMP key in the page source.

Each `analyze()` result is a JSON-safe dict, so you can also pipe it into a
journal, dashboard, or your existing Plaid Portfolio app.

## Architecture

```
fmp_client.py   data access. All endpoint paths centralized in ENDPOINTS;
                cached to disk (1h TTL) with throttle + clear 401/429/404 errors.
fundamentals.py quality_score / value_score / simple_dcf — the trustworthy core.
technicals.py   momentum, VWAP±sigma bands, ATR, support/resistance,
                entry_exit_levels, and a humble naive_forecast (vol cone).
signals.py      macro_regime (yield-curve tilt) + pluggable sentiment providers
                (FMP news default; X optional, off by default).
analyze.py      orchestrator: blends legs via WEIGHTS, prints report, ranks.
streamlit_app.py web UI wrapper (sliders for weights, key via st.secrets).
```

## Design philosophy (read this)

- **Fundamentals are the durable signal.** Quality + valuation + DCF carry the
  most weight because they hold up best over time. The DCF takes its growth /
  discount / terminal assumptions as *arguments* so you argue with them openly.
- **Macro is a filter, not a thesis.** A hostile rate regime raises the margin
  of safety you demand at entry (`tilt` multiplier); it doesn't flip your call.
- **Sentiment is small-weighted on purpose.** It's noisy. Default provider uses
  FMP's news feed (data you already pay for). `XSentiment` is off by default —
  X is pay-per-use now (~$0.005/read, 2M/mo cap), so only enable it once you've
  decided the signal beats the cost. Wire your own `fetch_fn(sym)` into it.
- **Entry/exit are rule-based levels, not a predicted price.** Support/resistance,
  your VWAP standard-deviation bands, and 2*ATR stops. Defensible, inspectable.
- **The "forecast" is humble by design.** `naive_forecast` draws a volatility
  cone, not a target. The width is the lesson: short-horizon price prediction is
  mostly noise, and a confident point forecast would just manufacture false
  conviction. If you ever bolt on an ML model, treat its output as one more
  small-weighted leg with the same skepticism — and backtest it walk-forward
  before you believe a single number it emits.

## Extending it

- **Tune the blend:** edit `WEIGHTS` in `analyze.py` to match your philosophy.
- **Add X sentiment:** `S.XSentiment(fetch_fn=my_fetcher)` then pass it as the
  sentiment provider in `main()`.
- **Swap legacy endpoints:** if a `/stable/` path 404s, change it to the
  `/api/v3/` equivalent in `fmp_client.ENDPOINTS` — one place.
- **Backtest before trusting weights:** none of these scores are validated until
  you run them walk-forward on history. The scaffolding makes that easy; the
  discipline is on you.

Not financial advice — this is tooling to inform your own decisions.

## Cross-computer sync (Learn tab + starred values)

Your watchlist and starred values can sync across computers using the SAME
Google Apps Script your corridor chart already uses — but in a separate
namespace so it never touches the corridor's ticker data.

**One-time Apps Script edit** (adds the `rugpull` namespace):
Open your Apps Script project (Code.gs) and add the two helper functions plus
the two routing lines shown in `cloud_sync.py` (the `APPS_SCRIPT_ADDON` string).
In short:
1. Add `_rugpullGet(key)` and `_rugpullSave(key, dataset)` helpers.
2. In `doGet(e)`: if `e.parameter.key` is present, return `_rugpullGet(key)`.
3. In `doPost(e)`: if `req.action === 'saveApp'`, return `_rugpullSave(req.key, req.dataset)`.
4. Re-deploy the web app (Manage deployments → new version), access = Anyone.

**Then in the app:**
- Best: add `APPS_SCRIPT_URL = "https://script.google.com/macros/s/.../exec"`
  to Streamlit **Settings → Secrets**. The app auto-loads your data on startup.
- Or paste the URL into the sidebar "Cloud sync" box per session.
- Use **⬆ Save cloud** after changes, **⬇ Load cloud** on another computer.

Your progress (watchlist + starred) now follows you everywhere.
