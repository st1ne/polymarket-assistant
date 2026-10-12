import os
from pathlib import Path

# ── Coins ───────────────────────────────────────────────────────
COINS = ["BTC", "ETH", "SOL", "XRP"]

COIN_BINANCE = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}

COIN_PM      = {"BTC": "btc",     "ETH": "eth",      "SOL": "sol",    "XRP": "xrp"}
COIN_PM_LONG = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp"}

# ── Timeframes ──────────────────────────────────────────────────
COIN_TIMEFRAMES = {
    "BTC": ["5m", "15m", "1h", "4h", "daily"],
    "ETH": ["5m", "15m", "1h", "4h", "daily"],
    "SOL": ["5m", "15m", "1h", "4h", "daily"],
    "XRP": ["5m", "15m", "1h", "4h", "daily"],
}

# Binance kline interval used for TA candles
TF_KLINE = {"5m": "1m", "15m": "1m", "1h": "1m", "4h": "15m", "daily": "1h"}

# ── Binance ─────────────────────────────────────────────────────
BINANCE_WS   = "wss://stream.binance.com/stream"
BINANCE_REST = "https://api.binance.com/api/v3"
BINANCE_US_WS   = "wss://stream.binance.us:9443/stream"
BINANCE_US_REST = "https://api.binance.us/api/v3"
OB_LEVELS    = 20          # depth levels in stream (Binance: 5 / 10 / 20)
TRADE_TTL    = 600         # keep 10 min of trades
KLINE_MAX    = 150         # max candles in memory
KLINE_BOOT   = 100         # candles fetched on startup

# ── Polymarket ──────────────────────────────────────────────────
PM_GAMMA = "https://gamma-api.polymarket.com/events"
PM_WS    = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PM_RTDS  = "wss://ws-live-data.polymarket.com"          # live Chainlink prices
PM_CRYPTO_PRICE = "https://polymarket.com/api/crypto/crypto-price"  # official price to beat
PM_VARIANT = {"5m": "fiveminute", "15m": "fifteen", "1h": "hourly", "4h": "fourhour", "daily": "daily"}
# resolution source per timeframe: 1h / daily settle on Binance candles, the rest on Chainlink
TF_RESOLVES_ON = {"5m": "chainlink", "15m": "chainlink", "4h": "chainlink", "1h": "binance", "daily": "binance"}

# ── Orderbook indicators ───────────────────────────────────────
OBI_BAND_PCT = 1.0          # % band around mid for OBI calc
OBI_THRESH   = 0.10         # ±10 % = signal
WALL_MULT    = 5            # wall = level qty > N × avg level qty
DEPTH_BANDS  = [0.1, 0.5, 1.0]   # % from mid for depth calc

# ── Flow indicators ────────────────────────────────────────────
CVD_WINDOWS  = [60, 180, 300]    # 1m / 3m / 5m in seconds
DELTA_WINDOW = 60                # short delta window (seconds)

# ── TA indicators ──────────────────────────────────────────────
RSI_PERIOD = 14
RSI_OB     = 70
RSI_OS     = 30
MACD_FAST  = 12
MACD_SLOW  = 26
MACD_SIG   = 9
EMA_S      = 5
EMA_L      = 20

# ── Bias Score weights (max absolute contribution of each indicator) ──
BIAS_WEIGHTS = {
    "ema":   10,   # EMA5/EMA20 cross  – strongest trend proxy
    "obi":    8,   # Order Book Imbalance
    "macd":   8,   # MACD histogram sign
    "cvd":    7,   # CVD 5m sign
    "ha":     6,   # Heikin-Ashi streak (up to 3 candles)
    "vwap":   5,   # Price vs VWAP
    "rsi":    5,   # RSI overbought/oversold
    "poc":    3,   # Price vs POC
    "walls":  4,   # bid walls − ask walls (capped ±4)
}
# sum of all weights = 56; bias = (raw_sum / 56) * 100, clamped to ±100

# ── Fair value ─────────────────────────────────────────────────
VOL_LOOKBACK = 60       # closed candles used for realised volatility
EDGE_THRESH  = 0.03     # flag when model P beats the ask by ≥ 3 ¢
TAKER_FEE_RATE = float(os.getenv("POLY_TAKER_FEE_RATE", "0.07"))
EDGE_SLIPPAGE_BUFFER = float(os.getenv("EDGE_SLIPPAGE_BUFFER", "0.005"))
EDGE_MIN_NET = float(os.getenv("EDGE_MIN_NET", "0.03"))
EDGE_PROBE_SHARES = float(os.getenv("EDGE_PROBE_SHARES", "5"))
EDGE_MAX_WALK = float(os.getenv("EDGE_MAX_WALK", "0.02"))
CL_STALE_SECS = float(os.getenv("CL_STALE_SECS", "5"))          # block signals when the price is older
CL_RECONNECT_SECS = float(os.getenv("CL_RECONNECT_SECS", "8"))  # fresh RTDS socket after this silence
SETTLE_TWAP_SECS = 60   # Chainlink markets settle on 60 s trailing TWAPs at window start / end
EDGE_DB_PATH = os.getenv("EDGE_DB_PATH", str(Path(__file__).resolve().parents[1] / "edge_research.db"))

# ── Dashboard ──────────────────────────────────────────────────
HA_COUNT   = 8          # Heikin Ashi candles shown
VP_BINS    = 30         # volume profile price buckets
VP_SHOW    = 9          # VP rows visible
REFRESH    = 10         # seconds between dashboard redraws
REFRESH_5M = 3          # faster refresh for 5m timeframe (seconds)
