#!/usr/bin/env python3
"""
Polymarket BTC 15m Up/Down automated trading bot.

Combined strategy: Trend Confirmation + Trend Reversal
- At minute 7, check for leader flip since minute 5 → REVERSAL signal
- If no flip, check trend confirmation (leader >= 0.60 AND trending from min 4) → TREND_CONFIRM
- Maker-only execution (0% fees + rebates)

Usage:
  # Paper trading (default — logs signals, no real orders):
  python bot.py

  # Paper trading with custom size:
  python bot.py --size 25

  # Live trading (requires PM_PRIVATE_KEY and PM_FUNDER env vars):
  python bot.py --live --size 10

  # Specify coin (default: BTC):
  python bot.py --coin ETH
"""

import sys
import os
import asyncio
import argparse
import time
import json
import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box as bx
from datetime import datetime, timezone

import config
import feeds
import strategy
import execution

console = Console(force_terminal=True)


# ── Outcome detection ────────────────────────────────────────

def fetch_period_outcome(coin: str, period_start_ts: int) -> str | None:
    """Check if a period has resolved and return 'up' or 'down' (or None if pending)."""
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(period_start_ts, tz=timezone.utc)
    start_iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Query past-results for the period AFTER this one (which would contain our result)
    next_period_ts = period_start_ts + 900
    dt_next = datetime.fromtimestamp(next_period_ts, tz=timezone.utc)
    next_iso = dt_next.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        resp = requests.get(config.PM_PAST_RESULTS, params={
            "symbol": coin,
            "variant": "fifteen",
            "assetType": "crypto",
            "currentEventStartTime": next_iso,
        }, timeout=5, headers={"User-Agent": "Mozilla/5.0"})

        if resp.status_code == 200:
            data = resp.json()
            for r in data.get("data", {}).get("results", []):
                st = r.get("startTime", "")
                if st.startswith(start_iso.replace("Z", "")):
                    return r.get("outcome")  # "up" or "down"
    except Exception:
        pass
    return None


# ── Bot dashboard ────────────────────────────────────────────

