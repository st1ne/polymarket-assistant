# Claude Instructions for Polymarket Assistant

**Last Updated**: February 8, 2026 (midnight KST)

## Project Overview

Real-time crypto trading dashboard + automated trading bot for Polymarket's Up/Down binary option markets. Originally forked from [st1ne/polymarket-assistant](https://github.com/st1ne/polymarket-assistant), extended with:
- **Fair Value calculator** (Black-Scholes binary option pricing against Chainlink oracle)
- **Automated trading bot** — Momentum Scalp v2 with TP/SL exits (no settlement dependency)
- **Comprehensive backtesting suite** (1818 scalp configs across 6 families + original 168 settlement configs)

## Architecture

```
main.py          – Dashboard entry point, coin/timeframe picker, asyncio event loop
bot.py           – Trading bot entry point (paper or live mode)
src/
  config.py      – All constants (coins, endpoints, indicator params)
  feeds.py       – Data feeds (Binance WS/REST, Polymarket WS/REST, oracle strike, period tracker)
  indicators.py  – Technical indicators + fair value math (RSI, MACD, EMA, OBI, CVD, Yang-Zhang vol, binary option pricing)
  dashboard.py   – Rich terminal UI (order book, flow, TA, fair value, signals panels)
  strategy.py    – Momentum Scalp v2 strategy (entry at min 5, exit via TP/SL)
  execution.py   – Order execution layer with TP/SL exit support (paper + live)
scripts/
  markout.py           – Mark-out analysis on raw trades
  backtest_momentum.py – Single-strategy momentum backtest (original, settlement-based)
  backtest_all_strategies.py – Multi-strategy backtest (9 families, 168 configs, settlement-based)
  backtest_scalp.py    – Scalping backtest with TP/SL exits (6 families, 1818 configs)
logs/
  trades_YYYY-MM-DD.jsonl – Daily trade log (auto-created by bot)
```

## Key Concepts

### Fair Value Calculator (the main addition)
- **Formula**: `P(Up) = Φ(d2)` where `d2 = (ln(S/K) - 0.5σ²T) / (σ√T)`
- **S** = Binance mid price (real-time proxy for current Chainlink stream price)
- **K** = Chainlink oracle strike ("PRICE TO BEAT") fetched from `polymarket.com/api/past-results`
- **σ** = Yang-Zhang annualized volatility on 1-minute klines (60-bar window)
- **T** = time remaining to expiry (in years)
- **Φ** = standard normal CDF via `math.erf` (zero dependencies)
- **Known limitation**: S uses Binance as proxy; actual resolution uses Chainlink. Typical basis is $50-150 (~0.1-0.2%). This introduces systematic model error.

### Data Sources
- **Binance**: Order book (REST poll 2s), trades + klines (WebSocket)
- **Polymarket WS**: Real-time Up/Down contract prices via CLOB WebSocket
- **Polymarket REST**: `outcomePrices` from Gamma API (fallback price poller every 10s)
- **Oracle Strike**: `polymarket.com/api/past-results?symbol=BTC&variant=fifteen&assetType=crypto&currentEventStartTime=...`
  - Returns actual Chainlink oracle open/close prices for past periods
  - Last result's `closePrice` = current period's strike (PRICE TO BEAT)

### Period Tracking (15-minute markets)
- Period boundaries: `(unix_ts // 900) * 900`
- On period transition: re-fetches oracle strike + new PM token IDs
- `state.strike_is_oracle` flag distinguishes oracle vs Binance fallback

### PM Price Sanity
- WebSocket can return stale 0.01/1.00 prices
- REST poller provides fallback via Gamma API `outcomePrices`
- Dashboard shows "PM prices stale" warning when prices are outside (0.02, 0.98)

## Polymarket Fee Structure (as of Feb 2026)

**Makers: 0% fees + earn daily USDC rebates** (20% of collected taker fees redistributed)

**Takers (15m crypto markets ONLY):**
- Fee formula: `shares × price × 0.25 × (price × (1 - price))²`
- Scales with probability — highest at 50%, lowest at extremes:
  - At 10%/90%: ~0.20%
  - At 30%/70%: ~0.88%
  - At 50%: ~1.56% (max)
