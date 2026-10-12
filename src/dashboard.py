from rich.table   import Table
from rich.panel   import Panel
from rich.console import Group
from rich.text    import Text
from rich         import box as bx

import time

import config
import indicators as ind
import edge_finder


class _Group:
    def __init__(self, *renderables):
        self.renderables = renderables

    def __rich_console__(self, console, options):
        for r in self.renderables:
            yield r


def _p(val, d=2):
    if val is None:
        return "—"
    if val >= 1e6:
        return f"${val / 1e6:,.2f}M"
    if val >= 1e3:
        return f"${val:,.{d}f}"
    return f"${val:,.{d}f}"


def _col(val):
    if val is None:
        return "dim"
    return "green" if val > 0 else "red"


TREND_THRESH = 3


def _score_trend(st):
    score = 0

    obi_v = ind.obi(st.bids, st.asks, st.mid) if st.mid else 0.0
    if obi_v > config.OBI_THRESH:
        score += 1
    elif obi_v < -config.OBI_THRESH:
        score -= 1

    cvd5 = ind.cvd(st.trades, 300)
    score += 1 if cvd5 > 0 else -1 if cvd5 < 0 else 0

    rsi_v = ind.rsi(st.klines)
    if rsi_v is not None:
        if rsi_v > config.RSI_OB:
            score -= 1
        elif rsi_v < config.RSI_OS:
            score += 1

    _, _, hv = ind.macd(st.klines)
    if hv is not None:
        score += 1 if hv > 0 else -1

    vwap_v = ind.vwap(st.klines)
    if vwap_v and st.mid:
        score += 1 if st.mid > vwap_v else -1

    es, el = ind.emas(st.klines)
    if es is not None and el is not None:
        score += 1 if es > el else -1

    bw, aw = ind.walls(st.bids, st.asks)
    score += min(len(bw), 2)
    score -= min(len(aw), 2)

    ha = ind.heikin_ashi(st.klines)
    if len(ha) >= 3:
        last3 = ha[-3:]
        if all(c["green"] for c in last3):
            score += 1
        elif all(not c["green"] for c in last3):
            score -= 1

    if score >= TREND_THRESH:
        return score, "BULLISH",  "green"
    elif score <= -TREND_THRESH:
        return score, "BEARISH",  "red"
    else:
        return score, "NEUTRAL",  "yellow"


def _bias_display(bias: float) -> tuple[str, str, str]:
    """Return (label, pct_str, color) for a bias score in [-100, +100]."""
    pct = abs(bias)
    if bias > 10:
        label, col = "BULLISH", "green"
    elif bias < -10:
        label, col = "BEARISH", "red"
    else:
        label, col = "NEUTRAL", "yellow"
    return label, f"{pct:.0f}%", col


def _header(st, coin, tf):
    score, label, col = _score_trend(st)
    bias = ind.bias_score(st.bids, st.asks, st.mid, st.trades, st.klines)
    b_label, b_pct, b_col = _bias_display(bias)

    parts = [
        (f"  {coin} ", "bold white on dark_blue"),
        (f" {tf} ", "bold white on dark_green"),
        (f"  Price: {_p(st.mid)}  ", "bold white"),
    ]

    if st.pm_up is not None and st.pm_dn is not None:
        parts.append((f"  PM ↑ {st.pm_up:.3f}  ↓ {st.pm_dn:.3f}  ", "cyan"))
    elif st.pm_slug:
        parts.append(("  PM: waiting for book…  ", "dim cyan"))

    parts.append((f" {label} ", f"bold white on {col}"))
    parts.append((f"  ({score:+d})", col))

    # ── Bias Score ────────────────────────────────────────────────
    parts.append(("   │  ", "dim"))
    parts.append(("Bias: ", "dim white"))
    parts.append((f"{b_label} {b_pct}", f"bold {b_col}"))

    parts.append(("\n", ""))
    parts.append(("  Polymarket Crypto Assistant", "dim white"))
    parts.append(("  |  @SolSt1ne", "dim cyan"))

    return Panel(
        Text.assemble(*parts),
        title="POLYMARKET CRYPTO ASSISTANT",
        box=bx.DOUBLE,
        expand=True,
    )


