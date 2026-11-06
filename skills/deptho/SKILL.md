---
name: deptho
description: Check a crypto perpetual-futures backtest against the trades that actually happened. Use when the user asks to check their backtest fills, whether a backtest is realistic, what slippage their orders would really have paid, or how funding and fees affect a perp backtest; when they want a tearsheet that splits returns into fees, funding and slippage; or a shareable "fill gap" card. Works locally on files the user has, with the free deptho library or the deptho-mcp tools.
---

# Deptho: reality-check a perp backtest

Deptho answers one question: what would this backtest's orders really have paid? It replays a
backtest's market orders against the trade prints that followed them, then charges the measured
cost, plus fees and funding, to the strategy's positions. Everything runs on the user's machine on
files they already have.

## When to use it

- "Check my backtest fills", "is my backtest realistic", "what slippage would I get":
  replay the orders with `simulate_taker_fills`, then summarise with `slippage_bps`.
- "Funding and fees in my perp backtest", "what did costs take": `pnl_from_positions`, then
  `tearsheet` for net vs gross and the cost drag split into fees, funding and slippage.
- "Something I can share": the report's `fill_gap_card`, an SVG (and PNG with `deptho[png]`) of the
  bar-close return, the return after replayed fills, and the gap between them in points.

If the deptho-mcp server is connected, use its tools instead of writing code (table below).

## What you need from the user

1. **Orders**: one row per market order. As a pandas DataFrame: a UTC `DatetimeIndex` (the decision
   time), `side` (`+1` buy, `-1` sell) and `qty` in base units (BTC, not dollars). As a CSV for the
   MCP tools:

   ```csv
   time,side,qty
   2026-09-30T08:00:00Z,buy,0.5
   2026-09-30T13:30:00Z,sell,2.0
   2026-09-30T18:45:00Z,1,5.0
   ```

   `time` is ISO 8601 or epoch (s, ms, us or ns; one kind per file); `side` is `+1`/`-1` or `buy`/`sell`.
2. **Trades for the same period**: a Binance archive `aggTrades` file (`.zip` or `.csv`) or a
   Tardis.dev `trades` file (`.csv` or `.csv.gz`). Ask for the file; don't download data unless
   the user asks you to.
3. For costs: bar prices and positions (exposure as a fraction of equity, `1.0` = 1x long,
   decided at each bar's close), and optionally funding rates (positive = longs pay).

## With the library

```python
import pandas as pd
import deptho as dp

trades = dp.datasets.read_aggtrades("BTCUSDT-aggTrades-2026-09-30.zip")   # price, qty, side (aggressor)

orders = pd.DataFrame(
    {"side": [1, -1, 1], "qty": [0.5, 2.0, 5.0]},
    index=pd.to_datetime(["2026-09-30 08:00", "2026-09-30 13:30", "2026-09-30 20:15"], utc=True),
)
fills = dp.slippage_bps(dp.simulate_taker_fills(trades, orders, fill_model="trade_prints", max_wait="5min"))
cost = fills["slippage_bps"]                       # bps vs the last trade before each order; + = cost
print(f"{len(fills)} of {len(orders)} filled; mean {cost.mean():.2f} bps, "
      f"median {cost.median():.2f}, p90 {cost.quantile(0.9):.2f}")
```

Then charge the measured cost, with fees and funding, to the positions, and build the report:

```python
import glob

import numpy as np

klines = pd.concat(dp.datasets.read_klines(p) for p in sorted(glob.glob("BTCUSDT-1m-*.zip")))
close = klines["close"].shift(freq="1min")         # klines are stamped at their open
prices = close.resample("1h", label="right", closed="right").last().dropna()
positions = np.sign(prices.diff(24)).fillna(0.0)   # the user's own positions go here
funding = pd.concat(dp.datasets.read_funding(p) for p in sorted(glob.glob("BTCUSDT-fundingRate-*.zip")))

pnl = dp.pnl_from_positions(prices, positions, funding=funding, fee_bps=5,
                            slippage_bps=max(cost.mean(), 0.0))
report = dp.tearsheet(pnl=pnl, benchmark=prices.pct_change(), fills=fills, title="BTCUSDT perp momentum")
report.save("report.html")                         # one self-contained HTML file
card = report.fill_gap_card("btc-card", symbol="BTCUSDT")
print(pnl[["gross", "fees", "funding", "slippage", "net"]].sum())
```

Charge the **mean** replay cost, not the median: the median hides the tail. A position decided at a
bar's close earns from the next bar (lookahead-safe by default).

## With the deptho-mcp tools

| tool | use it for |
|---|---|
| `replay_orders(orders_csv, trades_file)` | the fill check: writes the fills CSV, returns mean, median, p90, p99 slippage (bps) and how many orders were not filled |
| `tearsheet(bars_csv, funding_csv=None, fee_bps=0, slippage_bps=0)` | the cost split: bars CSV with `time`, `price`, `position`; pass the mean from `replay_orders` as `slippage_bps` |

A bars CSV for `tearsheet`:

```csv
time,price,position
2026-09-30T00:00:00Z,63000.0,0.0
2026-09-30T01:00:00Z,63150.5,1.0
2026-09-30T02:00:00Z,63090.0,1.0
2026-09-30T03:00:00Z,62980.0,-0.5
```

Install, if it isn't yet:

```bash
pip install deptho deptho-mcp
claude mcp add deptho -- deptho-mcp
```

## Limits: say these when you report results

- Fills are modeled from **trade prints**: each order fills at the VWAP of the same-side trades after
  it until its size is covered. There is **no order book depth and no queue position**, and each
  order sees the historical tape as if it hadn't traded.
- Orders the following trades can't cover (or, with `max_wait`, can't cover in time) are dropped,
  not filled at far-off prices. Report how many were dropped.
- A negative mean cost means the orders did slightly better than the last trade before them; charge
  0, and the card then says the replayed fills matched the backtest.
- Fees are a flat rate per unit of turnover; funding is charged on the position held at each funding
  time. Binance archive files cover USD-M perpetuals.
- Everything is local: the library makes no network call unless the user asks it to download
  Binance's public archive (`dp.datasets.download`, `python -m deptho.demo`).

Deptho Pro, a paid add-on, handles tick data bigger than memory; nothing in this skill needs it.
