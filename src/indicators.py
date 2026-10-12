import time
import config


def obi(bids, asks, mid):
    band = mid * config.OBI_BAND_PCT / 100
    bv = sum(q for p, q in bids if p >= mid - band)
    av = sum(q for p, q in asks if p <= mid + band)
    tot = bv + av
    return (bv - av) / tot if tot else 0.0


def walls(bids, asks):
    vols = [q for _, q in bids] + [q for _, q in asks]
    if not vols:
        return [], []
    avg = sum(vols) / len(vols)
    thr = avg * config.WALL_MULT
    return (
        [(p, q) for p, q in bids if q >= thr],
        [(p, q) for p, q in asks if q >= thr],
    )


def depth_usd(bids, asks, mid):
    out = {}
    for pct in config.DEPTH_BANDS:
        band = mid * pct / 100
        out[pct] = (
            sum(p * q for p, q in bids if p >= mid - band)
            + sum(p * q for p, q in asks if p <= mid + band)
        )
    return out


def cvd(trades, secs):
    cut = time.time() - secs
    return sum(
        t["qty"] * t["price"] * (1 if t["is_buy"] else -1)
        for t in trades
        if t["t"] >= cut
    )


def vol_profile(klines):
    if not klines:
        return 0.0, []

    lo = min(k["l"] for k in klines)
    hi = max(k["h"] for k in klines)
    if hi == lo:
        return lo, [(lo, sum(k["v"] for k in klines))]

    n   = config.VP_BINS
    bsz = (hi - lo) / n
    bins = [0.0] * n

    for k in klines:
        b_lo = max(0,     int((k["l"] - lo) / bsz))
        b_hi = min(n - 1, int((k["h"] - lo) / bsz))
        share = k["v"] / max(1, b_hi - b_lo + 1)
        for b in range(b_lo, b_hi + 1):
            bins[b] += share

    poci = bins.index(max(bins))
    poc  = lo + (poci + 0.5) * bsz
    data = [(lo + (i + 0.5) * bsz, bins[i]) for i in range(n)]
    return poc, data


def _ema_series(vals, period):
    if len(vals) < period:
        return [None] * len(vals)
    mult = 2.0 / (period + 1)
    out  = [None] * (period - 1)
    out.append(sum(vals[:period]) / period)
    for v in vals[period:]:
        out.append(v * mult + out[-1] * (1 - mult))
    return out


def rsi(klines):
    closes = [k["c"] for k in klines]
    n = config.RSI_PERIOD
    if len(closes) < n + 1:
        return None

    ch = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    ag = sum(max(c, 0) for c in ch[:n]) / n
    al = sum(max(-c, 0) for c in ch[:n]) / n
    for c in ch[n:]:
        ag = (ag * (n - 1) + max(c, 0))  / n
        al = (al * (n - 1) + max(-c, 0)) / n
    return 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)


def macd(klines):
    closes = [k["c"] for k in klines]
    if len(closes) < config.MACD_SLOW:
        return None, None, None

    ef = _ema_series(closes, config.MACD_FAST)
    es = _ema_series(closes, config.MACD_SLOW)

    ml = [ef[i] - es[i] for i in range(len(closes)) if ef[i] is not None and es[i] is not None]
    if not ml:
        return None, None, None

    sig = _ema_series(ml, config.MACD_SIG)
    m = ml[-1]
    s = sig[-1]
    h = (m - s) if s is not None else None
    return m, s, h


def vwap(klines):
    tp_v = sum((k["h"] + k["l"] + k["c"]) / 3 * k["v"] for k in klines)
    v    = sum(k["v"] for k in klines)
    return tp_v / v if v else 0.0


def emas(klines):
    closes = [k["c"] for k in klines]
    s = _ema_series(closes, config.EMA_S)
    l = _ema_series(closes, config.EMA_L)
    return (
        s[-1] if s and s[-1] is not None else None,
        l[-1] if l and l[-1] is not None else None,
    )


