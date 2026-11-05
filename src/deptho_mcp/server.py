"""deptho-mcp: Deptho's backtest checks as tools for coding assistants (MCP, stdio).

    deptho-mcp            # started by Claude Code, Claude Desktop, Cursor, ... (see README)

Tools:
  replay_orders  orders CSV + trades file -> fills CSV and a slippage summary
  tearsheet      bars CSV (time, price, position) -> HTML report path and headline numbers
  pro_status     whether Deptho Pro is installed and licensed on this machine
  pro_sync       (Deptho Pro) Binance USD-M archive days -> local memory-mapped dataset
  pro_replay     (Deptho Pro) orders CSV replayed against a synced dataset

Local only. Tools read and write files on this machine and return summaries to the assistant.
Nothing is sent to Deptho. The one download is pro_sync, which fetches Binance's public archive
(data.binance.vision) when you ask it to, like `deptho_pro.sync`.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from mcp.server.mcpserver import MCPServer

from . import __version__

# Links to deptho.io from this package go through its source page.
WEBSITE = "https://deptho.io/go/mcp"

server = MCPServer(
    name="deptho",
    title="Deptho",
    version=__version__,
    website_url=WEBSITE,
    instructions=(
        "Deptho checks crypto perpetual backtests at tick level, on the user's machine. Use replay_orders "
        "to estimate what a backtest's market orders would have paid against the trades that followed them "
        "(Binance archive aggTrades or Tardis.dev trades files), and tearsheet to attribute returns to fees, "
        "funding and slippage. Fills are modeled from trade prints: no order book depth or queue position. "
        "All paths are local files."
    ),
)


# --- inputs --------------------------------------------------------------------------------------

def _path(p: str, must_exist: bool = True) -> Path:
    path = Path(p).expanduser().resolve()
    if must_exist and not path.is_file():
        raise ValueError(f"no such file: {path}")
    return path


def _time_index(s: pd.Series) -> pd.DatetimeIndex:
    """ISO strings or epoch numbers (s, ms, us or ns, by magnitude) -> UTC index."""
    if pd.api.types.is_numeric_dtype(s):
        v = s.astype("int64")
        unit = next(u for lim, u in ((1e17, "ns"), (1e14, "us"), (1e11, "ms"), (0, "s")) if v.abs().max() >= lim)
        return pd.DatetimeIndex(pd.to_datetime(v, unit=unit, utc=True))
    return pd.DatetimeIndex(pd.to_datetime(s, utc=True))


def read_orders(path: str) -> pd.DataFrame:
    """Orders CSV: `time` (ISO or epoch), `side` (+1/-1 or buy/sell), `qty` (base units)."""
    df = pd.read_csv(_path(path))
    missing = {"time", "side", "qty"} - set(df.columns)
    if missing:
        raise ValueError(f"orders CSV needs columns time, side, qty; missing {sorted(missing)}")
    side = df["side"]
    if not pd.api.types.is_numeric_dtype(side):
        side = side.astype(str).str.strip().str.lower().map({"buy": 1, "sell": -1, "1": 1, "-1": -1})
    if side.isna().any() or not side.isin([1, -1]).all():
        raise ValueError("orders `side` must be +1/-1 or buy/sell")
    out = pd.DataFrame({"side": side.astype("int64").to_numpy(), "qty": df["qty"].astype(float).to_numpy()},
                       index=_time_index(df["time"]))
    return out.sort_index(kind="stable")


def read_trades(path: str) -> pd.DataFrame:
    """A Binance archive aggTrades file (.zip/.csv) or a Tardis.dev trades file (.csv/.csv.gz)."""
    from deptho import datasets as bn

    p = _path(path)
    head = pd.read_csv(p, nrows=0).columns if p.suffix.lower() != ".zip" else []
    if "side" in head and "amount" in head:
        return bn.read_tardis_trades(p)
    return bn.read_aggtrades(p)


def _summary(fills: pd.DataFrame, n_orders: int) -> dict:
    import deptho as dp

    cost = dp.slippage_bps(fills)["slippage_bps"] if len(fills) else pd.Series(dtype=float)
    q = (lambda x: round(float(cost.quantile(x)), 3)) if len(cost) else (lambda x: None)
    return {
        "orders": n_orders, "filled": int(len(fills)), "not_filled": int(n_orders - len(fills)),
        "slippage_bps": {"mean": round(float(cost.mean()), 3) if len(cost) else None, "median": q(0.5),
                         "p90": q(0.9), "p99": q(0.99)},
        "fill_model": "trade_prints: VWAP of the same-side trade prints after each order until its size is "
                      "covered; no order book depth or queue position",
    }


def _write_fills(fills: pd.DataFrame, out: str | None, default: Path) -> str:
    import deptho as dp

    path = _path(out, must_exist=False) if out else default
    dp.slippage_bps(fills).rename_axis("time").to_csv(path)
    return str(path)


# --- tools ---------------------------------------------------------------------------------------

@server.tool()
def replay_orders(orders_csv: str, trades_file: str, fills_csv: str | None = None) -> dict:
    """Replay a backtest's market orders against the trades that followed them.

    orders_csv: CSV with columns time (ISO or epoch), side (+1/-1 or buy/sell), qty (base units).
    trades_file: Binance archive aggTrades file (.zip or .csv) or Tardis.dev trades file (.csv or .csv.gz)
    covering the orders' period. Writes one row per filled order (side, qty, price, ref_price,
    slippage_bps) to fills_csv (default: next to the orders, <name>.fills.csv) and returns a summary.
    Slippage is in bps against the last trade before each order; positive = cost.
    """
    import deptho as dp

    orders = read_orders(orders_csv)
    fills = dp.simulate_taker_fills(read_trades(trades_file), orders, fill_model="trade_prints")
    default = _path(orders_csv).with_suffix(".fills.csv")
    return {**_summary(fills, len(orders)), "fills_csv": _write_fills(fills, fills_csv, default)}


@server.tool()
def tearsheet(bars_csv: str, report_html: str | None = None, funding_csv: str | None = None,
              fee_bps: float = 0.0, slippage_bps: float = 0.0, title: str = "Strategy") -> dict:
    """Write Deptho's HTML tearsheet for a strategy and return its path and headline numbers.

    bars_csv: CSV with columns time, price (mark/close) and position (exposure as a fraction of equity,
    1.0 = 1x long, decided at each bar's close; it earns from the next bar). funding_csv (optional):
    time and funding_rate (positive = longs pay). fee_bps and slippage_bps: per unit of turnover; use the
    slippage measured with replay_orders rather than a guess. Writes one self-contained HTML file
    (default: next to bars_csv, <name>.report.html).
    """
    import deptho as dp

    bars = pd.read_csv(_path(bars_csv))
    missing = {"time", "price", "position"} - set(bars.columns)
    if missing:
        raise ValueError(f"bars CSV needs columns time, price, position; missing {sorted(missing)}")
    idx = _time_index(bars["time"])
    prices = pd.Series(bars["price"].astype(float).to_numpy(), index=idx)
    positions = pd.Series(bars["position"].astype(float).to_numpy(), index=idx)
    funding = None
    if funding_csv:
        f = pd.read_csv(_path(funding_csv))
        rate = next((c for c in ("funding_rate", "fundingRate", "rate", "last_funding_rate") if c in f.columns), None)
        if "time" not in f.columns or rate is None:
            raise ValueError("funding CSV needs columns time and funding_rate")
        funding = pd.Series(f[rate].astype(float).to_numpy(), index=_time_index(f["time"]))
    pnl = dp.pnl_from_positions(prices, positions, funding=funding, fee_bps=fee_bps, slippage_bps=slippage_bps)
    report = dp.tearsheet(pnl=pnl, benchmark=prices.pct_change(), title=title)
    out = _path(report_html, must_exist=False) if report_html else _path(bars_csv).with_suffix(".report.html")
    report.save(out)
    m = report.metrics
    keep = ("total_return", "cagr", "sharpe", "sortino", "max_drawdown")
    headline = {k: round(float(m[k]), 4) for k in keep if k in m and m[k] is not None and np.isfinite(m[k])}
    costs = {k: round(float(pnl[k].sum()), 6) for k in ("fees", "funding", "slippage")}
    return {"report_html": str(out), "metrics": headline, "costs_fraction_of_equity": costs}


def _pro():
    """deptho_pro if installed and licensed, else None (and why)."""
    try:
        import deptho_pro
    except ImportError:
        return None, "Deptho Pro isn't installed (see " + WEBSITE + ")"
    st = deptho_pro.license_status()
    return (deptho_pro, "licensed") if st.valid else (None, f"Deptho Pro license: {st.reason}")


@server.tool()
def pro_status() -> dict:
    """Whether Deptho Pro is installed and licensed here. pro_sync and pro_replay need it."""
    mod, why = _pro()
    return {"available": mod is not None, "detail": why}


@server.tool()
def pro_sync(symbol: str, start: str, end: str) -> dict:
    """(Deptho Pro) Download Binance USD-M daily trade files from Binance's public archive for start..end
    (YYYY-MM-DD, inclusive) and append them to one local memory-mapped dataset, "<SYMBOL>-aggTrades".
    Days already on disk aren't downloaded again. Returns the dataset name and size."""
    dpp, why = _pro()
    if dpp is None:
        raise ValueError(why)
    sym = symbol.upper()
    f = dpp.sync(sym, start, end, log=lambda m: None)
    return {"dataset": f"{sym}-aggTrades", "rows": int(len(f)), "columns": list(f.columns)}


@server.tool()
def pro_replay(orders_csv: str, dataset: str, fills_csv: str | None = None) -> dict:
    """(Deptho Pro) Replay an orders CSV (time, side, qty) against a dataset made by pro_sync (the name it
    returned, e.g. "BTCUSDT-aggTrades"), straight off the memory-mapped columns. Same output as replay_orders."""
    dpp, why = _pro()
    if dpp is None:
        raise ValueError(why)
    orders = read_orders(orders_csv)
    fills = dpp.dataset(dataset).replay(orders, fill_model="trade_prints")
    default = _path(orders_csv).with_suffix(".fills.csv")
    return {**_summary(fills, len(orders)), "fills_csv": _write_fills(fills, fills_csv, default)}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