def render_bot_dashboard(
    state: feeds.State,
    strat_state: strategy.StrategyState,
    executor: execution.PaperExecutor | execution.LiveExecutor,
    coin: str,
    mode: str,
) -> Panel:
    """Render the bot status panel."""
    bot = executor.bot

    t = Table(box=None, show_header=False, pad_edge=False, expand=True)
    t.add_column("label", style="dim", width=22)
    t.add_column("value", width=20)
    t.add_column("detail", width=36)

    # Mode
    mode_style = "bold red" if mode == "LIVE" else "bold yellow"
    t.add_row("Mode", f"[{mode_style}]{mode}[/{mode_style}]",
              f"${executor.size_usd:.0f}/trade")

    # Period timer
    if state.period_start_ts > 0:
        remaining = max(0, state.period_end_ts - time.time())
        elapsed = 900 - remaining
        mins = int(remaining) // 60
        secs = int(remaining) % 60
        pct = elapsed / 900
        bar_len = int(pct * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        minute = elapsed / 60
        t.add_row("Period", f"[bold]{mins:02d}:{secs:02d}[/bold] left",
                  f"[cyan]{bar}[/cyan] min {minute:.1f}")
    else:
        t.add_row("Period", "[dim]waiting…[/dim]", "")

    # PM prices
    if state.pm_up is not None and state.pm_dn is not None:
        pm_sane = 0.02 < state.pm_up < 0.98
        pm_text = f"↑ {state.pm_up:.3f}  ↓ {state.pm_dn:.3f}"
        if pm_sane:
            t.add_row("PM Prices", pm_text, "")
        else:
            t.add_row("PM Prices", f"[yellow]{pm_text}[/yellow]",
                      "[yellow]stale[/yellow]")
    else:
        t.add_row("PM Prices", "[dim]waiting…[/dim]", "")

    # Strategy snapshots
    n_snaps = len(strat_state.snapshots)
    snap_info = ""
    if n_snaps > 0:
        last = strat_state.snapshots[-1]
        snap_info = f"last: min {last.minute:.1f} = {last.price_up:.3f}"
    t.add_row("Snapshots", f"{n_snaps} captured", snap_info)

    # Current signal / position
    if bot.current_side:
        last_trade = bot.trades[-1] if bot.trades else None
        sig_type = last_trade.signal_type if last_trade else "?"
        entry = last_trade.entry_price if last_trade else 0
        t.add_row("Position",
                  f"[bold green]{bot.current_side}[/bold green] @ {entry:.3f}",
                  f"[cyan]{sig_type}[/cyan]")
    elif strat_state.signal_fired:
        t.add_row("Position", "[dim]signal fired, settled[/dim]", "")
    else:
        t.add_row("Position", "[dim]waiting for signal…[/dim]", "")

    t.add_row("[dim]────────────────[/dim]", "[dim]────────────────[/dim]", "[dim]────────────────[/dim]")

    # P&L summary
    wr = bot.win_rate
    wr_col = "green" if wr > 0.5 else "red" if wr < 0.5 else "yellow"
    pnl_col = "green" if bot.total_pnl > 0 else "red" if bot.total_pnl < 0 else "yellow"

    t.add_row("Total Trades", f"{bot.total_trades}",
              f"[{wr_col}]{wr:.0%} win rate[/{wr_col}]" if bot.total_trades > 0 else "")
    t.add_row("W / L", f"[green]{bot.wins}[/green] / [red]{bot.losses}[/red]", "")
    t.add_row("Total P&L",
              f"[{pnl_col}]${bot.total_pnl:+.2f}[/{pnl_col}]",
              f"[dim]{bot.total_pnl / max(bot.total_trades, 1):+.2f}/trade[/dim]" if bot.total_trades > 0 else "")

    # Last 5 trades
    if bot.trades:
        t.add_row("[dim]────────────────[/dim]", "[dim]────────────────[/dim]", "[dim]────────────────[/dim]")
        t.add_row("[bold]Recent Trades[/bold]", "", "")
        for trade in bot.trades[-5:]:
            outcome_str = ""
            if trade.outcome == "won":
                outcome_str = f"[green]✓ +${trade.pnl:+.2f}[/green]"
            elif trade.outcome == "lost":
                outcome_str = f"[red]✗ ${trade.pnl:+.2f}[/red]"
            else:
                outcome_str = "[yellow]pending[/yellow]"
            t.add_row(
                f"  {trade.side} @ {trade.entry_price:.3f}",
                f"[dim]{trade.signal_type}[/dim]",
                outcome_str,
            )

    title_style = "red bold" if mode == "LIVE" else "yellow bold"
    return Panel(t, title=f"[{title_style}]🤖 BOT — {coin} 15m — {mode}[/{title_style}]",
                 box=bx.DOUBLE, expand=True)


# ── Core bot loop ────────────────────────────────────────────

async def bot_loop(
    state: feeds.State,
    executor: execution.PaperExecutor | execution.LiveExecutor,
    coin: str,
    strat_state: strategy.StrategyState,
):
    """Main bot logic: snapshot collection, strategy evaluation, trade execution."""
    last_period_ts = 0
    last_snapshot_minute = -1
    pending_settlement_ts = 0

    # Wait for feeds to populate
    await asyncio.sleep(5)
    print("  [BOT] strategy loop started", flush=True)

    while True:
        try:
            now = time.time()

            # ── Period change detection ──
            if state.period_start_ts != last_period_ts and state.period_start_ts > 0:
                # If we had a position from the previous period, settle it
                if last_period_ts > 0 and executor.bot.current_side:
                    pending_settlement_ts = last_period_ts

                # Reset strategy state for new period
                strat_state.reset(state.period_start_ts)
                last_period_ts = state.period_start_ts
                last_snapshot_minute = -1
                print(f"\n  [BOT] new period: {state.period_start_ts} "
                      f"| strike={'${:,.2f}'.format(state.strike) if state.strike else '?'}",
                      flush=True)

            # ── Settlement check (wait a bit after period ends for oracle) ──
            if pending_settlement_ts > 0:
                # Wait 30 seconds after period end for oracle to report
                period_end = pending_settlement_ts + 900
                if now > period_end + 30:
                    outcome = fetch_period_outcome(coin, pending_settlement_ts)
                    if outcome:
                        executor.settle(outcome)
                        bot = executor.bot
                        result = "✅ WON" if bot.trades[-1].outcome == "won" else "❌ LOST"
                        pnl = bot.trades[-1].pnl or 0
                        print(f"  [BOT] settlement: {result} (${pnl:+.2f}) | "
                              f"cumulative: ${bot.total_pnl:+.2f} | "
                              f"{bot.wins}W/{bot.losses}L ({bot.win_rate:.0%})",
                              flush=True)
                        pending_settlement_ts = 0
                    elif now > period_end + 120:
                        # Give up after 2 minutes — oracle may not be available
                        print(f"  [BOT] settlement timeout for period {pending_settlement_ts}, skipping")
                        executor.cancel()
                        pending_settlement_ts = 0

            # ── Snapshot collection ──
            if state.period_start_ts > 0 and state.pm_up is not None:
                elapsed = now - state.period_start_ts
                current_minute = elapsed / 60.0

                # Collect snapshots every ~30 seconds starting at minute 3.5
                if (current_minute >= strategy.MIN_SNAPSHOT_MINUTE and
                    current_minute <= 14.0 and
                    current_minute - last_snapshot_minute >= 0.4):

                    pm_sane = 0.02 < state.pm_up < 0.98
                    if pm_sane:
                        strat_state.add_snapshot(current_minute, state.pm_up)
                        last_snapshot_minute = current_minute

            # ── Strategy evaluation ──
            if not strat_state.signal_fired and state.period_start_ts > 0:
                signal = strategy.evaluate(strat_state)
                if signal:
                    print(f"\n  [BOT] 🎯 SIGNAL: {signal.signal.value} → {signal.side} "
                          f"@ {signal.entry_price:.3f} | {signal.confidence}",
                          flush=True)

                    # Execute!
                    up_id = state.pm_up_id
                    dn_id = state.pm_dn_id
                    if up_id and dn_id:
                        rec = executor.execute(signal, up_id, dn_id)
                        print(f"  [BOT] trade logged: {rec.status} | "
                              f"{rec.side} @ {rec.entry_price:.3f} | "
                              f"${rec.size_usd:.0f}",
                              flush=True)
                    else:
                        print(f"  [BOT] ⚠ no PM token IDs — signal skipped")

        except Exception as e:
            print(f"  [BOT] error: {e}")

        await asyncio.sleep(2)  # check every 2 seconds


async def display_loop(
    state: feeds.State,
    strat_state: strategy.StrategyState,
    executor: execution.PaperExecutor | execution.LiveExecutor,
    coin: str,
    mode: str,
):
    """Render the bot dashboard periodically (Rich terminal)."""
    await asyncio.sleep(3)
    with Live(console=console, refresh_per_second=1, transient=False) as live:
        while True:
            try:
                if state.mid > 0:
                    panel = render_bot_dashboard(state, strat_state, executor, coin, mode)
                    live.update(panel)
            except Exception as e:
                print(f"  [DISPLAY] render error: {e}")
            await asyncio.sleep(2)


async def headless_loop(
    state: feeds.State,
    strat_state: strategy.StrategyState,
    executor: execution.PaperExecutor | execution.LiveExecutor,
    coin: str,
    mode: str,
):
    """Headless status logger for Railway/server deployment (plain stdout)."""
    await asyncio.sleep(5)
    last_period = 0
    while True:
        try:
            bot = executor.bot
            now = time.time()

            # Log on period change
            if state.period_start_ts != last_period and state.period_start_ts > 0:
                last_period = state.period_start_ts
                remaining = max(0, state.period_end_ts - now)
                pm_str = f"↑{state.pm_up:.3f} ↓{state.pm_dn:.3f}" if state.pm_up else "?"
                print(f"[{mode}] period {state.period_start_ts} | "
                      f"PM: {pm_str} | "
                      f"snaps: {len(strat_state.snapshots)} | "
                      f"pos: {bot.current_side or 'none'} | "
                      f"P&L: ${bot.total_pnl:+.2f} ({bot.wins}W/{bot.losses}L)",
                      flush=True)

            # Periodic heartbeat every 60s
            elif int(now) % 60 < 3 and state.period_start_ts > 0:
                elapsed = (now - state.period_start_ts) / 60
                pm_str = f"↑{state.pm_up:.3f} ↓{state.pm_dn:.3f}" if state.pm_up else "?"
                print(f"[{mode}] min {elapsed:.1f} | PM: {pm_str} | "
                      f"snaps: {len(strat_state.snapshots)} | "
                      f"pos: {bot.current_side or 'none'}",
                      flush=True)

        except Exception as e:
            print(f"[HEADLESS] error: {e}", flush=True)

        await asyncio.sleep(3)


# ── Main ─────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Polymarket BTC 15m trading bot")
    parser.add_argument("--coin", default="BTC", choices=config.COINS,
                        help="Coin to trade (default: BTC)")
    parser.add_argument("--size", type=float, default=10.0,
                        help="Trade size in USD (default: 10)")
    parser.add_argument("--live", action="store_true",
                        help="Enable live trading (requires PM_PRIVATE_KEY and PM_FUNDER)")
    parser.add_argument("--headless", action="store_true",
                        help="Headless mode (plain stdout, no Rich dashboard — for server deployment)")
    args = parser.parse_args()

    tf = "15m"  # only supported timeframe
    mode = "LIVE" if args.live else "PAPER"
    log = print if args.headless else lambda *a, **kw: console.print(*a, **kw)

    log(f"\n═══ POLYMARKET TRADING BOT ═══")
    log(f"  Coin: {args.coin}")
    log(f"  Mode: {mode}")
    log(f"  Size: ${args.size:.0f}/trade")
    log(f"  Strategy: Combined (trend_confirm + reversal)")
    log(f"  Display: {'headless' if args.headless else 'Rich dashboard'}")
    log()

    # Initialize executor
    if args.live:
        try:
            executor = execution.LiveExecutor(size_usd=args.size)
        except ValueError as e:
            log(f"Error: {e}")
            log("Set PM_PRIVATE_KEY and PM_FUNDER environment variables")
            sys.exit(1)
    else:
        executor = execution.PaperExecutor(size_usd=args.size)
        log("  Paper trading mode — no real orders will be placed")

    # Initialize feeds state
    state = feeds.State()
    strat_state = strategy.StrategyState()

    # Fetch initial PM tokens
    state.pm_up_id, state.pm_dn_id = feeds.fetch_pm_tokens(args.coin, tf)
    if state.pm_up_id:
        log(f"  [PM] Up   → {state.pm_up_id[:24]}…")
        log(f"  [PM] Down → {state.pm_dn_id[:24]}…")
    else:
        log("  [PM] no market found — will retry on period change")

    # Bootstrap Binance data
    binance_sym = config.COIN_BINANCE[args.coin]
    kline_iv = config.TF_KLINE[tf]
    log("  [Binance] bootstrapping candles…")
    await feeds.bootstrap(binance_sym, kline_iv, state)

    log(f"\n  Bot running. Waiting for first signal…\n")

    # Choose display mode
    if args.headless:
        display_task = headless_loop(state, strat_state, executor, args.coin, mode)
    else:
        display_task = display_loop(state, strat_state, executor, args.coin, mode)

    # Run all tasks concurrently
    await asyncio.gather(
        feeds.ob_poller(binance_sym, state),
        feeds.binance_feed(binance_sym, kline_iv, state),
        feeds.pm_feed(state),
        feeds.pm_price_poller(state, args.coin, tf),
        feeds.period_tracker(state, args.coin, tf),
        bot_loop(state, executor, args.coin, strat_state),
        display_task,
    )


if __name__ == "__main__":
    asyncio.run(main())
