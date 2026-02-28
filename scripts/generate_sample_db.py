"""Generate a small dashboard-ready sample database for local UI testing."""

import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db

SAMPLE_DB_PATH = "assets/sample_paper_trades.db"


def main() -> None:
    db.init_db(SAMPLE_DB_PATH)
    now = time.time()

    wallet = "0x1111111111111111111111111111111111111111"
    token_id = "12345678901234567890123456789012345678901234567890123456789012345"

    with db.transaction(db_path=SAMPLE_DB_PATH) as conn:
        db.upsert_wallet(
            conn,
            wallet,
            alias="sample-whale",
            source="leaderboard:crypto",
            pnl=12345.67,
            vol=999999.0,
        )

        db.upsert_market(
            conn,
            token_id,
            question="Will BTC be above $120k by Dec 31, 2026?",
            outcomes='["Yes", "No"]',
            outcome_idx=0,
            condition_id="0xsamplecondition",
            slug="will-btc-be-above-120k-by-dec-31-2026",
            category="Crypto",
            group_item_title="BTC Price Targets",
            tags='["Crypto", "Bitcoin"]',
        )

        target_id = db.insert_target_trade(
            conn,
            wallet=wallet,
            token_id=token_id,
            tx_hash="0xsampletxhash",
            block_number=12345678,
            side="BUY",
            size=75.0,
            price=0.62,
            cost_usd=46.5,
            onchain_ts=now - 15,
            detected_ts=now - 14,
        )

        db.insert_orderbook_snapshot(
            conn,
            target_trade_id=target_id,
            token_id=token_id,
            side="BUY",
            bids=[{"price": 0.61, "size": 300}, {"price": 0.60, "size": 500}],
            asks=[{"price": 0.62, "size": 100}, {"price": 0.63, "size": 200}],
        )

        db.insert_paper_trade(
            conn,
            target_trade_id=target_id,
            token_id=token_id,
            side="BUY",
            size=40.3226,
            avg_price=0.62,
            cost_usd=25.0,
            slippage=0.0,
            orderbook_latency_ms=105,
            detection_delay_ms=250,
            execution_delay_ms=120,
            total_delay_ms=370,
        )

        db.upsert_position(conn, token_id=token_id, size=40.3226, cost_basis=25.0, realized_pnl=0.0)
        db.set_state(conn, "sample_generated_at", str(now))

    print(f"Sample DB written to {SAMPLE_DB_PATH}")


if __name__ == "__main__":
    main()
