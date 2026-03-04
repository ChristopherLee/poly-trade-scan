#!/usr/bin/env python
"""Shared helpers for copy-trade analysis scripts."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def resolve_wallet(conn: sqlite3.Connection, wallet_ref: str) -> tuple[str, str]:
    if wallet_ref.lower().startswith("0x"):
        row = conn.execute(
            "SELECT address, COALESCE(alias, '') AS alias FROM wallets WHERE lower(address) = ?",
            (wallet_ref.lower(),),
        ).fetchone()
        if row:
            return row["address"], row["alias"]
        return wallet_ref.lower(), ""

    row = conn.execute(
        """
        SELECT address, COALESCE(alias, '') AS alias
        FROM wallets
        WHERE alias = ?
           OR lower(alias) = lower(?)
        ORDER BY added_at DESC
        LIMIT 1
        """,
        (wallet_ref, wallet_ref),
    ).fetchone()
    if row:
        return row["address"], row["alias"]
    raise SystemExit(f"Wallet alias not found in DB: {wallet_ref}")


def fmt_money(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"${float(value):,.2f}"


def fmt_pct(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f}%"


def fmt_num(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.{digits}f}"


def fmt_seconds(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.1f}s"


def bucket_realized_points(points: list[dict], bucket_seconds: int) -> list[dict]:
    buckets: dict[int, dict] = {}
    for point in points:
        ts = int(float(point.get("ts") or 0))
        bucket_ts = (ts // bucket_seconds) * bucket_seconds
        bucket = buckets.setdefault(
            bucket_ts,
            {"ts": bucket_ts, "realized_pnl": 0.0, "trade_count": 0, "wins": 0, "losses": 0, "flat": 0},
        )
        realized = float(point.get("realized_pnl") or 0.0)
        bucket["realized_pnl"] += realized
        bucket["trade_count"] += 1
        if realized > 0.01:
            bucket["wins"] += 1
        elif realized < -0.01:
            bucket["losses"] += 1
        else:
            bucket["flat"] += 1

    return [buckets[key] for key in sorted(buckets)]


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))

    def render(values: list[str]) -> str:
        return "  ".join(str(value).ljust(widths[idx]) for idx, value in enumerate(values))

    print(render(headers))
    print(render(["-" * width for width in widths]))
    for row in rows:
        print(render(row))
