import asyncio
import json
import time

import requests
import websockets
from datetime import datetime, timezone, timedelta

import config


class State:
    def __init__(self):
        self.bids: list[tuple[float, float]] = []
        self.asks: list[tuple[float, float]] = []
        self.mid: float = 0.0

        self.trades: list[dict] = []

        self.klines: list[dict] = []
        self.cur_kline: dict | None = None

        self.pm_up_id:  str | None = None
        self.pm_dn_id:  str | None = None
        self.pm_up:     float | None = None
        self.pm_dn:     float | None = None

        # period tracking (fair value)
        self.period_start_ts: int = 0
        self.period_end_ts:   int = 0
        self.strike: float | None = None
        self.strike_is_oracle: bool = False


OB_POLL_INTERVAL = 2


async def ob_poller(symbol: str, state: State):
    url = f"{config.BINANCE_REST}/depth"
    print(f"  [Binance OB] polling {symbol} every {OB_POLL_INTERVAL}s")
    while True:
        try:
            resp = requests.get(url, params={"symbol": symbol, "limit": 20}, timeout=3).json()
            state.bids = [(float(p), float(q)) for p, q in resp["bids"]]
            state.asks = [(float(p), float(q)) for p, q in resp["asks"]]
            if state.bids and state.asks:
                state.mid = (state.bids[0][0] + state.asks[0][0]) / 2
        except Exception:
            pass
        await asyncio.sleep(OB_POLL_INTERVAL)


async def binance_feed(symbol: str, kline_iv: str, state: State):
    sym = symbol.lower()
    streams = "/".join([
        f"{sym}@trade",
        f"{sym}@kline_{kline_iv}",
    ])
    url = f"{config.BINANCE_WS}?streams={streams}"

    while True:
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=30, open_timeout=10) as ws:
                print(f"  [Binance WS] connected – {symbol}")
                while True:
                    data   = json.loads(await ws.recv())
                    stream = data.get("stream", "")
                    pay    = data["data"]

                    if "@trade" in stream:
                        state.trades.append({
                            "t":      pay["T"] / 1000.0,
                            "price":  float(pay["p"]),
                            "qty":    float(pay["q"]),
                            "is_buy": not pay["m"],
                        })
                        if len(state.trades) > 5000:
                            cut = time.time() - config.TRADE_TTL
                            state.trades = [t for t in state.trades if t["t"] >= cut]

                    elif "@kline" in stream:
                        k = pay["k"]
                        candle = {
                            "t": k["t"] / 1000.0,
                            "o": float(k["o"]), "h": float(k["h"]),
                            "l": float(k["l"]), "c": float(k["c"]),
                            "v": float(k["v"]),
                        }
                        state.cur_kline = candle
                        if k["x"]:
                            state.klines.append(candle)
                            state.klines = state.klines[-config.KLINE_MAX:]
        except Exception as e:
            print(f"  [Binance WS] disconnected ({e}), reconnecting in 3s…", flush=True)
            await asyncio.sleep(3)


async def bootstrap(symbol: str, interval: str, state: State):
    resp = requests.get(
        f"{config.BINANCE_REST}/klines",
        params={"symbol": symbol, "interval": interval, "limit": config.KLINE_BOOT},
    ).json()
    state.klines = [
        {
            "t": r[0] / 1e3,
            "o": float(r[1]), "h": float(r[2]),
            "l": float(r[3]), "c": float(r[4]),
            "v": float(r[5]),
        }
        for r in resp
    ]
    print(f"  [Binance] loaded {len(state.klines)} historical candles")


_MONTHS = ["", "january", "february", "march", "april", "may", "june",
           "july", "august", "september", "october", "november", "december"]


def _et_now() -> datetime:
    utc = datetime.now(timezone.utc)
    year = utc.year

    mar1_dow  = datetime(year, 3, 1).weekday()
    mar_sun   = 1 + (6 - mar1_dow) % 7
    dst_start = datetime(year, 3, mar_sun + 7, 2, 0, 0, tzinfo=timezone.utc)

    nov1_dow = datetime(year, 11, 1).weekday()
    nov_sun  = 1 + (6 - nov1_dow) % 7
    dst_end  = datetime(year, 11, nov_sun, 6, 0, 0, tzinfo=timezone.utc)

    offset = timedelta(hours=-4) if dst_start <= utc < dst_end else timedelta(hours=-5)
    return utc + offset