- All other markets: 0% fees for both makers and takers

**Implication**: Maker-only execution eliminates all transaction costs and earns rebates. The only cost is adverse selection. This makes the minimum viable edge much smaller than for taker strategies.

Source: https://docs.polymarket.com/polymarket-learn/trading/maker-rebates-program

## Environment

- **Python**: 3.12 (installed via Homebrew)
- **Venv**: `./venv/` (NOT `.venv/`)
- **Platform**: macOS (Apple Silicon)
- **Dependencies**: `requests`, `websockets>=16.0`, `rich`, `py-clob-client` (for live trading)

### Run Commands
```bash
cd "/Users/spson/Library/Mobile Documents/com~apple~CloudDocs/Projects/Claude/polymarket-assistant"

# Dashboard (manual monitoring)
./venv/bin/python3 main.py

# Trading bot — paper mode (default, logs signals without placing orders)
./venv/bin/python3 bot.py

# Trading bot — paper mode with custom size
./venv/bin/python3 bot.py --size 25

# Trading bot — live mode (requires env vars)
PM_PRIVATE_KEY=0x... PM_FUNDER=0x... ./venv/bin/python3 bot.py --live --size 10

# Headless mode for server deployment (plain stdout, no Rich dashboard)
python -u bot.py --headless
```

### Railway Deployment
- **Procfile**: `worker: python -u bot.py --headless`
- `-u` flag = unbuffered stdout (essential for Railway log visibility)
- `--headless` = plain print statements instead of Rich dashboard, disables PM WebSocket (uses REST poller only)
- Check logs: `railway logs --tail 50`
- Redeploy: push to git, Railway auto-deploys

## Trading Context

The user trades BTC Up/Down 15-minute binary markets on Polymarket.

**Current strategy**: Mean reversion — buy cheap contracts early in the 15-min period when prices overshoot, sell for quick profit as prices revert toward fair value. Exclusively maker orders.

**Performance**: Started with $400 USDC, grew to $764+ (~90% return). Win rate ~64% across 11 markets. Not yet statistically significant (small sample) but consistent with a real edge in early-period mispricing.

**Key Insight**: Polymarket resolves using Chainlink BTC/USD oracle, NOT Binance. There's typically a $50-150 basis between Binance and Chainlink prices. The dashboard now uses the actual oracle strike.

## Active Strategy: Momentum Scalp v2 (TP/SL Exits)

**Implemented in**: `src/strategy.py` → executed by `bot.py`

### Why v2? Post-mortem on v1 (settlement-based)

v1 (trend_confirm + reversal) held to binary settlement (price resolves to 0 or 1).
- **Live result**: 7W/12L, -$98.18 over ~18 hours
- **Root cause**: P&L asymmetry — avg win +$3.12 vs avg loss -$9.15 ($10 trade size)
- **Breakeven win rate**: 76% (actual was 37%)
- **Conclusion**: Signal quality didn't matter — the exit mechanism was broken

### v2 Logic (Momentum Scalp with TP/SL)

**Entry** (evaluated once per period around minute 5):
- Buy the leading side if price >= 0.60 AND price trending up from minute 3
- No reversal sub-signal — just momentum confirmation

**Exit** (checked every 2 seconds while position is open):
- **Take Profit**: +15¢ above entry → exit immediately
- **Stop Loss**: -5¢ below entry → exit immediately
- **Deadline**: exit at market price at minute 14 if neither TP/SL hit

**Parameters**: `ENTRY_MINUTE=5.0, LOOKBACK_MINUTE=3.0, THRESHOLD=0.60, TAKE_PROFIT=0.15, STOP_LOSS=0.05, DEADLINE_MINUTE=14.0`

