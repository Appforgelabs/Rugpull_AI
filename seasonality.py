"""
seasonality.py — monthly-returns seasonality heatmap (years × months).

Format mirrors the classic momentum-seasonality maps: one row per year
(newest at top, current year partial), one column per month, a "5 Yr Avg"
header row = mean of the five complete prior years, diverging red/green
cells, current month outlined.

HONEST NOTE: this is PRICE seasonality of the chosen ETF (e.g. SPY, QQQ) —
not the momentum-factor top-vs-bottom-decile spread some published maps show
(that needs full-universe decile portfolios). And seasonality is a weak,
noisy tendency measured on a handful of samples per cell (5 Julys is n=5);
it's context for sizing and timing, never a signal on its own.
"""

from __future__ import annotations
import datetime as dt

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fetch_daily(client, sym: str, years: int = 7) -> list[tuple[str, float]]:
    """Daily closes going back `years` via the EOD endpoint with an explicit
    from-date (the default endpoint only returns ~5y). Returns [(date, close)]
    ascending."""
    to = dt.date.today()
    frm = to - dt.timedelta(days=int(years * 365.25) + 10)
    raw = client._get(f"/stable/historical-price-eod/full?symbol={sym.upper()}"
                      f"&from={frm}&to={to}")
    if isinstance(raw, dict):
        raw = raw.get("historical") or []
    rows = [(r.get("date"), r.get("close") or r.get("adjClose"))
            for r in (raw or []) if r.get("date") and (r.get("close") or r.get("adjClose"))]
    rows.sort(key=lambda t: t[0])
    return rows


def monthly_grid(rows: list[tuple[str, float]], n_prior_years: int = 5) -> dict:
    """Build the heatmap grid from daily closes.
    Returns {years (desc, current first), grid[year][month]=ret%|None,
             avg[month]=mean over the n complete prior years, cur=(y,m)}"""
    if len(rows) < 300:
        return {"ok": False, "note": "insufficient history"}
    # month-end close per (year, month): last trading day of each month
    eom: dict[tuple[int, int], float] = {}
    for d, c in rows:
        y, m = int(d[:4]), int(d[5:7])
        eom[(y, m)] = float(c)          # ascending order → last write wins
    # monthly return vs previous month-end
    keys = sorted(eom)
    rets: dict[tuple[int, int], float] = {}
    for i in range(1, len(keys)):
        (y, m), (py, pm) = keys[i], keys[i - 1]
        # previous calendar month must be adjacent (no data gaps)
        if (y * 12 + m) - (py * 12 + pm) == 1:
            rets[(y, m)] = (eom[keys[i]] / eom[keys[i - 1]] - 1) * 100.0

    today = dt.date.today()
    cy, cm = today.year, today.month
    years = list(range(cy, cy - n_prior_years - 1, -1))   # current + N prior
    grid = {y: {m: rets.get((y, m)) for m in range(1, 13)} for y in years}
    # current month is in progress — mark it but don't show a fake full-month
    # number if the month isn't complete (use month-to-date vs last EOM)
    if (cy, cm) in eom and (cy, cm - 1) in eom or (cm == 1 and (cy - 1, 12) in eom):
        pass  # rets already computed treats latest close as month-end (MTD)

    complete = [y for y in years[1:]][:n_prior_years]     # the N prior years
    avg = {}
    for m in range(1, 13):
        vals = [grid[y][m] for y in complete if grid[y].get(m) is not None]
        avg[m] = round(sum(vals) / len(vals), 2) if len(vals) == len(complete) else (
            round(sum(vals) / len(vals), 2) if vals else None)

    # blank out future months of the current year
    for m in range(cm + 1, 13):
        grid[cy][m] = None

    return {"ok": True, "years": years, "grid": grid, "avg": avg,
            "cur": (cy, cm), "n_prior": n_prior_years,
            "mtd_note": f"{MONTHS[cm-1]} {cy} cell is month-to-date"}


def _cell_style(v: float | None, cap: float = 10.0) -> str:
    if v is None:
        return "background:#151a21;color:#3c4654"
    a = min(1.0, abs(v) / cap) * 0.80 + 0.10
    if v >= 0:
        bg = f"rgba(63,179,127,{a:.2f})"
    else:
        bg = f"rgba(214,80,80,{a:.2f})"
    fg = "#0c1116" if a > 0.55 else "#dfe7ee"
    return f"background:{bg};color:{fg}"


def render_heatmap_html(title: str, data: dict, cap: float = 10.0) -> str:
    if not data.get("ok"):
        return (f"<div style='color:#8899aa;font:13px system-ui;padding:14px'>"
                f"{title}: {data.get('note','no data')}</div>")
    cy, cm = data["cur"]
    th = ("padding:6px 8px;font:600 11px system-ui;color:#8899aa;"
          "text-align:center;border-bottom:1px solid #232c38")
    td = "padding:7px 6px;font:12px ui-monospace,monospace;text-align:center"
    cur_col = "box-shadow:inset 0 0 0 2px #d8a23a"
    H = [f"<div style='font:700 15px system-ui;color:#dfe7ee;margin:2px 0 1px'>{title}</div>",
         f"<div style='font:11px system-ui;color:#66788c;margin-bottom:8px'>"
         f"Monthly % return · 5 Yr Avg = mean of the five complete prior years · "
         f"boxed column = current month ({data['mtd_note']})</div>",
         "<table style='border-collapse:collapse;width:100%'>"]
    H.append("<tr><th style='" + th + "'></th>" + "".join(
        f"<th style='{th}{';color:#d8a23a' if m == cm else ''}'>{MONTHS[m-1]}</th>"
        for m in range(1, 13)) + "</tr>")
    # 5yr avg row
    H.append("<tr><td style='" + td + ";color:#8899aa;font-weight:600'>5 Yr Avg</td>"
             + "".join(
        f"<td style='{td};{_cell_style(data['avg'][m], cap)}"
        f"{';' + cur_col if m == cm else ''}'>"
        f"{data['avg'][m]:+.2f}</td>" if data["avg"][m] is not None else
        f"<td style='{td};{_cell_style(None)}'>—</td>"
        for m in range(1, 13)) + "</tr>")
    H.append(f"<tr><td colspan=13 style='border-bottom:2px solid #d8a23a;"
             f"padding:0'></td></tr>")
    for y in data["years"]:
        cells = []
        for m in range(1, 13):
            v = data["grid"][y][m]
            box = ";" + cur_col if (m == cm) else ""
            if v is None:
                cells.append(f"<td style='{td};{_cell_style(None)}{box}'>&nbsp;</td>")
            else:
                cells.append(f"<td style='{td};{_cell_style(v, cap)}{box}'>{v:+.2f}</td>")
        H.append(f"<tr><td style='{td};color:#8899aa;font-weight:600'>{y}</td>"
                 + "".join(cells) + "</tr>")
    H.append("</table>")
    return "".join(H)