def bias_score(bids, asks, mid, trades, klines) -> float:
    """Return a bias score in [-100, +100].
    Positive = bullish signal, negative = bearish signal.
    Uses weighted sum of all key indicators, normalised to max possible weight.
    """
    W  = config.BIAS_WEIGHTS
    total = 0.0

    # ── EMA cross ───────────────────────────────────────────────
    es, el = emas(klines)
    if es is not None and el is not None:
        total += W["ema"] if es > el else -W["ema"]

    # ── OBI (linear –1..+1 → –W..+W) ───────────────────────────
    if mid:
        obi_v = obi(bids, asks, mid)       # –1..+1
        total += obi_v * W["obi"]

    # ── MACD histogram sign ──────────────────────────────────────
    _, _, hv = macd(klines)
    if hv is not None:
        total += W["macd"] if hv > 0 else -W["macd"]

    # ── CVD 5m sign ─────────────────────────────────────────────
    cvd5 = cvd(trades, 300)
    if cvd5 != 0:
        total += W["cvd"] if cvd5 > 0 else -W["cvd"]

    # ── Heikin-Ashi streak (last 3 candles, 2 pts each) ─────────
    ha = heikin_ashi(klines)
    if ha:
        streak = 0
        for c in reversed(ha[-3:]):
            if c["green"]:
                if streak >= 0:
                    streak += 1
                else:
                    break
            else:
                if streak <= 0:
                    streak -= 1
                else:
                    break
        # streak ∈ {-3..+3}; scale to ±W
        total += max(-W["ha"], min(W["ha"], streak * (W["ha"] / 3)))

    # ── Price vs VWAP ────────────────────────────────────────────
    vwap_v = vwap(klines)
    if vwap_v and mid:
        total += W["vwap"] if mid > vwap_v else -W["vwap"]

    # ── RSI overbought / oversold (linear mapping) ───────────────
    rsi_v = rsi(klines)
    if rsi_v is not None:
        if rsi_v <= 30:
            total += W["rsi"]
        elif rsi_v >= 70:
            total -= W["rsi"]
        elif rsi_v < 50:
            total += W["rsi"] * (50 - rsi_v) / 20     # 30→+W, 50→0
        else:
            total -= W["rsi"] * (rsi_v - 50) / 20     # 50→0, 70→–W

    # ── Price vs POC ─────────────────────────────────────────────
    poc, _ = vol_profile(klines)
    if poc and mid:
        total += W["poc"] if mid > poc else -W["poc"]

    # ── Walls (bid walls bullish, ask walls bearish) ─────────────
    bw, aw = walls(bids, asks)
    wall_pts = (min(len(bw), 2) - min(len(aw), 2)) * 2   # ±0/2/4
    total += max(-W["walls"], min(W["walls"], wall_pts))

    max_possible = sum(W.values())   # 56
    raw = (total / max_possible) * 100
    return max(-100.0, min(100.0, raw))


def heikin_ashi(klines):
    ha = []
    for i, k in enumerate(klines):
        c = (k["o"] + k["h"] + k["l"] + k["c"]) / 4
        o = (k["o"] + k["c"]) / 2 if i == 0 else (ha[i - 1]["o"] + ha[i - 1]["c"]) / 2
        ha.append({
            "o": o,
            "h": max(k["h"], o, c),
            "l": min(k["l"], o, c),
            "c": c,
            "green": c >= o,
        })
    return ha


# ── Fair value for Up/Down markets ──────────────────────────────

def realized_vol_per_sec(klines):
    """Std-dev of log returns per √second, from recent closed candles."""
    import math
    ks = klines[-(config.VOL_LOOKBACK + 1):]
    if len(ks) < 10:
        return None
    rets = [math.log(ks[i]["c"] / ks[i - 1]["c"]) for i in range(1, len(ks))]
    dt   = (ks[-1]["t"] - ks[0]["t"]) / (len(ks) - 1)
    if dt <= 0:
        return None
    mu  = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var / dt)


def fair_up_prob(price, price_to_beat, secs_left, sigma_per_sec):
    """P(close ≥ price_to_beat) under a driftless lognormal walk."""
    import math
    if not price or not price_to_beat or sigma_per_sec is None:
        return None
    if secs_left <= 0:
        return 1.0 if price >= price_to_beat else 0.0
    sd = sigma_per_sec * math.sqrt(secs_left)
    if sd <= 0:
        return None
    z = math.log(price / price_to_beat) / sd
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))
