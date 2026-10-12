"""Read-only Polymarket edge scanner and forecast calibration ledger.

The probability estimate is a transparent driftless lognormal baseline. It is a
research signal, not a proven source of alpha. This module never places orders.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sqlite3
import time
from typing import Optional

import requests

import config
import indicators


def _time_bucket_seconds(timeframe: str) -> int:
    duration = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "daily": 86400}[timeframe]
    return max(30, duration // 10)


def _fee_per_share(price: Optional[float]) -> Optional[float]:
    if price is None or not 0 < price < 1:
        return None
    # Conservative default based on the crypto taker fee model; keep configurable.
    return config.TAKER_FEE_RATE * price * (1.0 - price)


def _entry(book: Optional[dict]) -> tuple[Optional[float], Optional[float], Optional[list]]:
    """Return probe VWAP, all-in cost/share, and a compact top-10 book snapshot."""
    if not book:
        return None, None, None
    asks = book.get("asks") or {}
    bids = book.get("bids") or {}
    ask_levels = sorted(((float(p), float(q)) for p, q in asks.items() if float(q) > 0))
    bid_levels = sorted(((float(p), float(q)) for p, q in bids.items() if float(q) > 0), reverse=True)
    snapshot = {"bids": bid_levels[:10], "asks": ask_levels[:10]}
    target = float(config.EDGE_PROBE_SHARES)
    if target <= 0 or not ask_levels:
        return None, None, snapshot
    best_ask = ask_levels[0][0]
    remaining, notional, fee_total = target, 0.0, 0.0
    for price, size in ask_levels:
        if price > best_ask + config.EDGE_MAX_WALK:
            break
        take = min(remaining, size)
        notional += take * price
        # Polymarket documents fee precision to five decimal places; each
        # ladder level is treated as a separate matched fill for this estimate.
        fee_total += round(take * (_fee_per_share(price) or 0.0), 5)
        remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-9:
        return None, None, snapshot
    vwap = notional / target
    all_in = (notional + fee_total) / target + config.EDGE_SLIPPAGE_BUFFER
    return vwap, all_in, snapshot


def estimate(state, timeframe: str) -> dict:
    """Return current model probability and net edge per share for both outcomes."""
    left = max(0.0, (state.win_end or time.time()) - time.time())
    sigma = indicators.realized_vol_per_sec(state.klines)
    p_up = indicators.fair_up_prob(state.ref_price, state.price_to_beat, left, sigma)
    up_book = state.pm_book.get(state.pm_up_id) if state.pm_up_id else None
    down_book = state.pm_book.get(state.pm_dn_id) if state.pm_dn_id else None
    up_vwap, up_cost, up_levels = _entry(up_book)
    down_vwap, down_cost, down_levels = _entry(down_book)
    result = {
        "seconds_left": left,
        "ref_age": state.ref_age,
        "ref_stale": state.ref_stale,
        "p_up": p_up,
        "sigma_per_sqrt_second": sigma,
        "up_bid": state.pm_up_bid,
        "up_ask": state.pm_up,
        "down_bid": state.pm_dn_bid,
        "down_ask": state.pm_dn,
        "up_edge": None,
        "down_edge": None,
        "up_spread": None,
        "down_spread": None,
        "up_probe_vwap": up_vwap,
        "down_probe_vwap": down_vwap,
        "up_entry_cost": up_cost,
        "down_entry_cost": down_cost,
        "up_book": up_levels,
        "down_book": down_levels,
    }
    for side, bid_key, ask_key, probability in (
        ("up", "up_bid", "up_ask", p_up),
        ("down", "down_bid", "down_ask", None if p_up is None else 1.0 - p_up),
    ):
        bid, ask = result[bid_key], result[ask_key]
        if bid is not None and ask is not None:
            result[f"{side}_spread"] = ask - bid
        entry_cost = result[f"{side}_entry_cost"]
        # No signal off a stale settlement price: the model would compare an old price to a fresh strike.
        if probability is not None and entry_cost is not None and not result["ref_stale"]:
            result[f"{side}_edge"] = probability - entry_cost
    return result


class EdgeLedger:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS forecasts (
                market_slug TEXT NOT NULL,
                coin TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                horizon_bucket INTEGER NOT NULL,
                horizon_bucket_seconds INTEGER NOT NULL,
                captured_ts REAL NOT NULL,
                ref_price REAL,
                price_to_beat REAL,
                sigma_per_sqrt_second REAL,
                p_up REAL NOT NULL,
                market_p_up REAL,
                up_bid REAL, up_ask REAL, down_bid REAL, down_ask REAL,
                up_probe_vwap REAL, down_probe_vwap REAL,
                up_entry_cost REAL, down_entry_cost REAL,
                up_book_json TEXT, down_book_json TEXT,
                up_net_edge REAL, down_net_edge REAL,
                outcome_up INTEGER,
                PRIMARY KEY (market_slug, horizon_bucket)
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(forecasts)")}
            for name, kind in (("up_probe_vwap", "REAL"), ("down_probe_vwap", "REAL"),
                               ("up_entry_cost", "REAL"), ("down_entry_cost", "REAL"),
                               ("up_book_json", "TEXT"), ("down_book_json", "TEXT"),
                               ("ref_age_seconds", "REAL"), ("ref_stale", "INTEGER"),
                               ("ptb_source", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE forecasts ADD COLUMN {name} {kind}")
            db.execute("CREATE INDEX IF NOT EXISTS forecasts_unresolved ON forecasts(outcome_up, captured_ts)")

    def record(self, state, coin: str, timeframe: str) -> bool:
        if not state.pm_slug or not state.win_end or not state.price_to_beat or not state.ref_price:
            return False
        e = estimate(state, timeframe)
        if e["p_up"] is None:
            return False
        bucket_seconds = _time_bucket_seconds(timeframe)
        bucket = int(e["seconds_left"] // bucket_seconds)
        bid, ask = e["up_bid"], e["up_ask"]
        market_p = (bid + ask) / 2 if bid is not None and ask is not None else None
        stale = 1 if e["ref_stale"] else 0
        with self._connect() as db:
            if not stale:
                # A live forecast replaces a stale-flagged one already holding this bucket.
                db.execute("""DELETE FROM forecasts WHERE market_slug = ? AND horizon_bucket = ?
                              AND ref_stale = 1 AND outcome_up IS NULL""", (state.pm_slug, bucket))
            cur = db.execute("""INSERT OR IGNORE INTO forecasts (
                market_slug, coin, timeframe, horizon_bucket, horizon_bucket_seconds,
                captured_ts, ref_price, price_to_beat, sigma_per_sqrt_second,
                p_up, market_p_up, up_bid, up_ask, down_bid, down_ask,
                up_probe_vwap, down_probe_vwap, up_entry_cost, down_entry_cost,
                up_book_json, down_book_json, up_net_edge, down_net_edge,
                ref_age_seconds, ref_stale, ptb_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (state.pm_slug, coin, timeframe, bucket, bucket_seconds, time.time(),
                 state.ref_price, state.price_to_beat, e["sigma_per_sqrt_second"],
                 e["p_up"], market_p, e["up_bid"], e["up_ask"], e["down_bid"], e["down_ask"],
                 e["up_probe_vwap"], e["down_probe_vwap"], e["up_entry_cost"], e["down_entry_cost"],
                 json.dumps(e["up_book"]) if e["up_book"] is not None else None,
                 json.dumps(e["down_book"]) if e["down_book"] is not None else None,
                 e["up_edge"], e["down_edge"], e["ref_age"], stale, state.ptb_source))
            return cur.rowcount > 0

    @staticmethod
    def _closed_outcome(event: dict) -> Optional[int]:
        for market in event.get("markets") or []:
            if not market.get("closed"):
                continue
            outcomes, prices = market.get("outcomes"), market.get("outcomePrices")
            try:
                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes)
                if isinstance(prices, str):
                    prices = json.loads(prices)
                vals = [float(x) for x in prices]
                up_i = next(i for i, label in enumerate(outcomes) if str(label).strip().lower() == "up")
                if not vals or max(vals) < 0.99 or len(vals) != len(outcomes):
                    continue
                return int(vals[up_i] >= 0.99)
            except (TypeError, ValueError, StopIteration, json.JSONDecodeError):
                continue
        return None

    def resolve_pending(self):
        """Fetch Gamma's resolved outcomes for saved markets; uses public read-only data."""
        with self._connect() as db:
            slugs = [r[0] for r in db.execute(
                "SELECT market_slug FROM forecasts WHERE outcome_up IS NULL "
                "GROUP BY market_slug ORDER BY MAX(captured_ts) DESC LIMIT 20")]
        resolved = 0
        for slug in slugs:
            try:
                response = requests.get(config.PM_GAMMA, params={"slug": slug, "limit": 1}, timeout=8)
                response.raise_for_status()
                payload = response.json()
                if not payload:
                    continue
                outcome = self._closed_outcome(payload[0])
                if outcome is None:
                    continue
                with self._connect() as db:
                    cur = db.execute("UPDATE forecasts SET outcome_up=? WHERE market_slug=? AND outcome_up IS NULL",
                                     (outcome, slug))
                    resolved += cur.rowcount
            except (requests.RequestException, ValueError, TypeError):
                continue
        return resolved

    @staticmethod
    def _tf_filter(timeframe: Optional[str]) -> tuple[str, tuple]:
        return (" AND timeframe = ?", (timeframe,)) if timeframe else ("", ())

    def timeframes(self) -> list[str]:
        """Timeframes with at least one resolved forecast, shortest first."""
        with self._connect() as db:
            rows = db.execute("""SELECT DISTINCT timeframe, horizon_bucket_seconds FROM forecasts
                       WHERE outcome_up IS NOT NULL ORDER BY horizon_bucket_seconds""").fetchall()
        return list(dict.fromkeys(r["timeframe"] for r in rows))

    def summary(self, timeframe: Optional[str] = None) -> dict:
        tf_sql, tf_args = self._tf_filter(timeframe)
        with self._connect() as db:
            rows = db.execute("""SELECT horizon_bucket, horizon_bucket_seconds,
                       p_up, market_p_up, outcome_up, up_ask, down_ask, up_net_edge, down_net_edge
                FROM forecasts WHERE outcome_up IS NOT NULL AND ref_stale IS NOT 1""" + tf_sql, tf_args).fetchall()
        feed = self._feed_check(tf_sql, tf_args)
        if not rows:
            return {"n": 0, "brier_model": None, "brier_market": None, "feed": feed,
                    "candidate_n": 0, "candidate_pnl": None, "bucket_seconds": None}
        model_brier = sum((r["p_up"] - r["outcome_up"]) ** 2 for r in rows) / len(rows)
        market_rows = [r for r in rows if r["market_p_up"] is not None]
        market_brier = (sum((r["market_p_up"] - r["outcome_up"]) ** 2 for r in market_rows)
                        / len(market_rows)) if market_rows else None
        # Score the first qualifying entry per market to avoid counting each repeated snapshot
        # as an independent trade. This is a resolution-held proxy, not a fill simulation.
        # Query market identity and timestamps separately to preserve one first signal per market.
        with self._connect() as db:
            all_rows = db.execute("""SELECT market_slug, captured_ts, up_entry_cost, down_entry_cost,
                              up_net_edge, down_net_edge, outcome_up
                       FROM forecasts WHERE outcome_up IS NOT NULL AND ref_stale IS NOT 1""" + tf_sql
                       + " ORDER BY captured_ts", tf_args).fetchall()
        chosen, seen = [], set()
        for r in all_rows:
            slug = r["market_slug"]
            if slug in seen:
                continue
            up_edge, dn_edge = r["up_net_edge"], r["down_net_edge"]
            if (up_edge is not None and up_edge >= config.EDGE_MIN_NET
                    and r["up_entry_cost"] is not None):
                chosen.append((float(r["outcome_up"]), r["up_entry_cost"], up_edge))
                seen.add(slug)
            elif (dn_edge is not None and dn_edge >= config.EDGE_MIN_NET
                    and r["down_entry_cost"] is not None):
                chosen.append((1.0 - float(r["outcome_up"]), r["down_entry_cost"], dn_edge))
                seen.add(slug)
        candidate_pnl = 0.0
        for win, entry_cost, _ in chosen:
            candidate_pnl += (win - entry_cost) * config.EDGE_PROBE_SHARES
        return {"n": len(rows), "brier_model": model_brier, "brier_market": market_brier, "feed": feed,
                "candidate_n": len(chosen), "candidate_pnl": candidate_pnl if chosen else None,
                "bucket_seconds": rows[0]["horizon_bucket_seconds"]}

    def _feed_check(self, tf_sql: str, tf_args: tuple) -> dict:
        """Resolved rows by settlement-price status: live, blocked (stale), legacy (pre-check)."""
        with self._connect() as db:
            rows = db.execute("""SELECT CASE ref_stale WHEN 0 THEN 'live' WHEN 1 THEN 'stale'
                                        ELSE 'legacy' END status, count(*) n,
                                 avg((p_up - outcome_up) * (p_up - outcome_up)) brier
                          FROM forecasts WHERE outcome_up IS NOT NULL""" + tf_sql
                              + " GROUP BY 1", tf_args).fetchall()
        return {r["status"]: {"n": r["n"], "brier_model": r["brier"]} for r in rows}

    def horizon_scores(self, timeframe: Optional[str] = None) -> list[dict]:
        tf_sql, tf_args = self._tf_filter(timeframe)
        with self._connect() as db:
            rows = db.execute("""SELECT horizon_bucket, horizon_bucket_seconds,
                              p_up, market_p_up, outcome_up
                       FROM forecasts WHERE outcome_up IS NOT NULL AND ref_stale IS NOT 1""" + tf_sql
                       + " ORDER BY horizon_bucket DESC", tf_args).fetchall()
        grouped = {}
        for row in rows:
            key = (row["horizon_bucket"], row["horizon_bucket_seconds"])
            g = grouped.setdefault(key, {"n": 0, "model": 0.0, "market": 0.0, "market_n": 0})
            g["n"] += 1
            g["model"] += (row["p_up"] - row["outcome_up"]) ** 2
            if row["market_p_up"] is not None:
                g["market"] += (row["market_p_up"] - row["outcome_up"]) ** 2
                g["market_n"] += 1
        return [{"horizon_bucket": bucket,
                 "range_seconds": (bucket * width, (bucket + 1) * width),
                 "n": g["n"], "brier_model": g["model"] / g["n"],
                 "brier_market": g["market"] / g["market_n"] if g["market_n"] else None}
                for (bucket, width), g in sorted(grouped.items(), reverse=True)]


