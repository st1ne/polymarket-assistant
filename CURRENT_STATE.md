# Polymarket Assistant - Current State

**Last updated:** Saturday February 8, 2026, ~12:00 AM KST

## What Works ✅
- Dashboard with fair value, order book, flow, TA panels (`main.py`)
- Oracle strike fetching from Chainlink via `past-results` API
- Fair value calculator (Black-Scholes binary option pricing)
- **Momentum Scalp v2 bot** with TP/SL exits (`bot.py`)
- Trade logging to `logs/trades_YYYY-MM-DD.jsonl`
- PM price snapshots every ~30s for strategy evaluation
- Period change detection + auto-fetch oracle strike + PM tokens
- Live execution via `py-clob-client` (maker-only, post_only=True)
- Backtesting suite: 1818 scalp configs across 6 strategy families
- **Deployed to Railway** — running 24/7 in headless mode
- Crash resilience: auto-restart on fatal error + Railway restart policy

## Strategy: Momentum Scalp v2

**Architecture change: exit BEFORE settlement via TP/SL.**

Parameters (from backtest `mom_m5_lb2_60%_tp0.15_sl0.05`):
- **Entry:** minute 5, leading side >= 0.60, price trending from minute 3
- **Take Profit:** +15¢ (sell when price rises 15¢ above entry)
- **Stop Loss:** -5¢ (cut losses when price drops 5¢ from entry)
- **Deadline:** exit at market price at minute 14

Backtest results (96h, 294 periods):
- 185 trades, 58% win rate, +5.6¢/trade
- t-stat: +8.18 (extremely significant)
- Avg win: +13.3¢, Avg loss: -4.9¢ (2.7:1 reward/risk)
- Breakeven win rate: 27%, actual: 58%
- Split-half: +5.9¢ H1, +5.3¢ H2 (stable)
- Profit factor: 3.70

### Why This Is Better Than v1
The old strategy held to binary settlement (0 or 1), creating terrible P&L asymmetry:
- Old: avg win +3.1¢ vs avg loss -10¢ → needed 76% win rate, got 37%
- New: avg win +13.3¢ vs avg loss -4.9¢ → needed 27% win rate, got 58%

## What Doesn't Work ❌
- PM WebSocket unreliable on Railway — disabled in headless mode, using REST poller
- PM REST poller prices are "sticky" (same value for minutes) — may cause missed signals
- Vol spike strategies don't work (10% win rate, negative edge)

## In Progress 🔄
- **Paper trading Momentum Scalp v2 on Railway** — deployed, collecting trades
- Current P&L: starting fresh at $0.00

## Recent Work (Saturday Night Session — Strategy Overhaul)

### Post-mortem on v1 bot (7W/12L, -$98.18)
- Diagnosed P&L asymmetry as root cause: avg win +$3.12 vs avg loss -$9.15
- Settlement-based exits create terrible risk/reward at high entry prices
- Strategy signal quality was irrelevant — the exit mechanism was broken

### Strategy redesign
- Designed 6 new strategy families with within-period TP/SL exits
- Built comprehensive backtest engine (`scripts/backtest_scalp.py`) with `simulate_exit()`
- Tested 1818 parameter combinations across 96h of data
- Momentum Scalp dominated: top 25 strategies ALL from this family
- Implemented new strategy in bot with `check_exit()` loop

### Commits (Saturday night)
1. `Add crash resilience: auto-restart on fatal error, Railway restart policy`
2. `Momentum Scalp v2: exit via TP/SL instead of settlement`

## Next Steps
- [ ] Monitor paper trading for 12+ hours
- [ ] Verify TP/SL exits are firing correctly in production
- [ ] Check if PM REST price stickiness affects exit detection
- [ ] If validated: run with `--live --size 10`
- [ ] Consider adding early_cheap as a secondary strategy (t=+5.81)

## Context for Next Session
Bot is running Momentum Scalp v2 on Railway. The critical architectural change is **exiting within the period** via TP/SL instead of holding to binary settlement. The backtest shows this completely fixes the P&L asymmetry that destroyed v1. Key risk: PM REST prices may be too sticky for reliable TP/SL detection (prices update every 10s via REST poller, but PM Gamma `outcomePrices` sometimes holds the same value for minutes). If exits seem delayed or missed, consider: (1) shorter poll interval, (2) Binance-derived fair value as exit trigger, (3) PM WebSocket for more responsive prices.
