"""Live Paper Trading Simulator for Polymarket — DB-backed & restartable."""
import argparse
import asyncio
import time
import urllib.request
import urllib.error
import json
import ssl
from typing import Optional
from functools import partial
from datetime import datetime

from src.monitor import TradeMonitor
from src.core.models import TradeData
from src.resolution_worker import ResolutionWorker
from src.utils.logging import get_logger
from src import db

ssl_context = ssl._create_unverified_context()
log = get_logger(__name__)


# ── HTTP helpers ──────────────────────────────────────────────────

def fetch_json(url: str) -> Optional[dict | list]:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        response = urllib.request.urlopen(req, context=ssl_context)
        return json.loads(response.read())
    except Exception as e:
        log.error(f"Error fetching {url}: {e}")
        return None


def fetch_orderbook(token_id: str) -> Optional[dict]:
    url = f"https://clob.polymarket.com/book?token_id={token_id}"
    return fetch_json(url)


def fetch_market_metadata(token_id: str) -> Optional[dict]:
    url = f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}"
    data = fetch_json(url)
    if not data:
        return None
    for m in data:
        clob_ids = json.loads(m.get("clobTokenIds", "[]"))
        if token_id in clob_ids:
            outcome_idx = clob_ids.index(token_id)
            outcomes = m.get("outcomes", "[]")
            if isinstance(outcomes, str):
                outcomes_list = json.loads(outcomes)
            else:
                outcomes_list = outcomes
            # Tags come as a list of dicts [{"id":...,"label":...}] or plain strings
            raw_tags = m.get("tags", [])
            if isinstance(raw_tags, str):
                try:
                    raw_tags = json.loads(raw_tags)
                except Exception:
                    raw_tags = []
            tag_labels = [
                t.get("label", t) if isinstance(t, dict) else str(t)
                for t in raw_tags
            ]
            primary_category = (m.get("category") or "").strip()
            group_item_title = (m.get("groupItemTitle") or "").strip()

            return {
                "id": m.get("id", ""),
                "question": m.get("question", ""),
                "outcomes": outcomes_list,
                "outcomes_json": json.dumps(outcomes_list),
                "outcome_idx": outcome_idx,
                "condition_id": m.get("conditionId", ""),
                "slug": m.get("slug", ""),
                # Gamma's top-level `category` field has broad labels (Weather, Crypto, ...).
                # `groupItemTitle` is often a sub-group/strike bucket and should only be fallback.
                "category": primary_category or group_item_title,
                "group_item_title": group_item_title,
                "tags": json.dumps(tag_labels),
                "resolved": bool(m.get("resolved")),
                "closed": bool(m.get("closed")),
            }
    return None


def fetch_top_wallets(category: str, time_period: str, order_by: str, limit: int) -> list[dict]:
    """Returns list of dicts with address, alias, pnl, vol."""
    log.info(f"Fetching top {limit} {category} wallets from leaderboard...")
    url = f"https://data-api.polymarket.com/v1/leaderboard?category={category}&timePeriod={time_period}&orderBy={order_by}&limit={limit}"
    data = fetch_json(url)
    results = []
    if data:
        for user in data:
            addr = user.get('proxyWallet') or user.get('address') or user.get('wallet')
            if addr:
                results.append({
                    "address": addr,
                    "alias": user.get("userName", ""),
                    "pnl": user.get("pnl", 0),
                    "vol": user.get("vol", 0),
                })
    return results


# ── Trade handler ─────────────────────────────────────────────────

