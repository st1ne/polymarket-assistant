"""Print calibration and resolution-held signal results from the read-only ledger."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import config
from edge_finder import EdgeLedger


def print_timeframe(ledger, timeframe):
    summary = ledger.summary(timeframe)
    print(f"\n══ {timeframe} markets (bucket width {summary['bucket_seconds']}s) ══")
    print(f"Forecasts: {summary['n']}")
    feed = summary["feed"]
    parts = [f"{label} {feed[key]['n']} (Brier {feed[key]['brier_model']:.4f})"
             for key, label in (("live", "live"), ("stale", "blocked stale-price"),
                                ("legacy", "pre-check legacy")) if key in feed]
    print("Feed check: " + " · ".join(parts))
    if "stale" in feed:
        print("  Blocked stale-price rows are excluded from every score below.")
    if "legacy" in feed:
        print("  Legacy rows predate the staleness check and may include frozen-price periods.")
    if not summary["n"]:
        print("  No live resolved forecasts to score yet.")
        return
    print(f"Overall Brier: model {summary['brier_model']:.4f} | "
          f"market midpoint {summary['brier_market']:.4f}" if summary["brier_market"] is not None
          else f"Overall Brier: model {summary['brier_model']:.4f} | market midpoint unavailable")
    print("\nBy time remaining (lower Brier is better):")
    print("  seconds left     n     model     market")
    for row in ledger.horizon_scores(timeframe):
        lo, hi = row["range_seconds"]
        market = "—" if row["brier_market"] is None else f"{row['brier_market']:.4f}"
        print(f"  {lo:5d}–{hi:<5d}     {row['n']:4d}   {row['brier_model']:.4f}   {market}")
    pnl = summary["candidate_pnl"]
    print("\nFirst qualifying signal per resolved market:")
    print(f"  markets: {summary['candidate_n']}")
    print(f"  {config.EDGE_PROBE_SHARES:g}-share-per-market total: {('—' if pnl is None else f'${pnl:+.3f}')}")


def main():
    ledger = EdgeLedger(config.EDGE_DB_PATH)
    print(f"Ledger: {config.EDGE_DB_PATH}")
    timeframes = ledger.timeframes()
    if not timeframes:
        print("No resolved forecasts yet. Run main.py and wait for markets to settle.")
        return
    for timeframe in timeframes:
        print_timeframe(ledger, timeframe)
    print("\nSignal totals use saved ask-depth VWAP, configured fee and slippage buffer; "
          "assume the entry fills and holds to resolution.")


if __name__ == "__main__":
    main()