def _ob_panel(st):
    obi_v      = ind.obi(st.bids, st.asks, st.mid) if st.mid else 0.0
    bw, aw     = ind.walls(st.bids, st.asks)
    dep        = ind.depth_usd(st.bids, st.asks, st.mid) if st.mid else {}

    if obi_v > config.OBI_THRESH:
        oc, os = "green", "BULLISH"
    elif obi_v < -config.OBI_THRESH:
        oc, os = "red",   "BEARISH"
    else:
        oc, os = "yellow", "NEUTRAL"

    t = Table(box=None, show_header=False, pad_edge=False, expand=True)
    t.add_column("label", style="dim",    width=16)
    t.add_column("value",                 width=18)
    t.add_column("signal",                width=14)

    t.add_row("OBI",
              f"[{oc}]{obi_v * 100:+.1f} %[/{oc}]",
              f"[{oc}]{os}[/{oc}]")

    for pct in config.DEPTH_BANDS:
        t.add_row(f"Depth {pct}%", _p(dep.get(pct, 0)), "")

    if bw:
        t.add_row("BUY walls",
                  f"[green]{', '.join(_p(p, 2) for p, _ in bw[:3])}[/green]",
                  "[green]WALL[/green]")
    if aw:
        t.add_row("SELL walls",
                  f"[red]{', '.join(_p(p, 2) for p, _ in aw[:3])}[/red]",
                  "[red]WALL[/red]")
    if not bw and not aw:
        t.add_row("Walls", "[dim]none[/dim]", "")

    return Panel(t, title="ORDER BOOK", box=bx.ROUNDED, expand=True)


def _flow_panel(st):
    cvds = {s: ind.cvd(st.trades, s) for s in config.CVD_WINDOWS}
    poc, vp = ind.vol_profile(st.klines)

    t = Table(box=None, show_header=False, pad_edge=False, expand=True)
    t.add_column("label", style="dim", width=16)
    t.add_column("value",              width=22)
    t.add_column("dir",                width=4)

    for secs in config.CVD_WINDOWS:
        v  = cvds[secs]
        c  = _col(v)
        t.add_row(f"CVD {secs // 60}m",
                  f"[{c}]{_p(v)}[/{c}]",
                  f"[{c}]{'↑' if v > 0 else '↓'}[/{c}]")

    delta_v = ind.cvd(st.trades, config.DELTA_WINDOW)
    dc = _col(delta_v)
    t.add_row("Delta 1m",
              f"[{dc}]{_p(delta_v)}[/{dc}]",
              f"[{dc}]{'↑' if delta_v > 0 else '↓'}[/{dc}]")

    t.add_row("POC", f"[bold]{_p(poc)}[/bold]", "")

    if vp:
        max_v  = max(v for _, v in vp) or 1
        poc_i  = min(range(len(vp)), key=lambda i: abs(vp[i][0] - poc))
        half   = config.VP_SHOW // 2
        start  = max(0, poc_i - half)
        end    = min(len(vp), start + config.VP_SHOW)
        start  = max(0, end - config.VP_SHOW)

        for i in range(end - 1, start - 1, -1):
            p, v = vp[i]
            bar_len = int(v / max_v * 14)
            bar     = "█" * bar_len + "░" * (14 - bar_len)
            is_poc  = i == poc_i
            style   = "green bold" if is_poc else "dim"
            marker  = " ◄ POC" if is_poc else ""
            t.add_row(f"[{style}]{_p(p)}[/{style}]",
                      f"[{style}]{bar}{marker}[/{style}]", "")

    return Panel(t, title="FLOW & VOLUME", box=bx.ROUNDED, expand=True)


def _ta_panel(st):
    rsi_v              = ind.rsi(st.klines)
    macd_v, sig_v, hv  = ind.macd(st.klines)
    vwap_v             = ind.vwap(st.klines)
    ema_s, ema_l       = ind.emas(st.klines)
    ha                 = ind.heikin_ashi(st.klines)

    t = Table(box=None, show_header=False, pad_edge=False, expand=True)
    t.add_column("label",  style="dim", width=16)
    t.add_column("value",              width=18)
    t.add_column("signal",             width=18)

    if rsi_v is not None:
        if rsi_v > config.RSI_OB:
            rc, rs = "red",   "OVERBOUGHT"
        elif rsi_v < config.RSI_OS:
            rc, rs = "green", "OVERSOLD"
        else:
            rc, rs = "yellow", f"{rsi_v:.0f}"
        t.add_row("RSI(14)", f"[{rc}]{rsi_v:.1f}[/{rc}]", f"[{rc}]{rs}[/{rc}]")
    else:
        t.add_row("RSI(14)", "[dim]—[/dim]", "")

    if macd_v is not None:
        mc = _col(macd_v)
        t.add_row("MACD", f"[{mc}]{macd_v:+.6f}[/{mc}]", f"[{mc}]{'↑' if macd_v > 0 else '↓'}[/{mc}]")
        if sig_v is not None:
            cross = "[green]bullish[/green]" if hv is not None and hv > 0 else "[red]bearish[/red]"
            t.add_row("Signal", f"{sig_v:+.6f}", cross)
    else:
        t.add_row("MACD", "[dim]—[/dim]", "")

    if vwap_v and st.mid:
        vc  = "green" if st.mid > vwap_v else "red"
        vr  = "above" if st.mid > vwap_v else "below"
        t.add_row("VWAP", _p(vwap_v), f"[{vc}]price {vr}[/{vc}]")

    if ema_s is not None and ema_l is not None:
        ec  = "green" if ema_s > ema_l else "red"
        rel = ">" if ema_s > ema_l else "<"
        t.add_row("EMA 5",  _p(ema_s), f"[{ec}]{rel} EMA 20[/{ec}]")
        t.add_row("EMA 20", _p(ema_l), "")

    if ha:
        last = ha[-config.HA_COUNT:]
        dots = " ".join("[green]▲[/green]" if c["green"] else "[red]▼[/red]" for c in last)
        green_tail = sum(1 for c in last[-3:] if c["green"])
        hc   = "green" if green_tail >= 2 else "red"
        hs   = "trend ↑" if green_tail >= 2 else "trend ↓"
        t.add_row("Heikin Ashi", dots, f"[{hc}]{hs}[/{hc}]")

    return Panel(t, title="TECHNICAL", box=bx.ROUNDED, expand=True)


