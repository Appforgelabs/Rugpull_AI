"""
learn_content.py — the reference guide shown in the Learning tab.

Plain-English explanations of every indicator and value in the app: what it
measures, how to read it, how to use it well, and the honest caveat (most are
lagging). Written to be skimmable and practical, not academic.
"""

# Each entry: (name, category, lag, what, how_to_read, how_to_use, watch_out)
GUIDE = [
    ("Composite Score", "Fundamentals", "n/a",
     "A 0–100 blend of valuation, quality, momentum, sentiment, and DCF for the whole business.",
     "70+ = strong profile; 45–58 = mixed; under 35 = multiple red flags.",
     "Use it to rank which companies deserve deeper research, not as a buy button. Open the breakdown to see which leg drives the score.",
     "A high score on an unprofitable company leans on momentum/sentiment — thinner evidence than for a profitable one."),

    ("P/E (Price / Earnings)", "Valuation", "n/a",
     "Price divided by earnings per share — how many dollars you pay per dollar of annual profit.",
     "Lower can mean cheaper; compare to the company's OWN history, not across industries.",
     "Best signal is current P/E vs the stock's historical median (the app shows both). Below its norm = relatively cheap.",
     "Negative P/E = the company loses money; the ratio is meaningless there. High P/E can be justified by growth."),

    ("P/S (Price / Sales)", "Valuation", "n/a",
     "Price relative to revenue. Useful when earnings are negative or volatile.",
     "Lower is cheaper per dollar of sales. Heavily sector-dependent (software runs high, retail low).",
     "Good for fast-growers with no earnings yet. Compare only to peers in the same business.",
     "Ignores profitability entirely — high sales at a loss is not value."),

    ("P/FCF (Price / Free Cash Flow)", "Valuation", "n/a",
     "Price relative to the actual cash the business throws off after capex.",
     "Lower is better. Often more honest than P/E because cash is harder to manipulate than earnings.",
     "A favorite quality-value cross-check. Cheap on P/FCF with growing FCF is a strong combo.",
     "Negative for cash-burning companies; like P/E, meaningless when negative."),

    ("EV/EBITDA", "Valuation", "n/a",
     "Enterprise value vs operating earnings — values the whole firm including debt.",
     "Lower is cheaper. Neutralizes capital-structure differences, so better than P/E for comparing levered companies.",
     "Use when comparing companies with different debt loads. ~8–12 is typical; varies by sector.",
     "Ignores capex and changes in working capital."),

    ("PEG", "Valuation", "n/a",
     "P/E divided by earnings growth rate — valuation adjusted for how fast it's growing.",
     "Around 1 = fairly priced for its growth; under 1 = cheap relative to growth.",
     "Reconciles 'expensive P/E' with 'fast growth'. A 40 P/E growing 40% (PEG 1) isn't crazy.",
     "Depends entirely on the growth estimate, which is itself a forecast that can be wrong."),

    ("ROE (Return on Equity)", "Quality", "n/a",
     "Profit generated per dollar of shareholder equity — how efficiently the business compounds capital.",
     "Higher is better; 15%+ is solid, 25%+ is excellent and rare to sustain.",
     "A core quality marker. Durable high ROE often signals a real competitive moat.",
     "Can be inflated by heavy debt. Cross-check with debt-to-equity."),

    ("DCF Intrinsic Value", "Valuation", "n/a",
     "A discounted-cash-flow estimate of what the business is 'worth' from its future cash flows.",
     "Compare to price: a positive margin of safety means price is below the model's value.",
     "Treat the assumptions (growth, discount rate) as arguments. Change them and watch value swing — that sensitivity IS the lesson.",
     "Garbage in, garbage out. A DCF is only as good as its inputs; never trust a single point estimate."),

    ("RSI (Relative Strength Index)", "Momentum", "lagging",
     "Speed and size of recent price moves, scaled 0–100.",
     "Below 30 = oversold (stretched down); above 70 = overbought (stretched up).",
     "Watch it across timeframes (the app shows 1d/D/W/M). Oversold on a higher timeframe in an uptrend is a classic pullback-buy zone.",
     "In strong trends RSI can stay overbought/oversold for a long time — 'oversold' is not 'about to bounce'."),

    ("Multi-timeframe RSI", "Momentum", "lagging",
     "RSI computed on intraday, daily, weekly, monthly bars at once.",
     "Alignment matters: weekly RSI rising while daily dips = healthy uptrend pullback.",
     "Use the higher timeframe for direction, the lower for timing. Don't fight the weekly with a daily signal.",
     "All of them are lagging; they describe momentum that already happened."),

    ("VWAP (Volume-Weighted Avg Price)", "Intraday", "coincident",
     "The average price weighted by volume — where the 'real' money traded.",
     "Price above VWAP = intraday buyers in control; below = sellers in control.",
     "Day-trade reference: longs are stronger above VWAP. Session VWAP resets each day; the app uses true session VWAP intraday.",
     "Most meaningful intraday. As a daily rolling value it's just another moving average."),

    ("SMA 20/50/200/325", "Trend", "lagging",
     "Simple moving averages — the average close over N days.",
     "Price above a rising SMA = uptrend on that horizon. 50-above-200 = 'golden cross' (bullish structure).",
     "Use the 200/325 for the long-term tide, 20/50 for the swing. Stacked in order = clean trend.",
     "They lag by design — a 200-day average turns slowly, confirming moves well after they start."),

    ("MACD", "Momentum", "lagging",
     "Difference between fast and slow moving averages, plus a signal line.",
     "Histogram above zero = upward momentum building; crossing down = momentum fading.",
     "The histogram flipping is an earlier read than the lines crossing. Best as a momentum confirm, not a standalone trigger.",
     "Whippy in sideways markets — lots of false flips when there's no trend."),

    ("ATR (Average True Range)", "Volatility", "coincident",
     "Average daily price range — how much the stock typically moves in dollars.",
     "Higher ATR = more volatile. It's a size, not a direction.",
     "Use it to set stops (e.g. 2×ATR below entry) and position size, so a normal wiggle doesn't stop you out.",
     "Says nothing about which way price goes — purely a volatility gauge."),

    ("Bollinger Bands", "Volatility", "lagging",
     "A moving average with bands at ±2 standard deviations.",
     "%b near 1 = price at upper band (stretched up); near 0 = lower band (stretched down).",
     "Band 'squeeze' (narrowing) often precedes a volatility expansion. Useful for spotting coiled setups.",
     "Touching a band is not a reversal signal — in trends price rides the band."),

    ("ADX", "Trend strength", "lagging",
     "Measures how strong a trend is, regardless of direction.",
     "Below 20 = no trend (choppy); above 25 = a real trend is in force.",
     "Use it as a gate: trend-following signals (Supertrend, MACD) are far more reliable when ADX is above 25.",
     "Tells you strength, not direction — a high ADX can be a strong DOWNtrend."),

    ("Supertrend", "Trend", "lagging",
     "An ATR-banded trend follower that flips between up and down.",
     "Green/up = the trend-follow bias is long; red/down = short. The app shows ATR distance to the next flip.",
     "Good trailing-stop and bias tool. Many traders use the Supertrend line itself as the stop-loss.",
     "It flips AFTER the trend turns — it confirms, it doesn't predict. Whipsaws in sideways markets."),

    ("Stochastic / Williams %R / CCI", "Oscillators", "lagging",
     "Three oscillators that all measure how stretched price is within its recent range.",
     "Each flags overbought/oversold zones (the app marks them).",
     "They mostly agree — treat them as ONE vote, not four. Most useful for timing entries within a known trend.",
     "Highly correlated with each other and with RSI, so 'four agree' is weaker evidence than it looks."),

    ("OBV (On-Balance Volume)", "Volume", "coincident",
     "Running total of volume, added on up days and subtracted on down days.",
     "Rising OBV = accumulation (buying); falling = distribution (selling).",
     "Watch for divergence: price flat but OBV rising can hint at quiet accumulation before a move.",
     "Volume can lead price slightly, but OBV is noisy and easily skewed by one big day."),

    ("Pivot Points", "Levels", "lagging",
     "Support/resistance levels computed from the prior bar's high/low/close.",
     "P is the pivot; R1–R3 are resistance above, S1–S3 support below.",
     "Day traders use them as intraday targets and reaction zones. Price often pauses or reverses near them.",
     "Self-fulfilling more than predictive — they work partly because many traders watch them."),

    ("Fibonacci Levels", "Levels", "lagging",
     "Retracement levels (38.2%, 50%, 61.8%) of a recent swing.",
     "In an uptrend, pullbacks often find support near these levels.",
     "Use the 0.5 and 0.618 as pullback-entry zones in a trend you already believe in.",
     "There's no physics here — like pivots, they work partly because they're widely watched."),

    ("Relative Strength vs S&P", "Leading-ish", "leading",
     "Whether the stock is outperforming the index, and whether that lead is growing.",
     "Positive and rising = the stock is stronger than the market.",
     "One of the few genuinely leading single-name tells — rising RS can show institutional accumulation before it's obvious in price.",
     "Leading-ish, not certain. RS can reverse; it's a tilt, not a guarantee."),

    ("Market Regime (Macro)", "Leading", "leading",
     "Risk-on / risk-off read from yields, the curve, SPY/QQQ trend, VIX, breadth, and the dollar.",
     "Risk-on = tailwind for longs; risk-off = headwind. The app re-weights conviction by this.",
     "This is the tide. Most single-stock moves are beta — knowing the regime is more forward-useful than any one stock's RSI.",
     "'Leading' means quarters-ahead with big error bars (yield curve) to days-ahead at best (VIX). Context, not prophecy."),

    ("Yield Curve (10y–2y)", "Macro / Leading", "leading",
     "The spread between 10-year and 2-year Treasury yields.",
     "Negative (inverted) = recession-warning, risk-off; steep positive = risk-on friendly.",
     "Watch the trend and the sign. Inversion has preceded most recessions — but with long, variable lag.",
     "Famous for crying wolf early — inversions can lead downturns by a year or more."),

    ("VIX", "Macro / Leading", "leading",
     "The market's expected 30-day volatility — the 'fear gauge'.",
     "Below 15 = complacent/calm; above 25 = elevated fear.",
     "Rising VIX often precedes equity stress. Spikes can also mark capitulation bottoms.",
     "A level, not a direction — high VIX can resolve either way."),

    ("Probability Zones (σ)", "Risk framing", "n/a",
     "Green ±1σ and red ±2σ projection bands from price volatility, plus the valuation corridor.",
     "The WIDTH is the message — it shows the range price could wander into by chance.",
     "Use it to set realistic targets and stops, and to sanity-check that a price target is even within the normal range.",
     "NOT a forecast. The center line is drift, not a prediction. Wide cone = high uncertainty, which is the honest default."),

    ("Conviction %", "Signal framing", "n/a",
     "How much the indicators agree on direction, re-weighted by market regime.",
     "Higher = more signals align. Hard-capped at 72%.",
     "Read it as agreement strength, NOT odds of winning. Open the vote breakdown to see what's driving it.",
     "Indicators are correlated, so 'many agree' overstates the evidence. Only the backtest tells you real win-rate."),

    ("Volume Profile / CVD (approx)", "Volume", "coincident",
     "Time-decayed volume at each price level, split into estimated buyer- vs seller-controlled volume (a bar approximation of CVD, not tick data).",
     "Heavy bands = price zones where lots of cost basis lives (POC = the heaviest). Green tint = net buying there; red = net selling. Thin gaps = price tends to move fast through them.",
     "Disposition-effect logic: trapped buyers above price create overhead supply (resistance); heavy zones below tend to be defended (support). Buy near a strong shelf with a stop below the gap; expect stalls at overhead shelves.",
     "These are REACTION zones, not forecasts — and the logic decays (capitulation, taxes, stops). The delta split is an estimate from bar shape, not true order-flow data. Backtest it before trusting it."),

    ("Backtest verdict", "Validation", "n/a",
     "Replays a strategy on history with costs and no lookahead, vs buy-and-hold.",
     "The number that matters is EXCESS vs holding, and whether edge survives out-of-sample.",
     "This is the truth-teller. Trust a strategy only if it beats hold AND holds up out-of-sample on several names.",
     "In-sample success is easy and usually fake. Under 30 trades = not meaningful. Most technical strategies fail this test — that's the point."),
]


PRINCIPLES = [
    "Almost every chart indicator is LAGGING — it transforms past prices. It describes the wake, not the bow.",
    "The leading-er inputs are macro regime, breadth, the yield curve, and relative strength — and even those are tilts, not prophecy.",
    "Correlated signals overstate evidence: RSI, Stochastic, Williams %R, and CCI largely measure the same thing.",
    "A signal you haven't backtested out-of-sample is a hypothesis, not an edge.",
    "Costs and slippage are real. A strategy that wins before costs often loses after them.",
    "Buy-and-hold is the benchmark to beat. Beating it is harder and rarer than it looks.",
    "Position sizing and stops matter more than entry signals. Survive first, optimize second.",
]
