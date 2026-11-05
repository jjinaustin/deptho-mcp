# deptho-mcp

<!-- mcp-name: io.deptho/deptho-mcp -->

An MCP server that gives coding assistants (Claude Code, Claude Desktop, Cursor and others) Deptho's
backtest checks: replay a backtest's market orders against the trades that followed them, and write a
tearsheet that splits returns into fees, funding and slippage. It runs on your machine over stdio,
reads and writes local files, and sends nothing anywhere.

## Tools

| tool | does |
|---|---|
| `replay_orders` | orders CSV + trades file → fills CSV and a slippage summary (mean, median, p90, p99 bps) |
| `tearsheet` | bars CSV (`time`, `price`, `position`) → self-contained HTML report, its path and headline numbers |
| `pro_status` | whether Deptho Pro is installed and licensed here |
| `pro_sync` | Deptho Pro: download Binance USD-M days from the public archive into a local memory-mapped dataset |
| `pro_replay` | Deptho Pro: replay an orders CSV against that dataset |

**Orders CSV:** `time` (ISO 8601 or epoch), `side` (`+1`/`-1` or `buy`/`sell`), `qty` (base units, e.g. BTC).
**Trades file:** a Binance archive `aggTrades` file (`.zip` or `.csv`) or a Tardis.dev `trades` file
(`.csv` or `.csv.gz`) covering the orders' period.

Fills are modeled from trade prints: each order fills at the VWAP of the same-side trades after it until
its size is covered. Order book depth and queue position are not modeled.

## Install

```bash
pip install deptho-mcp
```

Use a virtual environment if you like; then point your assistant at that environment's `deptho-mcp`
(`which deptho-mcp` prints the full path). For the Pro tools, install Deptho Pro into the same
environment with your license key.

### Claude Code

```bash
claude mcp add deptho -- deptho-mcp
```

### Claude Desktop

Settings → Developer → Edit Config, then add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "deptho": { "command": "/full/path/to/deptho-mcp" }
  }
}
```

### Cursor

Add to `~/.cursor/mcp.json` (or `.cursor/mcp.json` in a project):

```json
{
  "mcpServers": {
    "deptho": { "command": "/full/path/to/deptho-mcp" }
  }
}
```

Then ask, for example: *"Replay orders.csv against BTCUSDT-aggTrades-2026-09-30.zip and tell me the
p90 slippage."*

## Privacy

Every tool works on local files. Nothing is sent to Deptho. The only download is `pro_sync`, which fetches
Binance's public archive (data.binance.vision) when you ask it to.

## Links

- Deptho: https://deptho.io/go/mcp
- Source: https://github.com/jjinaustin/deptho-mcp

License: Apache-2.0. Market data comes from Binance's public archive or from files you supply; Deptho is
not affiliated with Binance or Tardis.dev.