**Backtest** (96h, 294 periods, config `mom_m5_lb2_60%_tp0.15_sl0.05`):
- 185 trades, 58% win rate, +5.6¢/trade, t-stat = +8.18
- Avg win: +13.3¢, Avg loss: -4.9¢ (2.7:1 reward/risk ratio)
- Breakeven win rate: 27%, actual: 58% → huge edge buffer
- Split-half: +5.9¢ H1, +5.3¢ H2 (perfectly stable)
- Profit factor: 3.70
- Exit breakdown: 39% TP, 41% SL, 20% deadline

### Bot Architecture (`bot.py`)

**Data flow**: feeds.py (Binance + PM prices) → strategy.py (signal evaluation + exit check) → execution.py (entry/exit)

**Snapshot collection**: Starting at minute 2.5, the bot captures PM Up price every ~30 seconds. These snapshots feed into `strategy.evaluate()`.

**Entry**: When `strategy.evaluate()` fires a signal at ~minute 5, `execution.py` places a maker-only order.

**Exit monitoring**: Every 2 seconds, `strategy.check_exit()` compares current PM price against TP/SL/deadline. When triggered, `execution.exit_position()` closes the trade at current market price. No oracle/settlement dependency.

**Fallback**: If somehow a position is still open at period boundary (shouldn't happen with deadline at min 14), it's force-closed at market price.

**Execution modes**:
- `PaperExecutor`: Logs trades to `logs/trades_YYYY-MM-DD.jsonl`, computes theoretical P&L based on current PM prices
- `LiveExecutor`: Places real orders via `py-clob-client` (exit sell orders TBD — requires CLOB sell implementation)

### Other Strategies Tested (Backtest Results)

From `scripts/backtest_scalp.py` (1818 configs, 6 families):

| Family | Best t-stat | Trades | Win% | PnL/trade | Viable? |
|--------|------------|--------|------|-----------|---------|
| **momentum_scalp** | **+8.18** | 185 | 58% | +5.6¢ | ✅ **Deployed** |
| early_cheap | +5.81 | 195 | 37% | +3.6¢ | ✅ Potential |
| support_resistance | +3.75 | 181 | 36% | +1.8¢ | ✅ Weak |
| range_bound | +3.44 | 220 | 34% | +1.4¢ | ✅ Weak |
| mean_reversion | +1.56 | 93 | 31% | +1.0¢ | ⚠️ Marginal |
| vol_spike | -1.24 | 29 | 10% | -4.2¢ | ❌ Negative |

### Deprecated: Chainlink-Anchored Passive Maker

Originally planned strategy (from Claude + Codex analysis). **Abandoned** after mark-out analysis proved it loses money due to adverse selection. Kept for reference only.

### Mark-out Analysis Results (Feb 6, 2026)

**Script**: `scripts/markout.py` — analyzes 79K+ trades across 20 resolved periods (24h sample)

**Key Findings**:
1. **Passive market-making loses money**: Maker markout is -1.1c/trade at 60s. Buyer flow is informed (59.7% on winning side vs 50% random). Unconditional liquidity provision = negative expectation.
2. **Model edge filter makes it WORSE**: Selling when model says overpriced → markout goes from -1.1c to -3.8c. The model's directional prediction is wrong more often than PM prices.
3. **PM prices predict outcomes better than our model**: PM correct 95% vs model 90% on direction. The model systematically overestimates FV(Up).
4. **Model calibration is poor**: When model says 70% FV, actual win rate is 95%. When it says 30%, actual is 7%. The model lags behind reality.
5. **Settlement P&L for buying underpriced contracts**: Per-trade avg is positive (+2c at edge>3c threshold) but per-period analysis shows 50% win rate with high variance. The aggregate stat is misleading due to correlated trades within periods.
6. **Early-period entries have best settlement P&L**: 0-5 min entries: +3.1c avg P&L. 10-15 min entries: +0.3c avg P&L. Confirms early mispricing exists but data is limited (API caps at 4000 trades/period, missing first ~3 min).

**Data Limitations**:
- `data-api.polymarket.com/trades` caps at 4000 trades per market (newest-first)
- Missing first 3-5 minutes of each period (where mispricing is theorized to be largest)
- 24h sample (20 periods) is statistically thin; need 100+ for confidence
- CLOB trades endpoint requires auth; no alternative for full trade history

**Implications for Strategy**:
- Pure passive maker strategy (the Codex "Chainlink-Anchored Passive Maker") is **not viable** — adverse selection dominates
- Model-based edge filtering doesn't help because model direction is less accurate than PM prices
- The user's actual strategy (manual mean reversion on early-period mispricing) may work but can't be validated with available data (missing early-period trades)
- The model is useful as a **dashboard indicator** but should NOT be used as an automated trading signal

### Momentum Strategy Backtest (Feb 6, 2026)

**Script**: `scripts/backtest_momentum.py` — tests momentum strategy using `clob.polymarket.com/prices-history` endpoint (full minute 0-15 coverage, no 4000-trade cap).

**Data**: 257 resolved periods across 96 hours, 100% coverage from minute 0.

**Strategy**: At minute M, if PM Up price > threshold → buy Up. If PM Down price > threshold → buy Down. Hold to settlement.

**Key Findings**:
1. **PM prices ARE slightly mispriced** — buying the leading side at higher thresholds produces win rates above entry prices (e.g., buying at 0.85 when actual win rate is 0.88). Edge is 1-4 cents per trade.
2. **Best combo by t-stat**: (7-10m, ≥0.80) → 93.3% win, +3.6c/trade, t=+1.67, PF=1.61, n=135
3. **Best combo by total P&L**: (2-5m, ≥0.55) → 71.2% win, +2.6c/trade, n=215, total +$5.49
4. **Most robust (stable across halves)**: (7-10m, ≥0.65) → 85.7% win, +1.5c/trade both halves, n=203
5. **NOT statistically significant**: Best t-stat is +1.67 (uncorrected), well below Bonferroni threshold of 3.84 for 40 comparisons. No combo passes multiple-testing correction at 5%.
6. **48h vs 96h degradation**: (3-7m, ≥0.60) was t=+2.16 at 48h but dropped to t=+0.39 at 96h — classic overfitting signal.
7. **Earlier entry = lower prices = more edge**: Min 3-7 at threshold 0.70 has edge=+0.040 (win rate 85.6% vs avg entry 81.6%). Later entries (10-13m) are approximately fairly priced.
8. **PM prices are efficient late-period**: At minutes 10-13, PM prices match actual win rates almost exactly (edge ≈ 0).

**Honest Assessment**:
- There MAY be a small edge (~2-4c/trade) in buying the leading side at moderate thresholds during minutes 3-7
- The edge is NOT proven statistically — need 500+ periods or a pre-specified hypothesis
- Win rates are high (85-93%) but so are entry prices (0.80-0.90), making net P&L thin
- The strategy survived split-half testing for several combos but t-stats are weak
- Real execution would face: slippage, missed fills (maker), adverse selection on fills
- The prices-history endpoint gives ~1 price/minute fidelity — real execution prices may differ

**Data Sources**:
- `clob.polymarket.com/prices-history?market={token}&startTs=&endTs=&fidelity=1` — full period coverage from minute 0
- `polymarket.com/api/past-results` — oracle outcomes and strikes
- `gamma-api.polymarket.com/events` — token IDs per period

### Multi-Strategy Backtest (Feb 6, 2026)

**Script**: `scripts/backtest_all_strategies.py` — tests 168 configurations across 9 strategy families.

**Strategy families tested**:
1. **Momentum** (baseline) — buy leading side if price > threshold
2. **Mean reversion** — buy losing side (TERRIBLE: 25% win rate)
3. **Trend confirmation** — momentum + price must be trending
4. **Trend reversal** — detect leader flip, buy new leader
5. **Volatility filter** — momentum + vol constraint
6. **Acceleration** — buy when price accelerating
7. **Multi-timepoint** — same side must lead at ALL checkpoints
8. **Price distance** — momentum + must have moved from open
9. **Extreme value** — buy cheap underdogs (barely breaks even)

**Results** (276 periods):
- **Trend reversal** #1 by t-stat (+2.34) but rare (31 trades)
- **Trend confirmation** #2 (t=+1.56, n=198, stable in both halves)
- Simple momentum only #4 (t=+1.22)
- Combined strategy (trend_confirm + reversal) = best overall: t=+1.90, n=214

### Open Questions (updated Feb 7)
- **Is the edge real?**: t=+1.90 is close to 1.96 significance (uncorrected) but not after multiple-testing correction. Live validation is the only true test.
- **Execution risk**: Backtest uses prices-history mid-prices. Real fills (maker) may be worse.
- **Model calibration**: Systematically overestimates FV(Up). Useful as dashboard indicator, not trading signal.
- **Regime dependence**: Edge may vary with BTC volatility regime.

### Implementation Priority (revised Feb 8 midnight)
1. ~~Mark-out analysis script~~ ✅ DONE
2. ~~Prices-history momentum backtest~~ ✅ DONE — inconclusive (possible edge, not proven)
3. ~~Multi-strategy backtest (168 configs)~~ ✅ DONE — combined strategy is best
4. ~~Build automated bot v1~~ ✅ DONE — deployed to Railway
5. ~~v1 post-mortem~~ ✅ DONE — 7W/12L (-$98), P&L asymmetry killed it
6. ~~Scalping backtest (1818 configs, 6 families)~~ ✅ DONE — momentum scalp +5.6¢/trade, t=+8.18
7. ~~Build + deploy v2 (Momentum Scalp with TP/SL)~~ ✅ DONE — running on Railway
8. **NOW**: Paper trading validation v2 — monitor 12+ hours
9. If validated: switch to live mode with small size ($10/trade)
10. Consider adding early_cheap as secondary strategy (t=+5.81)

## Volatility Interpretation (annualized Yang-Zhang)
- **20-35%**: Low vol. FV near 0.50. Little edge.
- **35-55%**: Normal. Best for model-based trading.
- **55-80%**: High vol. Mean reversion works well but size down.
- **80-120%+**: Extreme. Be cautious, prices can trend.

Note: These buckets are uncalibrated intuition. Need to validate against actual past-results data.

## Known Issues / Future Work
- **PM WebSocket unreliable on Railway**: Consistent 1011 keepalive ping timeouts every 2-3min. Disabled in headless mode; REST poller (`pm_price_poller`, 10s interval) is the sole PM price source on Railway.
- **PM REST prices are "sticky"**: Gamma API `outcomePrices` can return the same price for minutes. This affects both entry signals (p5 > p3 check) and TP/SL exit detection. A real 15¢ move may not be captured until minutes later. Consider shorter poll interval or alternative data source.
- **TP/SL exit detection may lag**: With 10s REST poll interval and sticky prices, a TP or SL exit may be triggered later than optimal. In the worst case, a 5¢ stop loss could become a larger loss if the price moved past -5¢ between polls. The deadline exit at minute 14 provides a hard floor.
- **Binance WebSocket disconnects on Railway**: Gets 1011 every 2-3min but reconnects instantly (3s delay). Functional but noisy in logs.
- Only 15m timeframe has full fair value support (oracle strike + period tracking)
- S uses Binance mid as proxy for live Chainlink price — introduces ~$50-150 basis error
- Need tracking dashboard: cumulative P&L, rolling win rate, edge at entry vs outcome, profit factor by hour
- `clob.polymarket.com/prices-history` is the best data source for backtesting (full coverage from min 0, no 4000-trade cap)
- `data-api.polymarket.com/trades` caps at 4000 trades per market, misses first 3-5 min of each period

### Settlement API Behavior (Critical Knowledge)
The `polymarket.com/api/past-results` API has specific behavior that caused a critical bug:
- **Returns results that ended BEFORE `currentEventStartTime`**, not after
- **~15min reporting delay** — a period that ended at :15 may not appear until :30
- **Returns only ~3-4 results per query**
- **Correct approach**: Query with `currentEventStartTime` set to the current period (or later), not the target period + 900s
- **Implementation**: `fetch_period_outcome()` in `bot.py` tries 3 different timestamps to maximize coverage

## Corrections Log
- **Feb 6**: Fixed PM prices showing 0.01/1.00 — added REST fallback poller
- **Feb 6**: Fixed strike using Binance instead of Chainlink oracle — now fetches from past-results API
- **Feb 6**: Fixed edge display showing absurd 48%/50% — added PM price sanity check
- **Feb 6**: Removed @SolSt1ne credit from dashboard header per user request
- **Feb 6**: Corrected fee assumptions — Polymarket charges 0% maker fees (+ rebates), taker fees only on 15m markets (0.2%-1.56% scaling with probability)
- **Feb 6**: Mark-out analysis revealed: passive maker strategy loses money due to adverse selection; model direction is less accurate than PM prices (90% vs 95%); model systematically overestimates FV(Up)
- **Feb 6**: Aggregate per-trade statistics are misleading — must analyze at per-period level due to within-period correlation. 23K trades showing +2c/trade edge becomes 50% win rate at period level.
- **Feb 6**: Discovered `clob.polymarket.com/prices-history` endpoint — provides full minute 0-15 price data, bypassing the 4000-trade cap on data-api
- **Feb 6**: 96h momentum backtest (257 periods) shows possible edge at (3-7m, ≥0.70) and (7-10m, ≥0.80) but NOT statistically significant after multiple-testing correction. Best t-stat +1.67 vs Bonferroni threshold 3.84.
- **Feb 6**: Earlier 48h results (t=+2.16 for 3-7m/0.60) degraded to t=+0.39 at 96h — classic overfitting/small-sample issue. Always validate on larger samples.
- **Feb 6**: Fixed oracle period enumeration bug — was stepping hourly, but past-results API returns ~4 results per query. Changed to 15-minute stepping, finding 89/96 periods per day (93%) vs 45 with hourly stepping.
- **Feb 6**: Multi-strategy backtest (168 configs, 9 families) showed: mean reversion is terrible (25% win), trend reversal is best single signal (t=+2.34 but rare), trend confirmation is most reliable.
- **Feb 7**: Built automated trading bot (`bot.py`) with combined strategy, paper/live execution modes, trade logging to `logs/` directory.
- **Feb 7**: Deployed bot to Railway with `Procfile` (headless mode). Fixed multiple production issues:
  - PM WebSocket stale token IDs after period change (moved assets read inside reconnect loop)
  - PM WebSocket not detecting period changes mid-connection (added token ID change detection + recv timeout)
  - Disabled PM WebSocket in headless mode — REST poller is more reliable on Railway
  - Python stdout buffering on Railway (`python -u` flag + `flush=True` on critical prints)
  - Heartbeat logging gaps (changed from modulo arithmetic to time-based tracking)
  - **CRITICAL**: Settlement timing out 50% of trades — `fetch_period_outcome()` was querying oracle API with `currentEventStartTime = period_start_ts + 900` but the API returns results that ended BEFORE that timestamp with ~15min delay. Fixed by querying multiple timestamps.
  - Settlement timeout increased from 2min → 10min, verbose logging throttled to every 30s
  - Oracle strike fetch retry (3 attempts, 2s delay) on period change
- **Feb 7**: v1 bot post-mortem: 7W/12L, -$98.18. Root cause: P&L asymmetry from settlement-based exits. Avg win +$3.12 vs avg loss -$9.15. Required 76% win rate, got 37%. Signal quality was irrelevant.
- **Feb 8**: Built scalping backtest (`scripts/backtest_scalp.py`) with `simulate_exit()` function. Tests within-period TP/SL exits instead of settlement. 6 strategy families, 1818 configs across 96h.
- **Feb 8**: Momentum Scalp dominates — top 25 strategies ALL from this family. Best: t=+8.18, 58% win, +5.6¢/trade, avg win +13.3¢ vs avg loss -4.9¢. Split-half stable (+5.9¢ / +5.3¢).
- **Feb 8**: Deployed Momentum Scalp v2 to Railway. Strategy enters at min 5 (trending from min 3), exits via TP(+15¢)/SL(-5¢)/deadline(min 14). No settlement dependency.