async def run_recorder(state, coin: str, timeframe: str):
    ledger = EdgeLedger(config.EDGE_DB_PATH)
    print(f"  [Edge] read-only forecast ledger → {config.EDGE_DB_PATH}")
    last_summary = 0.0
    while True:
        try:
            await asyncio.to_thread(ledger.record, state, coin, timeframe)
            now = time.time()
            if now - last_summary >= 30:
                await asyncio.to_thread(ledger.resolve_pending)
                summary = await asyncio.to_thread(ledger.summary, timeframe)
                if summary["n"]:
                    model = f"{summary['brier_model']:.3f}"
                    market = "—" if summary["brier_market"] is None else f"{summary['brier_market']:.3f}"
                    pnl = "—" if summary["candidate_pnl"] is None else f"${summary['candidate_pnl']:+.3f} total"
                    state.edge_summary = (f"{timeframe} Brier model {model} vs market {market} (n={summary['n']}); "
                                          f"first-signal hold proxy {pnl} across {summary['candidate_n']} "
                                          f"{config.EDGE_PROBE_SHARES:g}-share entries")
                else:
                    state.edge_summary = "waiting for resolved markets to score forecasts"
                last_summary = now
        except Exception as exc:
            state.edge_summary = f"ledger error: {exc}"
        await asyncio.sleep(1)
