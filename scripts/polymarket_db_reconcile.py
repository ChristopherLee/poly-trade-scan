#!/usr/bin/env python
"""Compare Polymarket wallet activity with the local paper trading database."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ACTIVITY_URL = "https://data-api.polymarket.com/activity"
GAMMA_URL = "https://gamma-api.polymarket.com/markets"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
}


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


def fetch_json(url: str) -> list | dict:
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_activity(wallet: str, page_size: int, max_offset: int, max_pages: int) -> list[dict]:
    rows: list[dict] = []
    pages = 0
    for offset in range(0, max_offset + 1, page_size):
        if max_pages and pages >= max_pages:
            break
        query = urllib.parse.urlencode({"user": wallet, "limit": page_size, "offset": offset})
        batch = fetch_json(f"{ACTIVITY_URL}?{query}")
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected Polymarket activity response at offset {offset}: {batch!r}")
        rows.extend(batch)
        pages += 1
        if len(batch) < page_size:
            break
        time.sleep(0.15)
    return rows


def fetch_market(token_id: str) -> dict | None:
    query = urllib.parse.urlencode({"clob_token_ids": token_id})
    data = fetch_json(f"{GAMMA_URL}?{query}")
    if not isinstance(data, list):
        return None
    for market in data:
        clob_ids = market.get("clobTokenIds") or market.get("clob_token_ids")
        if isinstance(clob_ids, str):
            try:
                parsed_ids = json.loads(clob_ids)
            except json.JSONDecodeError:
                parsed_ids = [clob_ids]
        else:
            parsed_ids = clob_ids or []
        if token_id in parsed_ids:
            return market
    return None


def load_target_rows(conn: sqlite3.Connection, wallet: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT tt.id, tt.tx_hash, tt.token_id, tt.side, tt.size, tt.price, tt.cost_usd,
               tt.onchain_ts, m.slug, m.question, m.outcome_idx
        FROM target_trades tt
        LEFT JOIN markets m ON m.token_id = tt.token_id
        WHERE tt.wallet = ?
        ORDER BY tt.onchain_ts ASC, tt.id ASC
        """,
        (wallet,),
    ).fetchall()


def load_copy_summary(conn: sqlite3.Connection, wallet: str) -> dict:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS target_trade_count,
            SUM(CASE WHEN pt.id IS NOT NULL THEN 1 ELSE 0 END) AS linked_paper_rows,
            SUM(CASE WHEN pt.id IS NOT NULL AND pt.no_fill_reason IS NULL THEN 1 ELSE 0 END) AS filled_paper_rows,
            COALESCE(SUM(CASE WHEN pt.no_fill_reason IS NULL THEN pt.cost_usd ELSE 0 END), 0) AS copied_usd,
            COALESCE(SUM(CASE WHEN pt.no_fill_reason IS NOT NULL THEN 1 ELSE 0 END), 0) AS no_fill_rows
        FROM target_trades tt
        LEFT JOIN paper_trades pt ON pt.target_trade_id = tt.id
        WHERE tt.wallet = ?
        """,
        (wallet,),
    ).fetchone()
    pnl = conn.execute(
        """
        SELECT
            COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
            COALESCE(SUM(cost_basis), 0) AS open_cost_basis,
            COALESCE(SUM(size), 0) AS open_size
        FROM wallet_positions
        WHERE wallet = ?
        """,
        (wallet,),
    ).fetchone()
    return {
        "target_trade_count": int(row["target_trade_count"] or 0),
        "linked_paper_rows": int(row["linked_paper_rows"] or 0),
        "filled_paper_rows": int(row["filled_paper_rows"] or 0),
        "copied_usd": float(row["copied_usd"] or 0.0),
        "no_fill_rows": int(row["no_fill_rows"] or 0),
        "realized_pnl": float(pnl["realized_pnl"] or 0.0),
        "open_cost_basis": float(pnl["open_cost_basis"] or 0.0),
        "open_size": float(pnl["open_size"] or 0.0),
    }


def load_top_market_losses(conn: sqlite3.Connection, wallet: str, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        WITH condition_pnl AS (
            SELECT m.condition_id, m.slug, m.question,
                   SUM(wp.realized_pnl) AS realized_pnl,
                   SUM(CASE WHEN wp.size > 0 THEN wp.cost_basis ELSE 0 END) AS open_cost_basis,
                   GROUP_CONCAT(CAST(m.outcome_idx AS TEXT) || ':' || ROUND(wp.realized_pnl, 2), ' | ') AS outcome_pnls
            FROM wallet_positions wp
            JOIN markets m ON m.token_id = wp.token_id
            WHERE wp.wallet = ?
            GROUP BY m.condition_id, m.slug, m.question
        ),
        condition_copy AS (
            SELECT m.condition_id,
                   SUM(CASE WHEN pt.no_fill_reason IS NULL THEN pt.cost_usd ELSE 0 END) AS copied_usd
            FROM target_trades tt
            JOIN markets m ON m.token_id = tt.token_id
            LEFT JOIN paper_trades pt ON pt.target_trade_id = tt.id
            WHERE tt.wallet = ?
            GROUP BY m.condition_id
        )
        SELECT cp.slug, cp.question, cp.realized_pnl, cp.open_cost_basis, cc.copied_usd, cp.outcome_pnls
        FROM condition_pnl cp
        LEFT JOIN condition_copy cc ON cc.condition_id = cp.condition_id
        WHERE ABS(cp.realized_pnl) > 0.0001 OR ABS(cp.open_cost_basis) > 0.0001
        ORDER BY cp.realized_pnl ASC, cp.open_cost_basis DESC
        LIMIT ?
        """,
        (wallet, wallet, limit),
    ).fetchall()