async def on_transaction(trade: TradeData, args: argparse.Namespace):
    detect_time = time.time()
    onchain_time = datetime.fromisoformat(trade.timestamp).timestamp()

    token_id = trade.token_id
    side_str = "BUY" if trade.side == 0 else "SELL"

    amount_a = trade.maker_amount / 1e6
    amount_b = trade.taker_amount / 1e6
    if max(amount_a, amount_b) == 0:
        return

    target_price = min(amount_a, amount_b) / max(amount_a, amount_b)
    target_size = max(amount_a, amount_b)
    target_cost = target_size * target_price

    # Fetch / cache market metadata & orderbook (Netword IO outside transaction)
    meta = await asyncio.to_thread(fetch_market_metadata, token_id)
    orderbook = await asyncio.to_thread(fetch_orderbook, token_id)

    if not orderbook:
        log.warning("Failed to fetch orderbook")
        return

    # Now do all DB work in a separate thread to avoid blocking the event loop
    def db_work():
        with db.transaction() as conn:
            if meta:
                db.upsert_market(
                    conn, token_id,
                    question=meta["question"],
                    outcomes=meta["outcomes_json"],
                    outcome_idx=meta["outcome_idx"],
                    condition_id=meta.get("condition_id", ""),
                    slug=meta.get("slug", ""),
                    category=meta.get("category", ""),
                    group_item_title=meta.get("group_item_title", ""),
                    tags=meta.get("tags", "[]"),
                )
            else:
                db.upsert_market(conn, token_id, question="Unknown / Pending Metadata")

            title = meta["question"] if meta else "Unknown"
            log.info(
                f"[{side_str}] {trade.wallet[:10]}… | {title[:50]} | "
                f"~{target_size:.1f} shares @ ${target_price:.4f}"
            )

            # 2. Persist target trade
            target_trade_id = db.insert_target_trade(
                conn, wallet=trade.wallet, token_id=token_id,
                tx_hash=trade.transaction_hash, block_number=trade.block_number,
                side=side_str, size=target_size, price=target_price,
                cost_usd=target_cost, onchain_ts=onchain_time, detected_ts=detect_time,
            )

            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])

            # 3. Persist orderbook snapshot
            db.insert_orderbook_snapshot(
                conn, target_trade_id=target_trade_id, token_id=token_id,
                side=side_str, bids=bids, asks=asks,
            )

            # 4. Simulate fill logic (moved inside for atomicity)
            paper_cost_basis = 0.0
            shares_filled = 0.0
            desired_dollars = args.size

            if side_str == "BUY":
                for ask in sorted(asks, key=lambda x: float(x['price'])):
                    p, s = float(ask['price']), float(ask['size'])
                    cost_for_level = p * s
                    if paper_cost_basis + cost_for_level >= desired_dollars:
                        remaining = desired_dollars - paper_cost_basis
                        shares_filled += remaining / p
                        paper_cost_basis += remaining
                        break
                    else:
                        shares_filled += s
                        paper_cost_basis += cost_for_level
            else:
                for bid in sorted(bids, key=lambda x: float(x['price']), reverse=True):
                    p, s = float(bid['price']), float(bid['size'])
                    value_for_level = p * s
                    if paper_cost_basis + value_for_level >= desired_dollars:
                        remaining = desired_dollars - paper_cost_basis
                        shares_filled += remaining / p
                        paper_cost_basis += remaining
                        break
                    else:
                        shares_filled += s
                        paper_cost_basis += value_for_level

            fill_time = time.time()
            detection_delay = (detect_time - onchain_time) * 1000
            execution_delay = (fill_time - detect_time) * 1000
            total_delay = (fill_time - onchain_time) * 1000

            # Build OB summary
            sorted_bids = sorted(bids, key=lambda x: float(x['price']), reverse=True)
            sorted_asks = sorted(asks, key=lambda x: float(x['price']))
            total_bid_liq = sum(float(b['price']) * float(b['size']) for b in bids)
            total_ask_liq = sum(float(a['price']) * float(a['size']) for a in asks)
            
            top_bids_str = ", ".join(f"${float(b['price']):.4f}×{float(b['size']):.1f}" for b in sorted_bids[:5]) or "(empty)"
            top_asks_str = ", ".join(f"${float(a['price']):.4f}×{float(a['size']):.1f}" for a in sorted_asks[:5]) or "(empty)"
            ob_summary = (
                f"Order Book Snapshot ({len(bids)} bids / {len(asks)} asks):\n"
                f"  Top Bids: {top_bids_str}\n"
                f"  Top Asks: {top_asks_str}\n"
                f"  Total Bid Liquidity: ${total_bid_liq:.2f} | Total Ask Liquidity: ${total_ask_liq:.2f}"
            )

            if shares_filled > 0:
                avg_paper_price = paper_cost_basis / shares_filled
                slippage = avg_paper_price - target_price if side_str == "BUY" else target_price - avg_paper_price

                db.insert_paper_trade(
                    conn, target_trade_id=target_trade_id, token_id=token_id,
                    side=side_str, size=shares_filled, avg_price=avg_paper_price,
                    cost_usd=paper_cost_basis, slippage=slippage,
                    orderbook_latency_ms=0,
                    detection_delay_ms=detection_delay,
                    execution_delay_ms=execution_delay,
                    total_delay_ms=total_delay,
                )

                pos = db.get_position(conn, token_id)
                if side_str == "BUY":
                    pos["cost_basis"] += shares_filled * avg_paper_price
                    pos["size"] += shares_filled
                elif side_str == "SELL" and pos["size"] > 0:
                    avg_entry = pos["cost_basis"] / pos["size"]
                    shares_to_close = min(shares_filled, pos["size"])
                    pos["realized_pnl"] += shares_to_close * (avg_paper_price - avg_entry)
                    pos["size"] -= shares_to_close
                    pos["cost_basis"] -= shares_to_close * avg_entry

                    if shares_filled > shares_to_close:
                        log.warning(
                            "Paper SELL filled more shares than held; capping position close",
                            token_id=token_id,
                            shares_filled=round(shares_filled, 6),
                            shares_closed=round(shares_to_close, 6),
                        )
                    if pos["size"] <= 0.0001:
                        pos["size"] = 0
                        pos["cost_basis"] = 0

                db.upsert_position(conn, token_id, pos["size"], pos["cost_basis"], pos["realized_pnl"])

                log.info(
                    f"  Paper fill: {shares_filled:.1f} @ ${avg_paper_price:.4f} | "
                    f"slip ${slippage:+.4f} | latency {total_delay:.0f}ms\n"
                    f"  {ob_summary}"
                )
            else:
                no_fill_reason = (
                    f"Insufficient liquidity: needed ${desired_dollars:.2f}, "
                    f"book had ${total_ask_liq:.2f} ask-side / ${total_bid_liq:.2f} bid-side"
                )
                db.insert_paper_trade(
                    conn, target_trade_id=target_trade_id, token_id=token_id,
                    side=side_str, size=0.0, avg_price=0.0,
                    cost_usd=0.0, slippage=0.0,
                    orderbook_latency_ms=0,
                    detection_delay_ms=detection_delay,
                    execution_delay_ms=execution_delay,
                    total_delay_ms=total_delay,
                    no_fill_reason=no_fill_reason,
                )
                log.warning(f"Not enough orderbook liquidity to fill paper trade\n  {ob_summary}")

    await asyncio.to_thread(db_work)


