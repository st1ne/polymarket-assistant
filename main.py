import sys
import os
import asyncio
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from rich.console import Console
from rich.live   import Live

import config
import feeds
import dashboard
import edge_finder

console = Console(force_terminal=True)


def pick(title: str, options: list[str]) -> str:
    console.print(f"\n[bold]{title}[/bold]")
    for i, o in enumerate(options, 1):
        console.print(f"  [{i}] {o}")
    while True:
        raw = input("  → ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        console.print("  [red]invalid – try again[/red]")


async def display_loop(state: feeds.State, coin: str, tf: str):
    await asyncio.sleep(2)
    refresh_interval = config.REFRESH_5M if tf == "5m" else config.REFRESH
    with Live(console=console, refresh_per_second=1, transient=False) as live:
        while True:
            if state.mid > 0 and state.klines:
                live.update(dashboard.render(state, coin, tf))
            await asyncio.sleep(refresh_interval)


def parse_args():
    ap = argparse.ArgumentParser(description="Polymarket crypto dashboard")
    ap.add_argument("--coin", type=str.upper, choices=config.COINS)
    ap.add_argument("--tf", choices=sorted({t for v in config.COIN_TIMEFRAMES.values() for t in v}))
    return ap.parse_args()


async def main():
    args = parse_args()
    console.print("\n[bold magenta]═══ CRYPTO PREDICTION DASHBOARD ═══[/bold magenta]\n")

    coin = args.coin or pick("Select coin:", config.COINS)
    tf   = args.tf   or pick("Select timeframe:", config.COIN_TIMEFRAMES[coin])

    console.print(f"\n[bold green]Starting {coin} {tf} …[/bold green]\n")

    await asyncio.to_thread(feeds.select_binance_endpoint)

    state       = feeds.State()
    state.resolves_on = config.TF_RESOLVES_ON[tf]
    binance_sym = config.COIN_BINANCE[coin]
    kline_iv    = config.TF_KLINE[tf]
    console.print("  [Binance] bootstrapping candles …")
    await feeds.bootstrap(binance_sym, kline_iv, state)

    await asyncio.gather(
        feeds.market_manager(coin, tf, binance_sym, state),
        feeds.ob_poller(binance_sym, state),
        feeds.binance_feed(binance_sym, kline_iv, state),
        feeds.pm_feed(state),
        feeds.chainlink_feed(coin, state),
        edge_finder.run_recorder(state, coin, tf),
        display_loop(state, coin, tf),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