def build_api_index(activity_rows: list[dict]) -> tuple[dict[tuple[str, str, str], list[dict]], dict[tuple[str, str, str], int]]:
    index: dict[tuple[str, str, str], list[dict]] = {}
    counts: dict[tuple[str, str, str], int] = {}
    for row in activity_rows:
        tx_hash = (row.get("transactionHash") or "").lower()
        asset = str(row.get("asset") or "")
        side = str(row.get("side") or "")
        key = (tx_hash, asset, side)
        index.setdefault(key, []).append(row)
        counts[key] = counts.get(key, 0) + 1
    return index, counts


def choose_candidate(candidates: list[dict], db_row: sqlite3.Row, used: set[int]) -> tuple[int, dict] | tuple[None, None]:
    best_idx = None
    best_score = None
    for idx, candidate in enumerate(candidates):
        if idx in used:
            continue
        price_diff = abs(float(db_row["price"] or 0.0) - float(candidate.get("price") or 0.0))
        size_diff = abs(float(db_row["size"] or 0.0) - float(candidate.get("size") or 0.0))
        score = (price_diff, size_diff)
        if best_score is None or score < best_score:
            best_score = score
            best_idx = idx
    if best_idx is None:
        return None, None
    return best_idx, candidates[best_idx]


def compare_rows(target_rows: list[sqlite3.Row], activity_rows: list[dict]) -> dict:
    api_index, api_counts = build_api_index(activity_rows)
    used_indices: dict[tuple[str, str, str], set[int]] = {}
    matched = []
    missing_in_api = []

    for row in target_rows:
        key = ((row["tx_hash"] or "").lower(), str(row["token_id"] or ""), str(row["side"] or ""))
        candidates = api_index.get(key)
        if not candidates:
            missing_in_api.append(
                {
                    "tx_hash": row["tx_hash"],
                    "token_id": row["token_id"],
                    "side": row["side"],
                    "size": float(row["size"] or 0.0),
                    "price": float(row["price"] or 0.0),
                    "onchain_ts": float(row["onchain_ts"] or 0.0),
                    "slug": row["slug"],
                }
            )
            continue
        used = used_indices.setdefault(key, set())
        chosen_idx, candidate = choose_candidate(candidates, row, used)
        if candidate is None:
            missing_in_api.append(
                {
                    "tx_hash": row["tx_hash"],
                    "token_id": row["token_id"],
                    "side": row["side"],
                    "size": float(row["size"] or 0.0),
                    "price": float(row["price"] or 0.0),
                    "onchain_ts": float(row["onchain_ts"] or 0.0),
                    "slug": row["slug"],
                }
            )
            continue
        used.add(chosen_idx)
        api_size = float(candidate.get("size") or 0.0)
        db_size = float(row["size"] or 0.0)
        matched.append(
            {
                "tx_hash": row["tx_hash"],
                "slug": row["slug"],
                "token_id": row["token_id"],
                "side": row["side"],
                "db_price": float(row["price"] or 0.0),
                "api_price": float(candidate.get("price") or 0.0),
                "price_diff": float(row["price"] or 0.0) - float(candidate.get("price") or 0.0),
                "db_size": db_size,
                "api_size": api_size,
                "size_ratio": (db_size / api_size) if api_size else math.inf,
                "db_ts": float(row["onchain_ts"] or 0.0),
                "api_ts": float(candidate.get("timestamp") or 0.0),
                "ts_diff": float(row["onchain_ts"] or 0.0) - float(candidate.get("timestamp") or 0.0),
                "outcome_idx_db": row["outcome_idx"],
                "outcome_idx_api": candidate.get("outcomeIndex"),
            }
        )

    matched_keys = {(item["tx_hash"].lower(), item["token_id"], item["side"]) for item in matched}
    extra_api_rows = []
    for key, count in api_counts.items():
        matched_count = sum(1 for item in matched if (item["tx_hash"].lower(), item["token_id"], item["side"]) == key)
        if matched_count >= count:
            continue
        candidates = api_index[key]
        used = used_indices.get(key, set())
        for idx, candidate in enumerate(candidates):
            if idx not in used:
                extra_api_rows.append(candidate)

    size_ratios = [item["size_ratio"] for item in matched if math.isfinite(item["size_ratio"])]
    return {
        "matched": matched,
        "missing_in_api": missing_in_api,
        "extra_api_rows": extra_api_rows,
        "size_ratio_median": statistics.median(size_ratios) if size_ratios else None,
        "size_ratio_mean": statistics.mean(size_ratios) if size_ratios else None,
        "size_ratio_gt_1_5": sum(1 for ratio in size_ratios if ratio > 1.5),
        "size_ratio_gt_3": sum(1 for ratio in size_ratios if ratio > 3.0),
    }


