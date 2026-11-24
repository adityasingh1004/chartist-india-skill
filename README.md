# Chartist (India) — Claude skill

Ask Claude to read Indian market charts for you. This skill runs the open-source Chartist engine on your own computer and lets Claude answer questions like:

- *"Read the chart of HDFC Bank."*
- *"Compare Tata Steel and JSW Steel on the weekly chart."*
- *"Where are the support and resistance levels on Nifty Bank?"*
- *"What does Brent crude's chart look like?"*
- *"Give me company information for Bharat Electronics."*

It covers every NSE and BSE listed stock, the Nifty and Sensex indices, global commodities and US Treasury yields. For every signal it reports **how that signal has actually behaved on the same security in the past**, including when it has not worked.

> **Analysis tool, not an investment recommendation.** The skill tells Claude never to turn a reading into buy or sell advice. Nothing here is investment advice or a research recommendation under SEBI regulations. Read [DISCLAIMER.md](DISCLAIMER.md).

## Install

You need Python 3.10 or newer on the computer where Claude runs code. There is nothing to `pip install`.

**Claude Code (plugin):**

```
/plugin install chartist-india --marketplace adityasingh1004/chartist-india-skill
```

On older Claude Code versions: `/plugin marketplace add adityasingh1004/chartist-india-skill`, then `/plugin install chartist-india@chartist-india`.

**As a skill folder (Claude Code or the Claude desktop app):** copy the `skills/chartist-india` folder into `~/.claude/skills/`, or zip that folder and upload it in Claude under Settings → Capabilities → Skills. The folder carries the whole engine.

Then just ask about a chart, or type `/chartist-india`.

**Network:** the skill fetches prices from Yahoo Finance and filings from NSE. It works where Claude runs code on your own computer. A cloud sandbox that blocks those sites will report the fetch error instead of an analysis.

## What Claude gets back

- Trend on daily and weekly bars, support and resistance zones with touch counts, 52-week range and gaps
- Chart patterns (double tops and bottoms, head and shoulders, triangles, channels, flags) with status and key line; candlestick patterns
- RSI, MACD, Stochastic, ADX, ATR, Bollinger Bands, Supertrend, Ichimoku, volume and OBV, relative strength against the Nifty 50, Fibonacci levels
- Each signal's track record on that security, and an indicator scorecard with a reliability rating (no total score)
- Company information (NSE classification, index memberships, F&O, surveillance, listing details, business profile)

## The honest part

The overall verdict was calibrated on 49 Nifty 50 stocks from 2016 to 2026 (3,474 point-in-time readings, default settings): stocks it read as bullish did **not** beat those it read as bearish (−0.3 points over 60 days). The skill tells Claude to present the verdict as a description of the chart, not a forecast, and to lead with each signal's own track record instead.

## Rules the skill gives Claude

1. No buy, sell or hold advice, no targets to act on, no position sizes.
2. Quote the track record of active signals, including failures.
3. Cite the data source and last bar date; add nothing from memory.
4. Pass on caveats: yield charts (bullish means yields rising), futures roll jumps, new listings with too little history.
5. End every analysis with the SEBI line.

## Related

The standalone desktop app version of Chartist (browser charts, no Claude needed) is maintained separately.

## Licence

MIT, see [LICENSE](LICENSE). Bundled chart library: TradingView Lightweight Charts (Apache-2.0). Author: Aditya Kumar Singh.
