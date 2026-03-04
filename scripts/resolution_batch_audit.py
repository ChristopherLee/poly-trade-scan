#!/usr/bin/env python
"""Inspect realized PnL batches to find resolution-driven concentration."""

from __future__ import annotations

import argparse
from pathlib import Path

from _analysis_common import bucket_realized_points, fmt_money, get_connection, print_table, resolve_wallet
from dashboard import _build_wallet_realized_trade_points


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit resolution/close batches for a wallet.")
    parser.add_argument("--wallet", required=True, help="Wallet alias or address from the DB.")
    parser.add_argument("--db", type=Path, default=Path("paper_trades.db"), help="SQLite DB path.")
    parser.add_argument("--bucket-seconds", type=int, default=60, help="Bucket size for close batches.")
    parser.add_argument("--top", type=int, default=15, help="How many largest batches to print.")
    args = parser.parse_args()

    conn = get_connection(args.db)
    try:
        wallet, alias = resolve_wallet(conn, args.wallet)
        points = _build_wallet_realized_trade_points(conn, wallet)
    finally:
        conn.close()

    if not points:
        raise SystemExit("No realized trade points found for this wallet.")

    buckets = bucket_realized_points(points, max(1, args.bucket_seconds))
    buckets.sort(key=lambda item: abs(item["realized_pnl"]), reverse=True)
    top_buckets = buckets[: max(1, args.top)]
    total_closed = len(points)
    label = f"{alias} ({wallet})" if alias else wallet

    print(label)
    print("=" * len(label))
    print(f"Closed trades: {total_closed}")
    print(f"Bucket size:   {args.bucket_seconds}s")
    print()

    print_table(
        ["Bucket TS", "Trades", "Net Realized", "Wins", "Losses", "Flat", "% Closed"],
        [
            [
                str(bucket["ts"]),
                str(bucket["trade_count"]),
                fmt_money(bucket["realized_pnl"]),
                str(bucket["wins"]),
                str(bucket["losses"]),
                str(bucket["flat"]),
                f"{(bucket['trade_count'] / total_closed) * 100.0:.1f}%",
            ]
            for bucket in top_buckets
        ],
    )


if __name__ == "__main__":
    main()
