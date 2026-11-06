"""skills/deptho/SKILL.md: every code sample in it runs, offline, and it says what it should."""
from __future__ import annotations

import re
import socket
from datetime import date
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
SKILL = PKG / "skills" / "deptho" / "SKILL.md"


def _parts():
    text = SKILL.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    assert m, "SKILL.md must start with a --- frontmatter block"
    meta = dict(line.split(": ", 1) for line in m.group(1).splitlines())
    blocks = re.findall(r"^ *```(\w+)\n(.*?)^ *```", m.group(2), re.S | re.M)
    return meta, m.group(2), [(lang, _dedent(code)) for lang, code in blocks]


def _dedent(code: str) -> str:
    import textwrap

    return textwrap.dedent(code)


def test_frontmatter_names_the_skill_and_triggers_on_backtest_questions():
    meta, _, _ = _parts()
    assert set(meta) == {"name", "description"} and meta["name"] == "deptho"
    d = meta["description"].lower()
    assert len(meta["description"]) <= 1024
    for trigger in ("check their backtest fills", "backtest is realistic", "funding and fees", "perp backtest",
                    "slippage", "tearsheet", "fill gap"):
        assert trigger in d, trigger


def test_every_code_block_is_a_kind_the_tests_run():
    _, _, blocks = _parts()
    assert {lang for lang, _ in blocks} == {"python", "csv", "bash"}
    assert sum(lang == "python" for lang, _ in blocks) == 2


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise OSError("the skill's samples must not use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_python_samples_run_offline_on_archive_files(tmp_path, monkeypatch, no_network):
    """The samples, in order, in one namespace, in a folder of Binance-format files (synthetic)."""
    pytest.importorskip("PIL")
    from deptho import _synthetic, config

    monkeypatch.setattr(config, "upgrade_prompts", False)
    _synthetic.make(tmp_path, tick_day=date(2026, 9, 30), kline_days=30, trades_per_day=200_000)
    monkeypatch.chdir(tmp_path)
    _, _, blocks = _parts()
    ns: dict = {}
    for lang, code in blocks:
        if lang == "python":
            exec(compile(code, str(SKILL), "exec"), ns)
    assert len(ns["fills"]) == len(ns["orders"]) == 3                 # every sample order filled
    assert ns["cost"].abs().max() < 50
    assert (tmp_path / "report.html").stat().st_size > 10_000
    assert ns["card"]["svg"].exists() and ns["card"]["png"].exists()
    assert set(ns["pnl"].columns) >= {"gross", "fees", "funding", "slippage", "net"}


def test_csv_samples_are_what_the_mcp_tools_read(tmp_path, no_network):
    import pandas as pd

    from deptho_mcp import server

    _, _, blocks = _parts()
    orders_csv, bars_csv = (code for lang, code in blocks if lang == "csv")
    (tmp_path / "orders.csv").write_text(orders_csv)
    orders = server.read_orders(str(tmp_path / "orders.csv"))
    assert list(orders["side"]) == [1, -1, 1] and list(orders["qty"]) == [0.5, 2.0, 5.0]
    assert list(orders.index) == [pd.Timestamp(t, tz="UTC") for t in
                                  ("2026-09-30 08:00", "2026-09-30 13:30", "2026-09-30 18:45")]
    (tmp_path / "bars.csv").write_text(bars_csv)
    out = server.tearsheet(str(tmp_path / "bars.csv"), fee_bps=5, slippage_bps=1.0)
    assert Path(out["report_html"]).exists() and out["costs_fraction_of_equity"]["fees"] > 0


def test_shell_sample_names_real_packages_and_the_real_command():
    """pip can't install from inside the tests (no network), so the commands are checked against
    the packages' own metadata: the package names and the deptho-mcp entry point."""
    from importlib.metadata import metadata

    tomllib = pytest.importorskip("tomllib")                                    # Python 3.11+
    _, _, blocks = _parts()
    (bash,) = [code for lang, code in blocks if lang == "bash"]
    lines = bash.strip().splitlines()
    free = metadata("deptho")                                                   # the installed free library
    mcp = tomllib.loads((PKG / "pyproject.toml").read_text())["project"]
    assert lines[0] == f"pip install {free['Name']} {mcp['name']}"
    assert lines[1] == "claude mcp add deptho -- deptho-mcp" and "deptho-mcp" in mcp["scripts"]
    assert lines[1] in (PKG / "README.md").read_text(encoding="utf-8")         # same as the README says
    assert "png" in free.get_all("Provides-Extra")                              # deptho[png], as the text says
    # The samples use max_wait and fill_gap_card, both new in deptho 0.1.2.
    assert any(d.replace(" ", "").startswith("deptho>=0.1.2") for d in mcp["dependencies"])


def test_free_tools_only_and_no_links_or_hidden_text():
    meta, body, _ = _parts()
    text = SKILL.read_text(encoding="utf-8")
    pro_lines = [line for line in body.splitlines() if "Pro" in line or "pro_" in line]
    assert pro_lines == ["Deptho Pro, a paid add-on, handles tick data bigger than memory; nothing in this skill needs it."]
    assert "deptho_pro" not in text and "license" not in text.lower()
    assert not re.search(r"https?://", text)                                     # no links, no fetches
    assert text.isascii() or all(ord(c) < 0x2500 for c in text)                 # plain text: no hidden characters
    assert not re.search(r"[​-‏‪-‮⁠-⁤﻿]", text)
    assert not re.search(r"base64|eval\(|exec\(|__import__|subprocess|os\.system", text)
    for word in ("trade prints", "no order book depth", "queue position", "mean", "lookahead-safe by default"):
        assert word in text, word
