#!/usr/bin/env python
"""Deep-dive a wallet to understand whether it is a good copy-trade candidate."""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from pathlib import Path

from _analysis_common import (
    bucket_realized_points,
    fmt_money,
    fmt_num,
    fmt_pct,
    fmt_seconds,
    get_connection,
    print_table,
    resolve_wallet,
)
from dashboard import _build_wallet_buy_outcome_rows, _build_wallet_detail_payload


def summarize_close_reasons(rows: list[dict]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "realized": 0.0, "unrealized": 0.0})
    for row in rows:
        reason = row.get("close_reason") or "open"
        summary[reason]["count"] += 1
        summary[reason]["realized"] += float(row.get("realized_pnl") or 0.0)
        summary[reason]["unrealized"] += float(row.get("unrealized_pnl") or 0.0)
    return dict(summary)


def group_markets(rows: list[dict]) -> list[dict]:
    markets: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row.get("question") or row.get("token_id") or "", row.get("outcome") or "?")
        market = markets.setdefault(
            key,
            {
                "question": key[0],
                "outcome": key[1],
                "entries": 0,
                "filled_entries": 0,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "entry_cost": 0.0,
            },
        )
        market["entries"] += 1
        if row.get("realized_pnl") is not None:
            market["filled_entries"] += 1
            market["realized_pnl"] += float(row.get("realized_pnl") or 0.0)
            market["unrealized_pnl"] += float(row.get("unrealized_pnl") or 0.0)
        market["entry_cost"] += float(row.get("entry_cost") or 0.0)
    return sorted(markets.values(), key=lambda item: item["realized_pnl"] + item["unrealized_pnl"], reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a copy-trade report for one wallet.")
    parser.add_argument("--wallet", required=True, help="Wallet alias or address from the DB.")
    parser.add_argument("--db", type=Path, default=Path("paper_trades.db"), help="SQLite DB path.")
    parser.add_argument("--market-limit", type=int, default=10, help="How many best/worst markets to print.")
    parser.add_argument("--bucket-seconds", type=int, default=900, help="Realized PnL bucket size in seconds.")
    args = parser.parse_args()

    conn = get_connection(args.db)
    try:
        wallet, alias = resolve_wallet(conn, args.wallet)
        payload = _build_wallet_detail_payload(conn, wallet)
        if not payload:
            raise SystemExit(f"Wallet not found: {args.wallet}")
        buy_rows = _build_wallet_buy_outcome_rows(conn, wallet)
    finally:
        conn.close()

    summary = payload["summary"]
    realized_points = payload.get("realized_trade_points") or []
    close_reason_summary = summarize_close_reasons(buy_rows)
    markets = group_markets(buy_rows)
    positive_realized = [float(point.get("realized_pnl") or 0.0) for point in realized_points if float(point.get("realized_pnl") or 0.0) > 0.01]
    negative_realized = [float(point.get("realized_pnl") or 0.0) for point in realized_points if float(point.get("realized_pnl") or 0.0) < -0.01]
    hold_values = [float(row["hold_seconds"]) for row in buy_rows if row.get("hold_seconds") is not None]
    buckets = bucket_realized_points(realized_points, max(1, args.bucket_seconds))
    biggest_buckets = sorted(buckets, key=lambda item: abs(item["realized_pnl"]), reverse=True)[:5]

    label = f"{alias} ({wallet})" if alias else wallet
    print(label)
    print("=" * len(label))
    print()
    print(f"Target trades:      {summary['total_target_trades']}")
    print(f"Filled paper trades:{summary['filled_trades']}")
    print(f"Closed trades:      {len(realized_points)}")
    print(f"Open positions:     {summary['active_positions']}")
    print(f"Paper volume:       {fmt_money(summary['paper_volume'])}")
    print(f"Realized PnL:       {fmt_money(summary['realized_pnl'])}")
    print(f"Unrealized PnL:     {fmt_money(summary['unrealized_pnl'])}")
    print(f"Total PnL:          {fmt_money(summary['total_pnl'])}")
    print(f"Avg slippage:       {fmt_num(summary['avg_slippage'], 4)}")
    print(f"Avg latency:        {summary['avg_latency_ms']:.0f}ms")
    print()

    print("Closed trade distribution")
    print("-------------------------")
    if realized_points:
        realized_values = [float(point.get("realized_pnl") or 0.0) for point in realized_points]
        print(f"Win rate:           {fmt_pct((sum(1 for value in realized_values if value > 0.01) / len(realized_values)) * 100.0)}")
        print(f"Median realized:    {fmt_money(statistics.median(realized_values))}")
        print(f"Avg realized:       {fmt_money(statistics.fmean(realized_values))}")
        print(f"Avg winner:         {fmt_money(statistics.fmean(positive_realized)) if positive_realized else '-'}")
        print(f"Avg loser:          {fmt_money(statistics.fmean(negative_realized)) if negative_realized else '-'}")
    else:
        print("No closed trades yet.")
    print(f"Median hold time:   {fmt_seconds(statistics.median(hold_values)) if hold_values else '-'}")
    print()

    print("PnL attribution by close reason")
    print("-------------------------------")
    attribution_rows = []
    for reason, values in sorted(close_reason_summary.items(), key=lambda item: item[1]["realized"], reverse=True):
        attribution_rows.append([
            reason,
            str(int(values["count"])),
            fmt_money(values["realized"]),
            fmt_money(values["unrealized"]),
        ])
    print_table(["Reason", "Count", "Realized", "Unrealized"], attribution_rows)
    print()

    print("Largest realized PnL buckets")
    print("----------------------------")
    bucket_rows = [
        [str(bucket["ts"]), str(bucket["trade_count"]), fmt_money(bucket["realized_pnl"]), str(bucket["wins"]), str(bucket["losses"])]
        for bucket in biggest_buckets
    ]
    print_table(["Bucket TS", "Trades", "Net Realized", "Wins", "Losses"], bucket_rows)
    print()

    best_markets = markets[: max(1, args.market_limit)]
    worst_markets = list(reversed(markets[-max(1, args.market_limit):]))

    print("Best markets")
    print("------------")
    print_table(
        ["Market", "Outcome", "Entries", "Filled", "Realized", "Unrealized"],
        [
            [
                market["question"][:42],
                market["outcome"],
                str(market["entries"]),
                str(market["filled_entries"]),
                fmt_money(market["realized_pnl"]),
                fmt_money(market["unrealized_pnl"]),
            ]
            for market in best_markets
        ],
    )
    print()

    print("Worst markets")
    print("-------------")
    print_table(
        ["Market", "Outcome", "Entries", "Filled", "Realized", "Unrealized"],
        [
            [
                market["question"][:42],
                market["outcome"],
                str(market["entries"]),
                str(market["filled_entries"]),
                fmt_money(market["realized_pnl"]),
                fmt_money(market["unrealized_pnl"]),
            ]
            for market in worst_markets
        ],
    )


if __name__ == "__main__":
    main()