def _signals_panel(st):
    sigs = []

    obi_v = ind.obi(st.bids, st.asks, st.mid) if st.mid else 0.0
    if abs(obi_v) > config.OBI_THRESH:
        c = "green" if obi_v > 0 else "red"
        d = "BULLISH" if obi_v > 0 else "BEARISH"
        sigs.append(f"[{c}]OBI → {d} ({obi_v * 100:+.1f} %)[/{c}]")

    cvd5 = ind.cvd(st.trades, 300)
    if cvd5 != 0:
        c = "green" if cvd5 > 0 else "red"
        d = "buy pressure" if cvd5 > 0 else "sell pressure"
        sigs.append(f"[{c}]CVD 5m → {d} ({_p(cvd5)})[/{c}]")

    rsi_v = ind.rsi(st.klines)
    if rsi_v is not None:
        if rsi_v > config.RSI_OB:
            sigs.append(f"[red]RSI → overbought ({rsi_v:.0f})[/red]")
        elif rsi_v < config.RSI_OS:
            sigs.append(f"[green]RSI → oversold ({rsi_v:.0f})[/green]")

    _, _, hv = ind.macd(st.klines)
    if hv is not None:
        c = "green" if hv > 0 else "red"
        d = "bullish" if hv > 0 else "bearish"
        sigs.append(f"[{c}]MACD hist → {d}[/{c}]")

    vwap_v = ind.vwap(st.klines)
    if vwap_v and st.mid:
        c = "green" if st.mid > vwap_v else "red"
        d = "above" if st.mid > vwap_v else "below"
        sigs.append(f"[{c}]Price {d} VWAP[/{c}]")

    es, el = ind.emas(st.klines)
    if es is not None and el is not None:
        c = "green" if es > el else "red"
        d = "golden" if es > el else "death"
        sigs.append(f"[{c}]EMA → {d} cross[/{c}]")

    bw, aw = ind.walls(st.bids, st.asks)
    if bw:
        sigs.append(f"[green]BUY wall × {len(bw)} levels[/green]")
    if aw:
        sigs.append(f"[red]SELL wall × {len(aw)} levels[/red]")

    ha = ind.heikin_ashi(st.klines)
    if len(ha) >= 3:
        last3 = ha[-3:]
        if all(c["green"] for c in last3):
            sigs.append("[green]HA → 3+ green candles (up streak)[/green]")
        elif all(not c["green"] for c in last3):
            sigs.append("[red]HA → 3+ red candles (down streak)[/red]")

    if not sigs:
        sigs.append("[dim]No active signals[/dim]")

    score, label, col = _score_trend(st)
    bias = ind.bias_score(st.bids, st.asks, st.mid, st.trades, st.klines)
    b_label, b_pct, b_col = _bias_display(bias)

    # ── TREND bar (qualitative) ─────────────────────────────────
    max_score = 10
    filled = int(min(abs(score), max_score) / max_score * 14)
    bar    = "█" * filled + "░" * (14 - filled)
    sigs.append("[dim]─────────────────────────────[/dim]")
    sigs.append(f"[{col} bold]TREND: {label}[/{col} bold]  "
                f"[{col}]{bar}[/{col}]  [{col}]{score:+d}[/{col}]")

    # ── BIAS SCORE bar (quantitative) ───────────────────────────
    bias_filled = int(abs(bias) / 100 * 20)
    bias_bar    = "█" * bias_filled + "░" * (20 - bias_filled)
    sign_arrow  = "▲" if bias >= 0 else "▼"
    sigs.append(f"[{b_col} bold]BIAS:  {b_label} {b_pct:>5}[/{b_col} bold]  "
                f"[{b_col}]{sign_arrow} {bias_bar}[/{b_col}]  "
                f"[dim]{bias:+.1f}[/dim]")

    return Panel("\n".join(sigs), title="SIGNALS", box=bx.ROUNDED, expand=True)


