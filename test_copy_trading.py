import os
import tempfile
import unittest

from live_paper_trade import normalize_target_trade
from src import db
from src.core.models import TradeData
from src.resolution_worker import ResolutionWorker


class TestCopyTradingLogic(unittest.TestCase):
    def test_normalize_target_trade_buy(self):
        trade = TradeData(
            block_number=1,
            timestamp="2026-01-01T00:00:00+00:00",
            transaction_hash="0xbuy",
            wallet="0xabc",
            token_id="token-1",
            side=0,
            maker_amount=25_000_000,
            taker_amount=40_000_000,
        )

        side, size, price, cost = normalize_target_trade(trade)
        self.assertEqual(side, "BUY")
        self.assertAlmostEqual(size, 40.0)
        self.assertAlmostEqual(cost, 25.0)
        self.assertAlmostEqual(price, 0.625)

    def test_normalize_target_trade_sell(self):
        trade = TradeData(
            block_number=1,
            timestamp="2026-01-01T00:00:00+00:00",
            transaction_hash="0xsell",
            wallet="0xabc",
            token_id="token-1",
            side=1,
            maker_amount=40_000_000,
            taker_amount=25_000_000,
        )

        side, size, price, cost = normalize_target_trade(trade)
        self.assertEqual(side, "SELL")
        self.assertAlmostEqual(size, 40.0)
        self.assertAlmostEqual(cost, 25.0)
        self.assertAlmostEqual(price, 0.625)

    def test_target_wallet_open_size_before_trade(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db.init_db(db_path)
            with db.transaction(db_path=db_path) as conn:
                db.upsert_wallet(conn, "0xwallet")
                db.upsert_market(conn, "token-1", question="Test")
                buy_1 = db.insert_target_trade(conn, "0xwallet", "token-1", "0x1", 1, "BUY", 100.0, 0.5, 50.0, 1.0, 1.1)
                buy_2 = db.insert_target_trade(conn, "0xwallet", "token-1", "0x2", 2, "BUY", 50.0, 0.6, 30.0, 2.0, 2.1)
                sell_1 = db.insert_target_trade(conn, "0xwallet", "token-1", "0x3", 3, "SELL", 75.0, 0.7, 52.5, 3.0, 3.1)

                self.assertAlmostEqual(db.get_target_wallet_open_size_before_trade(conn, "0xwallet", "token-1", buy_1), 0.0)
                self.assertAlmostEqual(db.get_target_wallet_open_size_before_trade(conn, "0xwallet", "token-1", buy_2), 100.0)
                self.assertAlmostEqual(db.get_target_wallet_open_size_before_trade(conn, "0xwallet", "token-1", sell_1), 150.0)
        finally:
            os.remove(db_path)

    def test_wallet_position_isolation_and_aggregate_recompute(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db.init_db(db_path)
            with db.transaction(db_path=db_path) as conn:
                db.upsert_wallet(conn, "0xwalletA")
                db.upsert_wallet(conn, "0xwalletB")
                db.upsert_market(conn, "token-1", question="Test")

                db.upsert_wallet_position(conn, "0xwalletA", "token-1", 10.0, 5.0, 0.0)
                db.upsert_wallet_position(conn, "0xwalletB", "token-1", 20.0, 12.0, 0.0)
                db.recompute_aggregate_position(conn, "token-1")

                wallet_a = db.get_wallet_position(conn, "0xwalletA", "token-1")
                avg_entry = wallet_a["cost_basis"] / wallet_a["size"]
                shares_to_close = 5.0
                wallet_a["size"] -= shares_to_close
                wallet_a["cost_basis"] -= shares_to_close * avg_entry
                wallet_a["realized_pnl"] += shares_to_close * (0.8 - avg_entry)
                db.upsert_wallet_position(
                    conn,
                    "0xwalletA",
                    "token-1",
                    wallet_a["size"],
                    wallet_a["cost_basis"],
                    wallet_a["realized_pnl"],
                )
                db.recompute_aggregate_position(conn, "token-1")

                refreshed_a = db.get_wallet_position(conn, "0xwalletA", "token-1")
                refreshed_b = db.get_wallet_position(conn, "0xwalletB", "token-1")
                aggregate = db.get_position(conn, "token-1")

                self.assertAlmostEqual(refreshed_a["size"], 5.0)
                self.assertAlmostEqual(refreshed_b["size"], 20.0)
                self.assertAlmostEqual(aggregate["size"], 25.0)
                self.assertAlmostEqual(aggregate["cost_basis"], refreshed_a["cost_basis"] + refreshed_b["cost_basis"])
        finally:
            os.remove(db_path)

    def test_resolution_settles_wallet_positions_and_aggregate(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db.init_db(db_path)
            with db.transaction(db_path=db_path) as conn:
                db.upsert_wallet(conn, "0xwalletA")
                db.upsert_market(conn, "token-1", question="Resolved", condition_id="cond-1", outcome_idx=0)
                db.upsert_wallet_position(conn, "0xwalletA", "token-1", 10.0, 4.0, 1.0)
                db.recompute_aggregate_position(conn, "token-1")

                ResolutionWorker(db_path=db_path).process_resolution(
                    conn,
                    {
                        "condition_id": "cond-1",
                        "clob_token_ids": ["token-1"],
                        "resolver_raw_payouts": [1.0],
                        "outcomes": '["Yes"]',
                    },
                )

                wallet_pos = db.get_wallet_position(conn, "0xwalletA", "token-1")
                aggregate = db.get_position(conn, "token-1")
                self.assertAlmostEqual(wallet_pos["size"], 0.0)
                self.assertAlmostEqual(wallet_pos["cost_basis"], 0.0)
                self.assertAlmostEqual(wallet_pos["realized_pnl"], 7.0)
                self.assertAlmostEqual(aggregate["size"], 0.0)
                self.assertAlmostEqual(aggregate["cost_basis"], 0.0)
                self.assertAlmostEqual(aggregate["realized_pnl"], 7.0)
        finally:
            os.remove(db_path)


if __name__ == "__main__":
    unittest.main()