def _to_12h(hour24: int) -> str:
    if hour24 == 0:
        return "12am"
    if hour24 < 12:
        return f"{hour24}am"
    if hour24 == 12:
        return "12pm"
    return f"{hour24 - 12}pm"


def _build_slug(coin: str, tf: str) -> str | None:
    now_utc = datetime.now(timezone.utc)
    now_ts  = int(now_utc.timestamp())
    et      = _et_now()

    if tf == "15m":
        ts = (now_ts // 900) * 900
        return f"{config.COIN_PM[coin]}-updown-15m-{ts}"

    if tf == "4h":
        ts = ((now_ts - 3600) // 14400) * 14400 + 3600
        return f"{config.COIN_PM[coin]}-updown-4h-{ts}"

    if tf == "1h":
        return (f"{config.COIN_PM_LONG[coin]}-up-or-down-"
                f"{_MONTHS[et.month]}-{et.day}-{_to_12h(et.hour)}-et")

    if tf == "daily":
        resolution = et.replace(hour=12, minute=0, second=0, microsecond=0)
        target      = et if et < resolution else et + timedelta(days=1)
        return (f"{config.COIN_PM_LONG[coin]}-up-or-down-on-"
                f"{_MONTHS[target.month]}-{target.day}")

    return None


def fetch_pm_tokens(coin: str, tf: str) -> tuple:
    slug = _build_slug(coin, tf)
    if slug is None:
        return None, None
    try:
        data = requests.get(config.PM_GAMMA, params={"slug": slug, "limit": 1}).json()
        if not data or data[0].get("ticker") != slug:
            print(f"  [PM] no active market for slug: {slug}")
            return None, None
        ids = json.loads(data[0]["markets"][0]["clobTokenIds"])
        return ids[0], ids[1]
    except Exception as e:
        print(f"  [PM] token fetch failed ({slug}): {e}")
        return None, None


async def pm_feed(state: State):
    if not state.pm_up_id:
        print("  [PM] no tokens for this coin/timeframe – skipped")
        return

    while True:
        try:
            # Re-read token IDs from state on each reconnect (period_tracker may have updated them)
            assets = [state.pm_up_id, state.pm_dn_id]
            if not assets[0]:
                await asyncio.sleep(5)
                continue

            async with websockets.connect(
                config.PM_WS,
                ping_interval=30,
                ping_timeout=30,
                open_timeout=10,
                close_timeout=5,
            ) as ws:
                await ws.send(json.dumps({"assets_ids": assets, "type": "market"}))
                print("  [PM] connected", flush=True)
                while True:
                    raw = json.loads(await ws.recv())

                    if isinstance(raw, list):
                        for entry in raw:
                            _pm_apply(entry.get("asset_id"), entry.get("asks", []), state)

                    elif isinstance(raw, dict) and raw.get("event_type") == "price_change":
                        for ch in raw.get("price_changes", []):
                            if ch.get("best_ask"):
                                _pm_set(ch["asset_id"], float(ch["best_ask"]), state)
        except Exception as e:
            print(f"  [PM] disconnected ({e}), reconnecting in 5s…", flush=True)
            await asyncio.sleep(5)


def _pm_apply(asset, asks, state):
    if asks:
        _pm_set(asset, min(float(a["price"]) for a in asks), state)


def _pm_set(asset, price, state):
    if asset == state.pm_up_id:
        state.pm_up = price
    elif asset == state.pm_dn_id:
        state.pm_dn = price


def _fetch_oracle_strike(coin: str, tf: str) -> float | None:
    """Fetch the Chainlink oracle strike (PRICE TO BEAT) from Polymarket."""
    variant = config.TF_VARIANT.get(tf)
    if not variant:
        return None

    now_ts = int(time.time())
    period_start = (now_ts // 900) * 900
    start_iso = datetime.fromtimestamp(period_start, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    try:
        resp = requests.get(
            config.PM_PAST_RESULTS,
            params={
                "symbol": coin,
                "variant": variant,
                "assetType": "crypto",
                "currentEventStartTime": start_iso,
            },
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        results = data.get("data", {}).get("results", [])
        if not results:
            return None

        # The last result's closePrice = current period's oracle open price
        last = results[-1]
        end_time = last.get("endTime", "")
        # Verify: lastResult.endTime should == current period start
        if start_iso.replace("Z", ".000Z") == end_time or end_time.startswith(
            start_iso.replace("Z", "")
        ):
            return last["closePrice"]

        # If current period is IN the results, use its openPrice
        for r in results:
            st = r.get("startTime", "")
            if st.startswith(start_iso.replace("Z", "")):
                return r["openPrice"]

        # Fallback: use last closePrice anyway (best guess)
        return last["closePrice"]

    except Exception as e:
        print(f"  [FV] oracle strike fetch failed: {e}")
        return None


async def period_tracker(state: State, coin: str, tf: str):
    """Track 15m period boundaries and fetch Chainlink oracle strike."""
    if tf != "15m":
        return

    # initialize period boundaries
    now_ts = int(time.time())
    state.period_start_ts = (now_ts // 900) * 900
    state.period_end_ts = state.period_start_ts + 900

    # fetch the actual Chainlink oracle strike
    await asyncio.sleep(2)  # let feeds populate first
    strike = _fetch_oracle_strike(coin, tf)
    if strike is not None:
        state.strike = strike
        state.strike_is_oracle = True
        print(f"  [FV] oracle strike = ${strike:,.2f}")
    else:
        # fallback to Binance mid
        if state.mid > 0:
            state.strike = state.mid
            state.strike_is_oracle = False
            print(f"  [FV] oracle unavailable, using Binance mid = ${state.mid:,.2f}")

    print(f"  [FV] period {state.period_start_ts}→{state.period_end_ts}, strike={state.strike}")

    while True:
        now_ts = int(time.time())
        current_period = (now_ts // 900) * 900

        if current_period != state.period_start_ts:
            state.period_start_ts = current_period
            state.period_end_ts = current_period + 900

            # Fetch new oracle strike for the new period
            strike = _fetch_oracle_strike(coin, tf)
            if strike is not None:
                state.strike = strike
                state.strike_is_oracle = True
                print(f"  [FV] new period {current_period}, oracle strike=${strike:,.2f}")
            else:
                state.strike = state.mid if state.mid > 0 else None
                state.strike_is_oracle = False
                print(f"  [FV] new period {current_period}, fallback strike={state.strike}")

            # Re-fetch PM token IDs for the new period market
            new_up, new_dn = fetch_pm_tokens(coin, tf)
            if new_up:
                state.pm_up_id = new_up
                state.pm_dn_id = new_dn
                state.pm_up = None
                state.pm_dn = None
                print(f"  [FV] new PM tokens: {new_up[:16]}… / {new_dn[:16]}…")

        await asyncio.sleep(1)


async def pm_price_poller(state: State, coin: str, tf: str):
    """Poll Gamma API for PM outcome prices as REST fallback."""
    while True:
        try:
            slug = _build_slug(coin, tf)
            if slug:
                resp = requests.get(
                    config.PM_GAMMA,
                    params={"slug": slug, "limit": 1},
                    timeout=5,
                )
                data = resp.json()
                if data and data[0].get("markets"):
                    m = data[0]["markets"][0]
                    prices = m.get("outcomePrices", "[]")
                    if isinstance(prices, str):
                        prices = json.loads(prices)
                    if len(prices) >= 2:
                        up_price = float(prices[0])
                        dn_price = float(prices[1])
                        # Only update if they look sane (not 0.00 or 1.00 extremes
                        # unless the market is truly near those levels)
                        if 0.01 < up_price < 0.99:
                            state.pm_up = up_price
                            state.pm_dn = dn_price
                        elif state.pm_up is None:
                            state.pm_up = up_price
                            state.pm_dn = dn_price
        except Exception:
            pass
        await asyncio.sleep(config.PM_PRICE_POLL)