def _f(v, d=3):
    return "—" if v is None else f"{v:.{d}f}"


def _market_panel(st, timeframe):
    t = Table(box=None, show_header=False, pad_edge=False, expand=True)
    t.add_column("label", style="dim", width=14, no_wrap=True)
    t.add_column("value", min_width=16, no_wrap=True)
    t.add_column("note",  ratio=1, overflow="ellipsis", no_wrap=True)

    if not st.pm_slug:
        t.add_row("Market", "[yellow]no live Polymarket market[/yellow]", "")
        return Panel(t, title="POLYMARKET WINDOW", box=bx.ROUNDED, expand=True)

    now  = time.time()
    left = (st.win_end or now) - now
    mm, ss = divmod(max(0, int(left)), 60)
    hh, mm = divmod(mm, 60)
    t.add_row("Market", f"closes in [bold]{hh:d}:{mm:02d}:{ss:02d}[/bold]",
              f"[cyan]{st.pm_slug}[/cyan]")

    ptb, ref = st.price_to_beat, st.ref_price
    src = "Chainlink" if st.resolves_on == "chainlink" else "Binance"
    age = st.ref_age
    if st.ref_stale:
        stale = "no tick yet" if age is None else f"{age:.0f}s old"
        t.add_row("Live price", _p(ref) if ref else "[dim]—[/dim]",
                  f"[red bold]STALE · {stale} · signals blocked[/red bold]")
    else:
        t.add_row("Live price", _p(ref) if ref else "[dim]—[/dim]", f"[dim]{src} · settlement source[/dim]")
    if ptb and ref:
        d   = ref - ptb
        c   = "green" if d >= 0 else "red"
        t.add_row("Price to beat", _p(ptb),
                  f"[{c}]{d:+,.2f} ({d / ptb * 100:+.3f}%)[/{c}] [dim]{st.ptb_source}[/dim]")
    else:
        note = "window not started" if st.win_start and now < st.win_start else "fetching…"
        t.add_row("Price to beat", "[dim]—[/dim]", f"[dim]{note}[/dim]")

    t.add_row("UP bid/ask", f"{_f(st.pm_up_bid)} / {_f(st.pm_up)}", "")
    t.add_row("DOWN bid/ask", f"{_f(st.pm_dn_bid)} / {_f(st.pm_dn)}", "")

    edge = edge_finder.estimate(st, timeframe)
    p = edge["p_up"]
    if p is None:
        t.add_row("Model P(up)", "[dim]—[/dim]", "")
    else:
        t.add_row("Model P(up)", f"[bold]{p:.3f}[/bold]",
                  f"[dim]lognormal, σ {config.VOL_LOOKBACK} candles[/dim]")
        for name, key in (("Net edge UP", "up_edge"), ("Net edge DOWN", "down_edge")):
            value = edge[key]
            if value is None:
                continue
            candidate = value >= config.EDGE_MIN_NET
            c = "green bold" if candidate else "red" if value < 0 else "yellow"
            note = "RESEARCH SIGNAL" if candidate else "after fee + buffer"
            t.add_row(name, f"[{c}]{value:+.3f}[/{c}]", f"[{c}]{note}[/{c}]")
        if edge["up_edge"] is None and edge["down_edge"] is None:
            t.add_row("Depth check", f"[dim]waiting for {config.EDGE_PROBE_SHARES:g}-share ask depth[/dim]",
                      f"[dim]VWAP within {config.EDGE_MAX_WALK:.3f} of best ask[/dim]")
    t.add_row("Forecast score", st.edge_summary, "[dim]read-only ledger[/dim]")

    return Panel(t, title="POLYMARKET WINDOW", box=bx.ROUNDED, expand=True)


def render(st, coin, tf) -> "_Group":
    header = _header(st, coin, tf)

    grid = Table(box=None, pad_edge=False, show_header=False, expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(
        Group(_ob_panel(st), _ta_panel(st)),
        _flow_panel(st),
    )

    return _Group(header, _market_panel(st, tf), grid, _signals_panel(st))
