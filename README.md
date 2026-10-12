# Polymarket Crypto Assistant

Real-time terminal dashboard that combines live Binance order flow with Polymarket prediction market prices to surface actionable crypto signals.

**By [@SolSt1ne](https://x.com/SolSt1ne)**

**[Polymarket](https://polymarket.com/?via=SolSt1ne)**
---

## What it does

- Streams live trades and orderbook from **Binance**
- Fetches Up/Down contract prices from **Polymarket** via WebSocket
- Calculates 11 indicators across orderbook, flow, and technical analysis
- Aggregates everything into a single **BULLISH / BEARISH / NEUTRAL** trend score
- Renders the full dashboard in the terminal with live refresh
- Shows a transparent, fee-adjusted Polymarket edge estimate and records forecasts for later scoring

---

## Supported coins & timeframes

| Coins | Timeframes |
|-------|------------|
| BTC, ETH, SOL, XRP | 5m, 15m, 1h, 4h, daily |

All 16 coin × timeframe combinations are supported on Polymarket.

---

## Indicators

**Order Book**
- OBI (Order Book Imbalance)
- Buy / Sell Walls
- Liquidity Depth (0.1% / 0.5% / 1.0%)

**Flow & Volume**
- CVD (Cumulative Volume Delta) — 1m / 3m / 5m
- Delta (1m)
- Volume Profile with POC

**Technical Analysis**
- RSI (14)
- MACD (12/26/9) + Signal + Histogram
- VWAP
- EMA 5 / EMA 20 crossover
- Heikin Ashi candle streak

---

## Setup

```bash
pip install -r requirements.txt
python main.py
```

The edge scanner is read-only. It records one forecast per time-to-expiry bucket in
`edge_research.db`, including compact Polymarket order-book snapshots. Before
showing an edge, it estimates a configurable probe-size buy (five shares by
default) using ask-side VWAP
within `EDGE_MAX_WALK` of the best ask, then subtracts the configured fee model
and slippage buffer. It scores resolved forecasts against outcomes and the
Polymarket midpoint using Brier score. Candidate PnL assumes the estimated entry
fills and holds to resolution; it is not a replay of actual fills. The fair-value
model is a driftless lognormal baseline, so an edge is a hypothesis to validate,
not a claim of profitability. Configure it with `POLY_TAKER_FEE_RATE`,
`EDGE_SLIPPAGE_BUFFER`, `EDGE_MIN_NET`, `EDGE_PROBE_SHARES`, and `EDGE_MAX_WALK`.
The default 7% fee-rate and `shares × rate × price × (1 − price)` formula follow
[Polymarket's published fee schedule](https://help.polymarket.com/en/articles/13364478-trading-fees).

After markets resolve, print the calibration report with:

```bash
python edge_report.py
```

---

## Project structure

```
arbi-pred/
├── src/
│   ├── config.py          # all constants — coins, URLs, indicator params
│   ├── feeds.py           # Binance + Polymarket data feeds
│   ├── edge_finder.py     # depth-aware research signals and forecast ledger
│   ├── indicators.py      # pure indicator calculations
│   └── dashboard.py       # Rich terminal UI & trend scoring
├── main.py                # entry point — menu & async orchestration
├── edge_report.py         # forecast calibration and signal report
├── requirements.txt       # Python dependencies
└── README.md
```
