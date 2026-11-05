"""pytest packages/deptho-mcp/tests: the MCP server over real stdio, and its tools in-process."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import deptho as dp
from deptho import datasets as bn
from deptho_mcp import server as srv

PKG = Path(__file__).resolve().parents[1]     # packages/deptho-mcp here; the root of its public repo
ROOT = PKG.parents[1]                         # this monorepo (tools/keygen.py), when inside it


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    from deptho._synthetic import make

    d = tmp_path_factory.mktemp("mcp")
    make(d, tick_day=date(2026, 9, 30), kline_days=2, trades_per_day=20_000)
    agg = next(d.glob("*aggTrades*.zip"))
    ticks = bn.read_aggtrades(agg)
    rng = np.random.default_rng(3)
    at = ticks.index[np.sort(rng.integers(100, len(ticks) - 500, 40))]
    pd.DataFrame({"time": at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "side": rng.choice(["buy", "sell"], 40),
                  "qty": rng.choice([0.1, 0.5, 2.0], 40)}).to_csv(d / "orders.csv", index=False)
    # The same trades as a Tardis.dev `trades` file.
    raw = bn._read(agg, bn.AGGTRADES_COLS)
    maker = raw["is_buyer_maker"].astype(str).str.lower().isin(["true", "1"])
    pd.DataFrame({"exchange": "binance-futures", "symbol": "btcusdt",
                  "timestamp": raw["transact_time"].astype("int64") * 1000,
                  "local_timestamp": raw["transact_time"].astype("int64") * 1000 + 900, "id": raw["agg_trade_id"],
                  "side": np.where(maker, "sell", "buy"), "price": raw["price"], "amount": raw["quantity"]}
                 ).to_csv(d / "tardis_trades.csv.gz", index=False)
    k = pd.concat([bn.read_klines(p) for p in sorted(d.glob("*-1m-*.zip"))])
    px = k["close"].resample("5min").last().dropna()
    pd.DataFrame({"time": px.index.strftime("%Y-%m-%d %H:%M:%S"), "price": px.to_numpy(),
                  "position": np.sign(px.diff(12)).fillna(0.0).to_numpy()}).to_csv(d / "bars.csv", index=False)
    return {"dir": d, "agg": agg, "ticks": ticks}


def expected_summary(data) -> dict:
    orders = srv.read_orders(str(data["dir"] / "orders.csv"))
    return srv._summary(dp.simulate_taker_fills(data["ticks"], orders), len(orders))


def test_over_stdio(data):
    """Start `python -m deptho_mcp.server` and talk to it as an assistant would."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "deptho_mcp.server"])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
            init = await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            rep = await s.call_tool("replay_orders", {"orders_csv": str(data["dir"] / "orders.csv"),
                                                      "trades_file": str(data["agg"])})
            ts = await s.call_tool("tearsheet", {"bars_csv": str(data["dir"] / "bars.csv"), "fee_bps": 5.0,
                                                 "slippage_bps": 2.0, "title": "BTC test"})
            return init, tools, rep, ts

    init, tools, rep, ts = asyncio.run(run())
    assert init.server_info.name == "deptho"
    assert tools == {"replay_orders", "tearsheet", "pro_status", "pro_sync", "pro_replay"}
    assert not rep.is_error, rep.content
    got = rep.structured_content or json.loads(rep.content[0].text)
    want = expected_summary(data)
    assert {k: got[k] for k in want} == want and got["filled"] > 30
    fills = pd.read_csv(got["fills_csv"])
    assert list(fills.columns)[:5] == ["time", "side", "qty", "price", "ref_price"] and "slippage_bps" in fills
    assert not ts.is_error, ts.content
    t = ts.structured_content or json.loads(ts.content[0].text)
    html = Path(t["report_html"]).read_text(encoding="utf-8")
    assert "<title>BTC test - Deptho</title>" in html and {"sharpe", "max_drawdown"} <= set(t["metrics"])
    assert t["costs_fraction_of_equity"]["fees"] > 0 and t["costs_fraction_of_equity"]["slippage"] > 0


def test_tardis_trades_give_the_same_replay(data, tmp_path):
    a = srv.replay_orders(str(data["dir"] / "orders.csv"), str(data["agg"]), str(tmp_path / "a.csv"))
    b = srv.replay_orders(str(data["dir"] / "orders.csv"), str(data["dir"] / "tardis_trades.csv.gz"),
                          str(tmp_path / "b.csv"))
    assert {k: v for k, v in a.items() if k != "fills_csv"} == {k: v for k, v in b.items() if k != "fills_csv"}
    pd.testing.assert_frame_equal(pd.read_csv(tmp_path / "a.csv"), pd.read_csv(tmp_path / "b.csv"))


