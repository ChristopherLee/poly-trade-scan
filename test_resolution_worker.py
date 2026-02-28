import asyncio
import json
import os
import ssl
import tempfile
import unittest
import urllib.request

from src import db
from src.resolution_worker import ResolutionWorker


class TestResolutionWorkerRealPayload(unittest.TestCase):
    TOKEN_ID = "36278286187204114350073024625104927267013601459327508958711396294252312530878"
    URL = f"https://gamma-api.polymarket.com/markets?clob_token_ids={TOKEN_ID}"

    def test_real_closed_market_payload_marks_market_resolved(self):
        ssl_context = ssl._create_unverified_context()
        req = urllib.request.Request(self.URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ssl_context) as response:
            payload = json.loads(response.read())

        self.assertIsInstance(payload, list)
        self.assertGreater(len(payload), 0)

        market = None
        for candidate in payload:
            clob_ids = ResolutionWorker()._parse_maybe_json_list(candidate.get("clobTokenIds")) or []
            if self.TOKEN_ID in clob_ids and (candidate.get("closed") or candidate.get("resolved")):
                market = candidate
                break

        self.assertIsNotNone(market, "Expected closed/resolved market payload for known token id")

        worker = ResolutionWorker()
        clob_ids = worker._parse_maybe_json_list(market.get("clobTokenIds")) or []
        self.assertIn(self.TOKEN_ID, clob_ids)

        normalized = worker._normalize_payouts(
            {
                "conditionId": market.get("conditionId"),
                "outcomePrices": market.get("outcomePrices"),
            },
            clob_ids,
        )
        self.assertIsNotNone(normalized, "Expected payouts from outcomePrices to normalize")

        payouts, source_name = normalized
        self.assertEqual(source_name, "outcomePrices")

        target_index = clob_ids.index(self.TOKEN_ID)
        expected_payout = payouts[target_index]

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db.init_db(db_path)
            with db.transaction(db_path=db_path) as conn:
                for idx, token in enumerate(clob_ids):
                    db.upsert_market(
                        conn,
                        token_id=token,
                        question=market.get("question", ""),
                        outcome_idx=idx,
                        condition_id=market.get("conditionId", ""),
                    )
                db.upsert_position(conn, self.TOKEN_ID, size=5.0, cost_basis=2.0, realized_pnl=0.0)

            ws_event = {
                "conditionId": market.get("conditionId"),
                "clobTokenIds": market.get("clobTokenIds"),
                "outcomePrices": market.get("outcomePrices"),
                "closed": True,
            }
            asyncio.run(ResolutionWorker(db_path=db_path).on_market_resolved(ws_event))

            with db.transaction(db_path=db_path) as conn:
                resolved_row = conn.execute(
                    "SELECT resolved, payout_value FROM markets WHERE token_id = ?",
                    (self.TOKEN_ID,),
                ).fetchone()

            self.assertIsNotNone(resolved_row)
            self.assertEqual(resolved_row["resolved"], 1)
            self.assertAlmostEqual(float(resolved_row["payout_value"]), float(expected_payout), places=7)
        finally:
            os.remove(db_path)


if __name__ == "__main__":
    unittest.main()
