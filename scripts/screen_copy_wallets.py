#!/usr/bin/env python
"""Rank wallets for copy-trading based on paper-trade performance and quality filters."""

from __future__ import annotations

import argparse
import statistics
from pathlib import Path

from _analysis_common import fmt_money, fmt_num, fmt_pct, get_connection, print_table
from dashboard import _build_wallet_detail_payload


def max_drawdown(points: list[dict]) -> float:
    peak = None
    worst = 0.0
    for point in points:
        total = float(point.get("total_pnl") or 0.0)
        peak = total if peak is None else max(peak, total)
        worst = min(worst, total - peak)
    return worst


def build_candidate_row(conn, wallet: str) -> dict | None:
    payload = _build_wallet_detail_payload(conn, wallet)
    if not payload:
        return None

    wallet_data = payload["wallet"]
    summary = payload["summary"]
    realized_points = payload.get("realized_trade_points") or []
    timeline = payload.get("pnl_timeline") or []

    closed_count = len(realized_points)
    if not closed_count:
        median_realized = 0.0
        avg_realized = 0.0
        win_rate = 0.0
    else:
        realized_values = [float(point.get("realized_pnl") or 0.0) for point in realized_points]
        median_realized = statistics.median(realized_values)
        avg_realized = statistics.fmean(realized_values)
        win_rate = (sum(1 for value in realized_values if value > 0.01) / closed_count) * 100.0

    max_batch_ratio = 0.0
    if realized_points:
        batch_counts: dict[float, int] = {}
        for point in realized_points:
            ts = round(float(point.get("ts") or 0.0), 3)
            batch_counts[ts] = batch_counts.get(ts, 0) + 1
        max_batch_ratio = (max(batch_counts.values()) / closed_count) * 100.0 if batch_counts else 0.0

    filled_trades = int(summary.get("filled_trades") or 0)
    total_trades = int(summary.get("total_target_trades") or 0)
    no_fill_trades = int(summary.get("no_fill_trades") or 0)
    paper_volume = float(summary.get("paper_volume") or 0.0)
    realized_pnl = float(summary.get("realized_pnl") or 0.0)
    unrealized_pnl = float(summary.get("unrealized_pnl") or 0.0)
    total_pnl = float(summary.get("total_pnl") or 0.0)
    avg_slippage = float(summary.get("avg_slippage") or 0.0)
    avg_latency_ms = float(summary.get("avg_latency_ms") or 0.0)
    no_fill_rate = ((no_fill_trades / total_trades) * 100.0) if total_trades else 0.0
    pnl_per_filled_trade = (realized_pnl / filled_trades) if filled_trades else 0.0
    roi_pct = ((total_pnl / paper_volume) * 100.0) if paper_volume > 0.0001 else 0.0
    drawdown = max_drawdown(timeline)

    # Heuristic ranking: favor realized profits, consistency, and execution quality.
    score = (
        realized_pnl
        + (0.35 * total_pnl)
        + (8.0 * pnl_per_filled_trade)
        + (2.0 * win_rate)
        + (12.0 * median_realized)
        - (450.0 * avg_slippage)
        - (0.015 * avg_latency_ms)
        - (4.0 * no_fill_rate)
        - (0.1 * abs(drawdown))
        - (0.5 * max_batch_ratio)
    )

    return {
        "wallet": wallet,
        "alias": wallet_data.get("alias") or "",
        "score": round(score, 2),
        "realized_pnl": round(realized_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "roi_pct": round(roi_pct, 2),
        "filled_trades": filled_trades,
        "closed_trades": closed_count,
        "win_rate": round(win_rate, 1),
        "avg_realized": round(avg_realized, 2),
        "median_realized": round(median_realized, 2),
        "pnl_per_filled_trade": round(pnl_per_filled_trade, 2),
        "paper_volume": round(paper_volume, 2),
        "avg_slippage": round(avg_slippage, 4),
        "avg_latency_ms": round(avg_latency_ms, 1),
        "no_fill_rate": round(no_fill_rate, 1),
        "active_positions": int(summary.get("active_positions") or 0),
        "max_drawdown": round(drawdown, 2),
        "max_batch_ratio": round(max_batch_ratio, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank local wallets for copy-trading.")
    parser.add_argument("--db", type=Path, default=Path("paper_trades.db"), help="SQLite DB path.")
    parser.add_argument("--top", type=int, default=20, help="How many wallets to print.")
    parser.add_argument("--min-filled", type=int, default=50, help="Minimum filled paper trades.")
    parser.add_argument("--min-closed", type=int, default=20, help="Minimum realized closed trades.")
    parser.add_argument("--sort-by", default="score", choices=[
        "score", "realized_pnl", "total_pnl", "roi_pct", "win_rate", "pnl_per_filled_trade"
    ], help="Primary sort column.")
    args = parser.parse_args()

    conn = get_connection(args.db)
    try:
        wallets = [
            row["wallet"]
            for row in conn.execute(
                "SELECT wallet FROM target_trades GROUP BY wallet ORDER BY COUNT(*) DESC"
            ).fetchall()
        ]

        candidates = []
        for wallet in wallets:
            row = build_candidate_row(conn, wallet)
            if not row:
                continue
            if row["filled_trades"] < args.min_filled or row["closed_trades"] < args.min_closed:
                continue
            candidates.append(row)
    finally:
        conn.close()

    reverse = True
    candidates.sort(key=lambda item: item[args.sort_by], reverse=reverse)
    top_rows = candidates[: max(1, args.top)]

    headers = [
        "Rank", "Alias", "Wallet", "Score", "Realized", "Total", "ROI", "Filled",
        "Closed", "Win%", "PnL/Fill", "Slip", "Latency", "NoFill%", "Batch%"
    ]
    rows = []
    for idx, row in enumerate(top_rows, start=1):
        alias = row["alias"] or row["wallet"][:10]
        rows.append([
            str(idx),
            alias[:18],
            row["wallet"][:10] + "...",
            fmt_num(row["score"], 1),
            fmt_money(row["realized_pnl"]),
            fmt_money(row["total_pnl"]),
            fmt_pct(row["roi_pct"]),
            str(row["filled_trades"]),
            str(row["closed_trades"]),
            fmt_pct(row["win_rate"]),
            fmt_money(row["pnl_per_filled_trade"]),
            fmt_num(row["avg_slippage"], 4),
            f"{row['avg_latency_ms']:.0f}ms",
            fmt_pct(row["no_fill_rate"]),
            fmt_pct(row["max_batch_ratio"]),
        ])

    print(
        f"Ranked {len(candidates)} candidate wallets from {len(wallets)} observed wallets "
        f"(min_filled={args.min_filled}, min_closed={args.min_closed}, sort_by={args.sort_by})"
    )
    print()
    print_table(headers, rows)


if __name__ == "__main__":
    main()