def check_missing_metadata():
    """Polls the DB for markets with placeholder metadata and retries fetching them."""
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT token_id
            FROM markets
            WHERE question = 'Unknown / Pending Metadata'
               OR category GLOB '*[0-9$]*'
               OR category LIKE '%,%'
            """
        ).fetchall()
        
        if not rows:
            return

        log.info(f"Retrying metadata fetch for {len(rows)} markets...")
        for row in rows:
            tid = row["token_id"]
            meta = fetch_market_metadata(tid)
            if meta:
                db.upsert_market(
                    conn, tid,
                    question=meta["question"],
                    outcomes=meta["outcomes_json"],
                    outcome_idx=meta["outcome_idx"],
                    condition_id=meta.get("condition_id", ""),
                    slug=meta.get("slug", ""),
                    category=meta.get("category", ""),
                    group_item_title=meta.get("group_item_title", ""),
                    tags=meta.get("tags", "[]"),
                )
                log.info(f"  Successfully backfilled metadata for {tid[:10]}…: {meta['question'][:50]}")
            # Throttle API calls
            time.sleep(0.5)

# ── Main ──────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Live Paper Trading Simulator for Polymarket")
    parser.add_argument("--wallets", type=str, default="",
                        help="Comma-separated list of target wallet addresses (overrides leaderboard)")
    parser.add_argument("--size", type=float, default=100.0,
                        help="Simulated paper trade size in USD ($)")
    parser.add_argument("--category", type=str, default=None,
                        help="Comma-separated leaderboard categories (e.g. WEATHER,CRYPTO). If omitted, fetches from all major categories.")
    parser.add_argument("--time-period", type=str, default="MONTH",
                        help="Leaderboard time period (DAY, WEEK, MONTH, ALL)")
    parser.add_argument("--order-by", type=str, default="PNL",
                        help="Leaderboard sort order (PNL, VOL)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Number of top wallets to fetch from leaderboard")
    parser.add_argument("--db", type=str, default=None,
                        help="Path to SQLite database file (default: paper_trades.db)")
    parser.add_argument(
        "--run-resolution-inline",
        action="store_true",
        help="Run resolution polling/WS inside this process (default: disabled; use resolution_worker.py).",
    )

    args = parser.parse_args()

    # Initialize DB
    db.init_db(args.db)

    # Determine target wallets
    target_wallets = []
    with db.transaction(db_path=args.db) as conn:
        if args.wallets:
            for w in args.wallets.split(","):
                w = w.strip()
                if w:
                    db.upsert_wallet(conn, w, source="manual")
                    target_wallets.append(w)
            log.info(f"Loaded {len(target_wallets)} wallets from CLI.")
        else:
            if args.category:
                cats_to_fetch = [c.strip().upper() for c in args.category.split(",") if c.strip()]
            else:
                # Optimized categories based on current Polymarket Data API support
                cats_to_fetch = ["politics", "sports", "crypto", "finance", "culture", "mentions", "weather", "economics", "tech", "overall"]

            seen_addresses = set()
            for cat in cats_to_fetch:
                # Ensure category is lowercase for API compatibility
                wallet_data = fetch_top_wallets(cat.lower(), args.time_period.upper(), args.order_by.upper(), args.limit)
                for wd in wallet_data:
                    addr = wd["address"]
                    if addr not in seen_addresses:
                        db.upsert_wallet(conn, addr, alias=wd["alias"],
                                         source=f"leaderboard:{cat}", pnl=wd["pnl"], vol=wd["vol"])
                        target_wallets.append(addr)
                        seen_addresses.add(addr)
            log.info(f"Loaded {len(target_wallets)} unique wallets from {len(cats_to_fetch)} leaderboards.")

        if not target_wallets:
            log.error("No target wallets to monitor!")
            return

        db.set_state(conn, "last_start", str(time.time()))
        db.set_state(conn, "paper_size", str(args.size))

    monitor = TradeMonitor()
    monitor.on("transaction", partial(on_transaction, args=args))

    try:
        await monitor.start(target_wallets)

        if args.run_resolution_inline:
            resolution_worker = ResolutionWorker(db_path=args.db, poll_interval_seconds=1800)
            asyncio.create_task(resolution_worker.run())
            log.info("Resolution worker running inline with live simulator")
        else:
            log.warning(
                "Inline resolution worker disabled. Start it separately: python resolution_worker.py --db <path>"
            )

        async def metadata_backfill_loop():
            while True:
                # Check for missing metadata every 10 minutes
                await asyncio.to_thread(check_missing_metadata)
                await asyncio.sleep(600)

        asyncio.create_task(metadata_backfill_loop())

        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await monitor.stop()
        log.info("Monitor stopped gracefully.")


if __name__ == "__main__":
    asyncio.run(main())