def verify_market_rows(conn: sqlite3.Connection, wallet: str, limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT wp.token_id, m.slug, m.question, m.outcome_idx, m.resolved, m.payout_value, wp.realized_pnl
        FROM wallet_positions wp
        JOIN markets m ON m.token_id = wp.token_id
        WHERE wp.wallet = ? AND ABS(wp.realized_pnl) > 0.0001
        ORDER BY wp.realized_pnl ASC
        LIMIT ?
        """,
        (wallet, limit),
    ).fetchall()

    verified = []
    for row in rows:
        market = fetch_market(row["token_id"])
        if not market:
            verified.append(
                {
                    "token_id": row["token_id"],
                    "slug": row["slug"],
                    "question": row["question"],
                    "db_resolved": int(row["resolved"] or 0),
                    "db_payout_value": row["payout_value"],
                    "api_resolution": None,
                    "api_outcome_prices": None,
                }
            )
            continue
        verified.append(
            {
                "token_id": row["token_id"],
                "slug": row["slug"],
                "question": row["question"],
                "db_resolved": int(row["resolved"] or 0),
                "db_payout_value": row["payout_value"],
                "api_resolution": market.get("umaResolutionStatus"),
                "api_outcome_prices": market.get("outcomePrices"),
            }
        )
        time.sleep(0.1)
    return verified


def top_size_discrepancies(matched: list[dict], limit: int) -> list[dict]:
    ordered = sorted(
        matched,
        key=lambda item: (
            0 if math.isfinite(item["size_ratio"]) else 1,
            abs(item["size_ratio"] if math.isfinite(item["size_ratio"]) else 0),
            abs(item["db_size"] - item["api_size"]),
        ),
        reverse=True,
    )
    return ordered[:limit]


def render_markdown(
    wallet: str,
    alias: str,
    db_path: Path,
    activity_rows: list[dict],
    comparison: dict,
    copy_summary: dict,
    losses: list[sqlite3.Row],
    market_checks: list[dict],
) -> str:
    lines = []
    label = f"{alias} ({wallet})" if alias else wallet
    lines.append(f"# Polymarket Reconciliation Report")
    lines.append("")
    lines.append(f"- Wallet: `{label}`")
    lines.append(f"- DB: `{db_path}`")
    lines.append(f"- Activity rows fetched: `{len(activity_rows)}`")
    lines.append(f"- DB target trades: `{copy_summary['target_trade_count']}`")
    lines.append("")

    lines.append("## Target vs API")
    lines.append("")
    lines.append(f"- Matched rows: `{len(comparison['matched'])}`")
    lines.append(f"- Missing in API: `{len(comparison['missing_in_api'])}`")
    lines.append(f"- Extra API rows: `{len(comparison['extra_api_rows'])}`")
    lines.append(f"- Size ratio median (DB/API): `{comparison['size_ratio_median']}`")
    lines.append(f"- Size ratio mean (DB/API): `{comparison['size_ratio_mean']}`")
    lines.append(f"- Size ratio > 1.5x: `{comparison['size_ratio_gt_1_5']}`")
    lines.append(f"- Size ratio > 3x: `{comparison['size_ratio_gt_3']}`")
    lines.append("")

    lines.append("## Copy Summary")
    lines.append("")
    lines.append(f"- Filled paper trades: `{copy_summary['filled_paper_rows']}`")
    lines.append(f"- Linked paper rows: `{copy_summary['linked_paper_rows']}`")
    lines.append(f"- Copied USD: `{copy_summary['copied_usd']:.6f}`")
    lines.append(f"- No-fill rows: `{copy_summary['no_fill_rows']}`")
    lines.append(f"- Realized PnL: `{copy_summary['realized_pnl']:.6f}`")
    lines.append(f"- Open cost basis: `{copy_summary['open_cost_basis']:.6f}`")
    lines.append(f"- Open size: `{copy_summary['open_size']:.6f}`")
    lines.append("")

    lines.append("## Top Size Discrepancies")
    lines.append("")
    for item in top_size_discrepancies(comparison["matched"], 10):
        ratio = "inf" if not math.isfinite(item["size_ratio"]) else f"{item['size_ratio']:.6f}"
        lines.append(
            f"- `{item['tx_hash']}` {item['slug']} {item['side']}: "
            f"DB `{item['db_size']:.6f}` vs API `{item['api_size']:.6f}` shares, "
            f"ratio `{ratio}`, price diff `{item['price_diff']:.6f}`"
        )
    if not comparison["matched"]:
        lines.append("- No matched rows.")
    lines.append("")

    lines.append("## Top Market Losses")
    lines.append("")
    for row in losses:
        lines.append(
            f"- `{row['slug']}`: realized `{float(row['realized_pnl'] or 0.0):.6f}`, "
            f"open cost `{float(row['open_cost_basis'] or 0.0):.6f}`, copied `{float(row['copied_usd'] or 0.0):.6f}`, "
            f"outcomes `{row['outcome_pnls']}`"
        )
    if not losses:
        lines.append("- No loss rows.")
    lines.append("")

    lines.append("## Market Verification")
    lines.append("")
    for row in market_checks:
        lines.append(
            f"- `{row['slug']}` token `{row['token_id']}`: DB resolved `{row['db_resolved']}`, "
            f"DB payout `{row['db_payout_value']}`, API resolution `{row['api_resolution']}`, "
            f"API outcome prices `{row['api_outcome_prices']}`"
        )
    if not market_checks:
        lines.append("- No resolved market checks.")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile Polymarket wallet activity with the local paper trading DB.")
    parser.add_argument("--wallet", required=True, help="Wallet alias or address from the DB.")
    parser.add_argument("--db", type=Path, default=Path("paper_trades.db"), help="SQLite DB path.")
    parser.add_argument("--page-size", type=int, default=1000, help="Activity API page size.")
    parser.add_argument("--max-offset", type=int, default=3000, help="Largest activity offset to request.")
    parser.add_argument("--max-pages", type=int, default=0, help="Optional page cap.")
    parser.add_argument("--market-check-limit", type=int, default=8, help="How many resolved losing tokens to verify against Gamma.")
    parser.add_argument("--loss-limit", type=int, default=10, help="How many top losing conditions to print.")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional JSON output path.")
    parser.add_argument("--activity-cache", type=Path, default=None, help="Read activity rows from JSON instead of hitting the API.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    conn = get_connection(args.db)
    try:
        wallet, alias = resolve_wallet(conn, args.wallet)
        if args.activity_cache:
            activity_rows = json.loads(args.activity_cache.read_text(encoding="utf-8"))
        else:
            activity_rows = fetch_activity(
                wallet=wallet,
                page_size=min(1000, max(1, args.page_size)),
                max_offset=max(0, args.max_offset),
                max_pages=max(0, args.max_pages),
            )
        target_rows = load_target_rows(conn, wallet)
        comparison = compare_rows(target_rows, activity_rows)
        copy_summary = load_copy_summary(conn, wallet)
        losses = load_top_market_losses(conn, wallet, max(1, args.loss_limit))
        market_checks = verify_market_rows(conn, wallet, max(0, args.market_check_limit))
    except urllib.error.HTTPError as exc:
        print(f"HTTP error {exc.code}: {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    if args.output_json:
        payload = {
            "wallet": wallet,
            "alias": alias,
            "db": str(args.db),
            "activity_row_count": len(activity_rows),
            "comparison": comparison,
            "copy_summary": copy_summary,
            "losses": [dict(row) for row in losses],
            "market_checks": market_checks,
        }
        args.output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(render_markdown(wallet, alias, args.db, activity_rows, comparison, copy_summary, losses, market_checks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