def test_bad_inputs_say_what_is_wrong(data, tmp_path):
    bad = tmp_path / "o.csv"
    pd.DataFrame({"time": ["2026-09-30T00:00:00Z"], "side": ["long"], "qty": [1]}).to_csv(bad, index=False)
    with pytest.raises(ValueError, match="buy/sell"):
        srv.replay_orders(str(bad), str(data["agg"]))
    with pytest.raises(ValueError, match="no such file"):
        srv.replay_orders(str(tmp_path / "missing.csv"), str(data["agg"]))
    pd.DataFrame({"t": [1]}).to_csv(bad, index=False)
    with pytest.raises(ValueError, match="time, side, qty"):
        srv.replay_orders(str(bad), str(data["agg"]))


def test_pro_tools(data, tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    pytest.importorskip("deptho_pro")
    keygen = pytest.importorskip("keygen", reason="needs tools/keygen.py from the Deptho monorepo")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import deptho_pro
    from deptho_pro import license as lic

    monkeypatch.delenv("DEPTHO_LICENSE", raising=False)
    monkeypatch.setenv("DEPTHO_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("HOME", str(tmp_path))                           # no license file from this machine
    assert srv.pro_status()["available"] is False
    with pytest.raises(ValueError, match="license"):
        srv.pro_replay(str(data["dir"] / "orders.csv"), "BTCUSDT-aggTrades")
    sk = Ed25519PrivateKey.generate()                                   # throwaway key, never the real one
    monkeypatch.setattr(lic.verify, "__defaults__", (None, keygen.public_b64(sk)))
    monkeypatch.setenv("DEPTHO_LICENSE", keygen.issue(sk, "t@x", date.today() + timedelta(days=2)))
    assert srv.pro_status() == {"available": True, "detail": "licensed"}
    # pro_sync downloads from Binance's archive; point it at the local file instead.
    monkeypatch.setattr(deptho_pro, "sync", lambda sym, start, end, **k: deptho_pro.build_dataset(
        f"{sym}-aggTrades", [data["agg"]], log=lambda m: None))
    s = srv.pro_sync("btcusdt", "2026-09-30", "2026-09-30")
    assert s["dataset"] == "BTCUSDT-aggTrades" and s["rows"] == len(data["ticks"])
    pro = srv.pro_replay(str(data["dir"] / "orders.csv"), s["dataset"], str(tmp_path / "p.csv"))
    free = srv.replay_orders(str(data["dir"] / "orders.csv"), str(data["agg"]), str(tmp_path / "f.csv"))
    assert {k: v for k, v in pro.items() if k != "fills_csv"} == {k: v for k, v in free.items() if k != "fills_csv"}


def test_local_only():
    """The server itself makes no network calls; its one link goes through deptho.io/go/mcp."""
    src = (PKG / "src" / "deptho_mcp" / "server.py").read_text(encoding="utf-8")
    for lib in ("urllib", "requests", "httpx", "socket", "http.client"):
        assert f"import {lib}" not in src and f"from {lib}" not in src, lib
    import re
    assert set(re.findall(r"https?://[^\s\"')]+", src)) == {"https://deptho.io/go/mcp"}
    readme = (PKG / "README.md").read_text(encoding="utf-8")
    assert set(re.findall(r"https?://deptho\.io[^\s)\"]*", readme)) == {"https://deptho.io/go/mcp"}
    meta = json.loads((PKG / "server.json").read_text(encoding="utf-8"))
    assert f"mcp-name: {meta['name']}" in readme                       # MCP Registry ownership check
    from deptho_mcp import __version__

    assert meta["version"] == meta["packages"][0]["version"] == __version__
    assert len(meta["description"]) <= 100


def test_server_json_matches_the_registry_schema():
    """Validated against the official schema when a copy is available (CI may be offline)."""
    import os

    jsonschema = pytest.importorskip("jsonschema")
    path = os.environ.get("MCP_SERVER_SCHEMA")
    if not path:
        pytest.skip("set MCP_SERVER_SCHEMA to a downloaded server.schema.json to validate")
    meta = json.loads((PKG / "server.json").read_text(encoding="utf-8"))
    jsonschema.validate(meta, json.loads(Path(path).read_text(encoding="utf-8")))
