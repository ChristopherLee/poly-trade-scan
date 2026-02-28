"""Resolution monitoring worker for Polymarket markets."""
import asyncio
import json
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from src import db
from src.api.polymarket import PolymarketWSClient
from src.utils.logging import get_logger

ssl_context = ssl._create_unverified_context()
log = get_logger(__name__)


class ResolutionWorker:
    """Tracks open positions and applies market resolution updates."""

    def __init__(self, db_path: Optional[str] = None, poll_interval_seconds: int = 1800) -> None:
        self.db_path = db_path
        self.poll_interval_seconds = poll_interval_seconds

    def _parse_maybe_json_list(self, value):
        """Parse a list that may be a native list or JSON-encoded string."""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else None
            except Exception:
                return None
        return None

    def _normalize_payouts(self, payload: dict, clob_ids: list):
        """Normalize payouts from supported payload fields.

        Returns a tuple: (payouts, source_name) or None when unavailable/invalid.
        """
        source_candidates = [
            ("resolver_raw_payouts", payload.get("resolver_raw_payouts")),
            ("outcomePrices", payload.get("outcomePrices")),
        ]

        condition_id = payload.get("conditionId") or payload.get("condition_id")
        token_id = payload.get("token_id")
        dedupe_key = payload.get("dedupe_key")

        for source_name, raw_value in source_candidates:
            if raw_value is None:
                continue

            parsed = self._parse_maybe_json_list(raw_value)
            if parsed is None:
                log.warning(
                    "Resolution payouts field is not a parseable list",
                    dedupe_key=dedupe_key,
                    condition_id=condition_id,
                    token_id=token_id,
                    source=source_name,
                    raw_value=raw_value,
                )
                continue

            try:
                payouts = [float(value) for value in parsed]
            except (TypeError, ValueError):
                log.warning(
                    "Resolution payouts field contains non-numeric values",
                    dedupe_key=dedupe_key,
                    condition_id=condition_id,
                    token_id=token_id,
                    source=source_name,
                    raw_value=raw_value,
                )
                continue

            if not clob_ids or len(payouts) != len(clob_ids):
                log.warning(
                    "Resolution payouts length mismatch",
                    dedupe_key=dedupe_key,
                    condition_id=condition_id,
                    token_id=token_id,
                    source=source_name,
                    clob_count=len(clob_ids) if isinstance(clob_ids, list) else None,
                    payout_count=len(payouts),
                    raw_value=raw_value,
                )
                continue

            return payouts, source_name

        return None

    def process_resolution(self, conn: db.sqlite3.Connection, market_meta: dict) -> None:
        """Processes resolution for a market given its metadata."""
        cid = market_meta.get("condition_id")
        clob_ids = market_meta.get("clob_token_ids") or []
        payouts = market_meta.get("resolver_raw_payouts")

        log.info(
            "Processing resolution payload",
            condition_id=cid,
            token_count=len(clob_ids),
            payout_count=len(payouts) if isinstance(payouts, list) else None,
        )

        if not cid or not clob_ids or payouts is None:
            log.warning(
                "Ignoring invalid resolution payload",
                has_condition_id=bool(cid),
                has_clob_ids=bool(clob_ids),
                has_payouts=payouts is not None,
            )
            return

        tokens_in_db = conn.execute(
            "SELECT token_id FROM markets WHERE condition_id = ?", (cid,)
        ).fetchall()
        log.info("Loaded DB tokens for condition", condition_id=cid, db_token_count=len(tokens_in_db))

        resolved_tokens = 0
        skipped_tokens = 0
        for row in tokens_in_db:
            tid = row["token_id"]
            if tid not in clob_ids:
                skipped_tokens += 1
                continue

            idx = clob_ids.index(tid)
            payout_value = float(payouts[idx])

            mkt_status = conn.execute(
                "SELECT resolved FROM markets WHERE token_id=?", (tid,)
            ).fetchone()
            if mkt_status and mkt_status["resolved"]:
                skipped_tokens += 1
                continue

            db.mark_resolved(conn, tid, idx, payout_value)
            resolved_tokens += 1

            pos = db.get_position(conn, tid)
            if pos and pos["size"] > 0.0001:
                realized_gain = (payout_value * pos["size"]) - pos["cost_basis"]
                new_realized = pos["realized_pnl"] + realized_gain
                db.upsert_position(conn, tid, 0.0, 0.0, new_realized)

                mkt = conn.execute(
                    "SELECT question FROM markets WHERE token_id=?", (tid,)
                ).fetchone()
                log.info(
                    "Applied position settlement",
                    token_id=tid,
                    question=mkt["question"] if mkt else tid[:20],
                    payout=payout_value,
                    realized_gain=round(realized_gain, 2),
                )

        log.info(
            "Resolution payload processing complete",
            condition_id=cid,
            resolved_tokens=resolved_tokens,
            skipped_tokens=skipped_tokens,
        )

    def check_resolutions(self) -> None:
        """Poll Gamma API for unresolved markets that still have open positions."""
        now = time.time()
        success_cooldown_seconds = 4 * 60 * 60
        error_backoff_seconds = [15 * 60, 30 * 60, 60 * 60, 2 * 60 * 60, 4 * 60 * 60]
        global_backoff_failures = 0
        global_next_request_at = 0.0

        with db.transaction(db_path=self.db_path) as conn:
            due_rows = conn.execute(
                "SELECT DISTINCT m.token_id, m.condition_id, m.next_resolution_check "
                "FROM positions p "
                "JOIN markets m ON p.token_id = m.token_id "
                "WHERE p.size > 0.0001 "
                "AND m.resolved = 0 "
                "AND (m.next_resolution_check IS NULL OR m.next_resolution_check <= ?)",
                (now,),
            ).fetchall()

            skipped_rows = conn.execute(
                "SELECT DISTINCT m.token_id, m.condition_id, m.next_resolution_check "
                "FROM positions p "
                "JOIN markets m ON p.token_id = m.token_id "
                "WHERE p.size > 0.0001 "
                "AND m.resolved = 0 "
                "AND m.next_resolution_check IS NOT NULL "
                "AND m.next_resolution_check > ?",
                (now,),
            ).fetchall()

            log.info(
                "Resolution poll cycle",
                due_markets=len(due_rows),
                cooling_down_markets=len(skipped_rows),
                poll_timestamp=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            )

            for row in skipped_rows:
                token_or_condition = row["condition_id"] or row["token_id"]
                next_check = datetime.fromtimestamp(row["next_resolution_check"], tz=timezone.utc).isoformat()
                log.info("Skipping Gamma check (cooldown)", target=token_or_condition, next_check=next_check)

            if not due_rows:
                return

            processed_conditions = set()
            for row in due_rows:
                check_started_at = time.time()
                tid = row["token_id"]
                cid = row["condition_id"]
                dedupe_key = cid or tid

                if dedupe_key in processed_conditions:
                    log.info("Skipping duplicate condition in same cycle", dedupe_key=dedupe_key)
                    continue
                processed_conditions.add(dedupe_key)

                market_token_ids = [
                    r["token_id"]
                    for r in conn.execute(
                        "SELECT token_id FROM markets WHERE condition_id = ?",
                        (cid,),
                    ).fetchall()
                ] if cid else [tid]

                def _update_schedule(last_check: Optional[float], next_check: Optional[float], failures: int) -> None:
                    placeholders = ",".join("?" for _ in market_token_ids)
                    conn.execute(
                        f"UPDATE markets SET last_resolution_check=?, next_resolution_check=?, resolution_check_failures=? "
                        f"WHERE token_id IN ({placeholders})",
                        (last_check, next_check, failures, *market_token_ids),
                    )

                if check_started_at < global_next_request_at:
                    _update_schedule(check_started_at, global_next_request_at, global_backoff_failures)
                    next_check_iso = datetime.fromtimestamp(global_next_request_at, tz=timezone.utc).isoformat()
                    log.info("Global Gamma cooldown active", dedupe_key=dedupe_key, next_check=next_check_iso)
                    continue

                url = f"https://gamma-api.polymarket.com/markets?clob_token_ids={tid}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                data = None
                response_error = None
                status_code = None

                log.info("Calling Gamma resolution endpoint", dedupe_key=dedupe_key, token_id=tid)
                try:
                    response = urllib.request.urlopen(req, context=ssl_context)
                    status_code = getattr(response, "status", None)
                    data = json.loads(response.read())
                    log.info("Gamma response received", dedupe_key=dedupe_key, status_code=status_code, rows=len(data) if isinstance(data, list) else None)
                except urllib.error.HTTPError as e:
                    status_code = e.code
                    response_error = e
                except Exception as e:
                    response_error = e

                current_failures = conn.execute(
                    "SELECT COALESCE(MAX(resolution_check_failures), 0) AS failures "
                    "FROM markets WHERE token_id IN ({})".format(
                        ",".join("?" for _ in market_token_ids)
                    ),
                    market_token_ids,
                ).fetchone()["failures"]

                if response_error:
                    next_failures = current_failures + 1
                    delay = error_backoff_seconds[min(next_failures - 1, len(error_backoff_seconds) - 1)]
                    next_check = check_started_at + delay
                    _update_schedule(check_started_at, next_check, next_failures)

                    if status_code == 429:
                        global_backoff_failures += 1
                        global_delay = error_backoff_seconds[
                            min(global_backoff_failures - 1, len(error_backoff_seconds) - 1)
                        ]
                        global_next_request_at = check_started_at + global_delay

                    next_check_iso = datetime.fromtimestamp(next_check, tz=timezone.utc).isoformat()
                    log.warning(
                        "Gamma check failed",
                        dedupe_key=dedupe_key,
                        status_code=status_code,
                        failures=next_failures,
                        next_check=next_check_iso,
                        error=str(response_error),
                    )
                    continue

                global_backoff_failures = 0
                global_next_request_at = 0.0

                if not data:
                    next_check = check_started_at + success_cooldown_seconds
                    _update_schedule(check_started_at, next_check, 0)
                    next_check_iso = datetime.fromtimestamp(next_check, tz=timezone.utc).isoformat()
                    log.info("No Gamma data for market", dedupe_key=dedupe_key, next_check=next_check_iso)
                    continue

                found_resolution = False
                for market_payload in data:
                    clob_ids = self._parse_maybe_json_list(market_payload.get("clobTokenIds")) or []
                    if tid not in clob_ids:
                        continue

                    if market_payload.get("resolved") or market_payload.get("closed"):
                        payload_for_normalize = {
                            **market_payload,
                            "token_id": tid,
                            "dedupe_key": dedupe_key,
                        }
                        normalized = self._normalize_payouts(payload_for_normalize, clob_ids)
                        if not normalized:
                            log.warning(
                                "Gamma market is closed/resolved but payouts unavailable/invalid",
                                dedupe_key=dedupe_key,
                                condition_id=market_payload.get("conditionId"),
                                token_id=tid,
                            )
                            continue

                        payouts, source_name = normalized
                        found_resolution = True
                        if source_name != "resolver_raw_payouts":
                            log.info(
                                "Gamma resolution used fallback payout source",
                                dedupe_key=dedupe_key,
                                condition_id=market_payload.get("conditionId"),
                                token_id=tid,
                                source=source_name,
                            )

                        log.info(
                            "Gamma indicates market resolved",
                            dedupe_key=dedupe_key,
                            condition_id=market_payload.get("conditionId"),
                            token_id=tid,
                            payout_source=source_name,
                        )
                        self.process_resolution(
                            conn,
                            {
                                "condition_id": market_payload.get("conditionId"),
                                "clob_token_ids": clob_ids,
                                "resolver_raw_payouts": payouts,
                            },
                        )
                        break

                if found_resolution:
                    log.info("Resolution applied from Gamma poll", dedupe_key=dedupe_key)
                    continue

                next_check = check_started_at + success_cooldown_seconds
                _update_schedule(check_started_at, next_check, 0)
                next_check_iso = datetime.fromtimestamp(next_check, tz=timezone.utc).isoformat()
                log.info("Market still unresolved", dedupe_key=dedupe_key, next_check=next_check_iso)

    async def on_market_resolved(self, event: dict) -> None:
        """Handle instantaneous market resolution from Polymarket WS."""
        data = event.get("data", event)
        condition_id = data.get("condition_id") or data.get("conditionId")
        clob_ids = self._parse_maybe_json_list(data.get("clob_token_ids"))
        if clob_ids is None:
            clob_ids = self._parse_maybe_json_list(data.get("clobTokenIds"))

        log.info("WS market_resolved event received", condition_id=condition_id, raw_keys=sorted(list(data.keys())))

        if not condition_id or not clob_ids:
            log.warning(
                "Skipping WS resolution event due to missing identifiers",
                condition_id=condition_id,
                has_clob_ids=bool(clob_ids),
                present_keys=sorted(list(data.keys())),
            )
            return

        normalized = self._normalize_payouts(data, clob_ids)
        if not normalized:
            log.warning(
                "WS market is closed/resolved but payouts unavailable/invalid",
                dedupe_key=condition_id,
                condition_id=condition_id,
                token_id=clob_ids[0] if clob_ids else None,
            )
            return

        payouts, source_name = normalized
        if source_name != "resolver_raw_payouts":
            log.info(
                "WS resolution used fallback payout source",
                dedupe_key=condition_id,
                condition_id=condition_id,
                token_id=clob_ids[0] if clob_ids else None,
                source=source_name,
            )

        with db.transaction(db_path=self.db_path) as conn:
            self.process_resolution(
                conn,
                {
                    "condition_id": condition_id,
                    "clob_token_ids": clob_ids,
                    "resolver_raw_payouts": payouts,
                },
            )

    async def run(self) -> None:
        """Run websocket listener plus periodic poll loop."""
        db.init_db(self.db_path)
        log.info("Starting resolution worker", poll_interval_seconds=self.poll_interval_seconds, db_path=self.db_path or "paper_trades.db")

        pm_client = PolymarketWSClient()
        pm_client.on("market_resolved", self.on_market_resolved)
        asyncio.create_task(pm_client.start())

        while True:
            await asyncio.to_thread(self.check_resolutions)
            await asyncio.sleep(self.poll_interval_seconds)
