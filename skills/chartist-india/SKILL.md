---
name: chartist-india
description: Chart and company analysis for Indian stocks (every NSE/BSE listing), Nifty/Sensex indices, global commodities (Brent, gold, copper...) and US Treasury yields, using the open-source Chartist engine on the user's own computer. Use when the user asks to analyse or read a chart, find support/resistance, chart patterns, Fibonacci levels, trend or momentum for a stock, index, commodity or yield; to compare charts of several Indian stocks; or for company information on an Indian listed company. Analysis only - never places orders, never gives buy/sell advice.
---

# Chartist (India)

Chartist reads a price chart the way a chartist would (trend on daily and weekly bars, support and resistance, classical and candlestick patterns, momentum, volatility, volume, Ichimoku, Fibonacci) and, for every signal, reports **how that signal has actually behaved on the same security in the past**. Python standard library only; prices from Yahoo Finance, filings from NSE.

## Where the code is

Everything this skill needs is inside its own folder, under `scripts/`:

- `<cli>` = `scripts/chartist/cli.py` in this skill's folder (when installed as a Claude Code plugin: `${CLAUDE_PLUGIN_ROOT}/skills/chartist-india/scripts/chartist/cli.py`)
- `<app>` = `scripts/chartist/app.py` (the interactive web app)

Use `python` (Windows: `py`, macOS/Linux: `python3`). Python 3.10+ is required; nothing to pip install. The first run downloads the NSE and BSE symbol lists (can take a minute) and caches prices next to the scripts.

## Commands

| Need | Command |
|---|---|
| Daily chart read | `python <cli> "<query>"` |
| Weekly read | `python <cli> "<query>" --tf W` |
| Full detail as JSON (for your own reasoning) | `python <cli> "<query>" --json` |
| Company information | `python <cli> "<query>" --company` |
| Interactive chart for the user | `python <app>` then open http://127.0.0.1:8765 |

`<query>` can be an NSE symbol (`RELIANCE`, `M&M`, `BAJAJ-AUTO`), a company name (`"hdfc bank"`), an index (`"nifty 50"`, `"nifty bank"`, `sensex`), `SYMBOL.BO` for a BSE-only stock, a commodity or yield name (`brent`, `gold`, `"us 10-year"`) or any Yahoo ticker (`BZ=F`, `^TNX`, `^GSPC`). Quote names with spaces.

## Workflow

1. Run the text read first. If the user wants depth, a comparison, or specific levels, run `--json` and read the fields below rather than guessing.
2. For "compare A and B", run each separately and compare the same fields side by side (bias, trend.long/intermediate/short, momentum.rsi, levels, active signals with their track records).
3. Report in plain language: what the chart shows now, the key levels, any pattern and its status, and the track record of the signals that are active.

### JSON fields worth reading

- `verdict`: `bias`, `confidence`, `score`, `narrative`, `invalidation` (the level that would change the reading)
- `trend`: long / intermediate / short direction, `structure` (higher highs etc.), ADX strength; `weekly` repeats this on weekly bars
- `levels.support` / `levels.resistance`: zones with touch counts; `high_52w`, `low_52w`, unfilled `gaps`
- `patterns`: name, `bias`, `status` (forming / confirmed / failed), key `line`, `target`
- `signals`: each signal's `reading` is its track record on this security (cases, return after N bars vs an ordinary stretch); `active` marks signals firing now
- `scorecard`: each indicator's reading out of 10 plus a separate `reliability` on this security. There is deliberately no total
- `fibonacci`, `ichimoku`, `momentum`, `volatility`, `volume`, `relative` (vs Nifty 50), `regime`
- `calibration`: the verdict's track record for this security's group (see rule 3)
- `note`: caveats from the app (custom settings, yield chart, futures roll, uncalibrated or thinly tested group). Always pass these on.

## Rules when reporting

1. **Analysis, not advice.** Never turn a reading into buy/sell/hold advice, entry or exit instructions, price targets for the user to act on, or position sizes. If asked "should I buy", describe what the chart shows and say the tool is not investment advice.
2. **Lead with the track record.** When a signal is active, say how it has behaved on that security before. If its reading says it "has worked AGAINST its usual meaning", "no better than an ordinary day" or "too few cases", say so plainly.
3. **The verdict is a description, not a forecast.** The `calibration` field gives the verdict's past track record for this security's group (Nifty 100, Midcap 150, Smallcap 250, other NSE, SME, BSE-only, commodity, yield, FX): `group`, `readings`, `bull_minus_bear_x60` and `finding`. Quote it when the user leans on the verdict. If `finding` is "does not separate" or "too few", or `untested` is true, say the verdict has not predicted returns for that group. Even "separates" is a modest historical edge measured on today's lists (survivorship), never a forecast.
4. **Cite the data.** Give the source and the date of the last bar with any price or level. Yahoo prices are unofficial and can lag; NSE/Yahoo company fields are unofficial too. Do not add prices, fundamentals or news from memory.
5. **Instrument caveats.** On a yield chart, "bullish" means yields rising (bond prices falling). Futures tickers (`=F`) are front-month continuous series; roll-date jumps are not real gaps. Stocks with fewer than 80 trading days cannot be read yet.
6. **Errors are answers.** If the tool fails (no data, site changed, new listing), report the message and what the user can do; do not fill the gap yourself.
7. End every analysis with: *Chartist is an analysis tool, not an investment recommendation; nothing here is investment advice under SEBI regulations.*
