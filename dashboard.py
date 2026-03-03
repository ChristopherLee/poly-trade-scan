"""Dashboard web server — serves API + static HTML for paper trade visualization."""
import json
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import urllib.request

from src.db import get_connection, init_db
from src.utils.logging import get_logger

STATIC_DIR = Path(__file__).parent / "dashboard"
PORT = 8050
log = get_logger(__name__)


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_leaderboard(category: str, time_period: str, order_by: str, limit: int):
    url = (
        "https://data-api.polymarket.com/v1/leaderboard"
        f"?category={category}&timePeriod={time_period}&orderBy={order_by}&limit={limit}"
    )
    data = fetch_json(url) or []
    results = []
    for user in data:
        addr = user.get("proxyWallet") or user.get("address") or user.get("wallet")
        if not addr:
            continue
        results.append({
            "address": addr.lower(),
            "alias": user.get("userName", ""),
            "pnl": user.get("pnl", 0),
            "vol": user.get("vol", 0),
            "category": category,
        })
    return results


def _decode_outcomes(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except Exception:
        return []


def _mark_position_unrealized(state):
    size = float(state.get("size") or 0.0)
    cost_basis = float(state.get("cost_basis") or 0.0)
    if size <= 0.0001:
        return 0.0

    if state.get("resolved"):
        payout_value = float(state.get("payout_value") or 0.0)
        return (payout_value * size) - cost_basis

    last_price = state.get("last_price")
    mark_price = float(last_price) if last_price is not None else (cost_basis / size)
    return (mark_price * size) - cost_basis


def _build_wallet_pnl_timeline(trade_rows, position_state):
    timeline = []
    if not trade_rows:
        trade_rows = []

    timeline_state = {}
    cumulative_realized = 0.0
    open_positions = 0

    for trade in trade_rows:
        token_id = trade["token_id"]
        state = timeline_state.setdefault(
            token_id,
            {
                "size": 0.0,
                "cost_basis": 0.0,
                "realized_pnl": 0.0,
                "last_price": position_state.get(token_id, {}).get("last_price"),
                "resolved": position_state.get(token_id, {}).get("resolved"),
                "payout_value": position_state.get(token_id, {}).get("payout_value"),
            },
        )
        final_state = position_state.get(token_id, {})
        if final_state:
            state["last_price"] = final_state.get("last_price")
            state["resolved"] = final_state.get("resolved")
            state["payout_value"] = final_state.get("payout_value")

        filled = trade.get("paper_id") is not None and not trade.get("no_fill_reason") and (trade.get("paper_size") or 0) > 0
        if filled:
            side = (trade.get("paper_side") or trade.get("target_side") or "").upper()
            paper_size = float(trade.get("paper_size") or 0.0)
            paper_price = float(trade.get("paper_price") or 0.0)
            paper_cost = float(trade.get("paper_cost") or 0.0)
            was_open = state["size"] > 0.0001

            if side == "BUY":
                state["cost_basis"] += paper_cost
                state["size"] += paper_size
            elif side == "SELL" and state["size"] > 0.0001:
                avg_entry = state["cost_basis"] / state["size"]
                shares_to_close = min(paper_size, state["size"])
                realized_delta = shares_to_close * (paper_price - avg_entry)
                state["realized_pnl"] += realized_delta
                cumulative_realized += realized_delta
                state["size"] -= shares_to_close
                state["cost_basis"] -= shares_to_close * avg_entry
                if state["size"] <= 0.0001:
                    state["size"] = 0.0
                    state["cost_basis"] = 0.0

            is_open = state["size"] > 0.0001
            if not was_open and is_open:
                open_positions += 1
            elif was_open and not is_open:
                open_positions = max(open_positions - 1, 0)

        cumulative_unrealized = sum(_mark_position_unrealized(state) for state in timeline_state.values())
        point_ts = trade.get("paper_created_at") or trade.get("target_created_at") or trade.get("onchain_ts")
        timeline.append({
            "ts": point_ts,
            "realized_pnl": round(cumulative_realized, 2),
            "unrealized_pnl": round(cumulative_unrealized, 2),
            "total_pnl": round(cumulative_realized + cumulative_unrealized, 2),
            "open_positions": open_positions,
        })

    settlement_events = []
    for token_id, final_state in position_state.items():
        if not final_state.get("resolved"):
            continue
        settlement_ts = final_state.get("wallet_position_updated_at")
        payout_value = final_state.get("payout_value")
        size = float(final_state.get("size") or 0.0)
        cost_basis = float(final_state.get("cost_basis") or 0.0)
        last_trade_ts = final_state.get("last_trade_ts") or 0
        if settlement_ts is None or settlement_ts <= last_trade_ts or size <= 0.0001:
            continue
        settlement_events.append((float(settlement_ts), token_id, float(payout_value or 0.0)))

    settlement_events.sort(key=lambda item: (item[0], item[1]))
    for settlement_ts, token_id, payout_value in settlement_events:
        state = timeline_state.setdefault(
            token_id,
            {
                "size": 0.0,
                "cost_basis": 0.0,
                "realized_pnl": 0.0,
                "last_price": None,
                "resolved": True,
                "payout_value": payout_value,
            },
        )
        if state["size"] <= 0.0001:
            continue
        realized_delta = (payout_value * state["size"]) - state["cost_basis"]
        state["resolved"] = True
        state["payout_value"] = payout_value
        state["realized_pnl"] += realized_delta
        cumulative_realized += realized_delta
        state["size"] = 0.0
        state["cost_basis"] = 0.0
        open_positions = max(open_positions - 1, 0)

        cumulative_unrealized = sum(_mark_position_unrealized(state) for state in timeline_state.values())
        timeline.append({
            "ts": settlement_ts,
            "realized_pnl": round(cumulative_realized, 2),
            "unrealized_pnl": round(cumulative_unrealized, 2),
            "total_pnl": round(cumulative_realized + cumulative_unrealized, 2),
            "open_positions": open_positions,
        })

    return timeline


def _build_wallet_detail_payload(conn, wallet: str):
    wallet_row = conn.execute(
        """
        SELECT w.*,
               (SELECT COUNT(*) FROM target_trades tt WHERE tt.wallet = w.address) as trade_count,
               (SELECT COALESCE(SUM(pt.cost_usd), 0)
                FROM paper_trades pt
                JOIN target_trades tt ON pt.target_trade_id = tt.id
                WHERE tt.wallet = w.address) as paper_volume
        FROM wallets w
        WHERE w.address = ?
        """,
        (wallet,),
    ).fetchone()

    if wallet_row:
        wallet_data = dict(wallet_row)
    else:
        trade_stats = conn.execute(
            """
            SELECT ? as address,
                   '' as alias,
                   'observed' as source,
                   0.0 as leaderboard_pnl,
                   0.0 as leaderboard_vol,
                   MIN(tt.created_at) as added_at,
                   0 as tracking_enabled,
                   NULL as enabled_at,
                   NULL as disabled_at,
                   COUNT(*) as trade_count,
                   COALESCE(SUM(pt.cost_usd), 0) as paper_volume
            FROM target_trades tt
            LEFT JOIN paper_trades pt ON pt.target_trade_id = tt.id
            WHERE tt.wallet = ?
            """,
            (wallet, wallet),
        ).fetchone()
        if not trade_stats or not trade_stats["trade_count"]:
            return None
        wallet_data = dict(trade_stats)

    rows = conn.execute(
        """
        SELECT tt.id as target_id, tt.wallet, tt.token_id, tt.tx_hash, tt.block_number,
               tt.side as target_side, tt.size as target_size, tt.price as target_price,
               tt.cost_usd as target_cost, tt.onchain_ts, tt.detected_ts, tt.created_at as target_created_at,
               pt.id as paper_id, pt.side as paper_side, pt.size as paper_size, pt.avg_price as paper_price,
               pt.cost_usd as paper_cost, pt.slippage, pt.orderbook_latency_ms, pt.detection_delay_ms,
               pt.execution_delay_ms, pt.total_delay_ms, pt.no_fill_reason, pt.requested_size,
               pt.source_position_fraction, pt.source_wallet_position_before, pt.position_mismatch_reason,
               pt.created_at as paper_created_at,
               m.question, m.outcomes, m.outcome_idx, m.resolved, m.payout_value, m.category,
               m.group_item_title, m.slug,
               wp.updated_at as wallet_position_updated_at,
               (SELECT pt2.avg_price FROM paper_trades pt2
                WHERE pt2.token_id = tt.token_id
                ORDER BY pt2.created_at DESC LIMIT 1) as last_price
        FROM target_trades tt
        LEFT JOIN paper_trades pt ON pt.target_trade_id = tt.id
        LEFT JOIN markets m ON m.token_id = tt.token_id
        LEFT JOIN wallet_positions wp ON wp.wallet = tt.wallet AND wp.token_id = tt.token_id
        WHERE tt.wallet = ?
        ORDER BY COALESCE(pt.created_at, tt.created_at) ASC, tt.id ASC
        """,
        (wallet,),
    ).fetchall()

    position_state = {}
    trade_rows = []
    filled_trade_pnls = []
    filled_count = 0
    no_fill_count = 0
    opened_count = 0
    closed_count = 0
    slippage_total = 0.0
    slippage_count = 0
    latency_total = 0.0
    latency_count = 0

    for row in rows:
        trade = dict(row)
        trade["outcomes"] = _decode_outcomes(trade.get("outcomes"))
        token_id = trade["token_id"]
        state = position_state.setdefault(
            token_id,
            {
                "token_id": token_id,
                "question": trade.get("question"),
                "outcomes": trade.get("outcomes") or [],
                "outcome_idx": trade.get("outcome_idx"),
                "resolved": trade.get("resolved"),
                "payout_value": trade.get("payout_value"),
                "category": trade.get("category"),
                "group_item_title": trade.get("group_item_title"),
                "slug": trade.get("slug"),
                "last_price": trade.get("last_price"),
                "wallet_position_updated_at": trade.get("wallet_position_updated_at"),
                "entry_ts": trade.get("onchain_ts"),
                "last_trade_ts": trade.get("paper_created_at") or trade.get("target_created_at"),
                "size": 0.0,
                "cost_basis": 0.0,
                "realized_pnl": 0.0,
                "filled_trades": 0,
                "total_bought": 0.0,
                "total_sold": 0.0,
                "total_cost": 0.0,
                "total_proceeds": 0.0,
            },
        )

        state["question"] = trade.get("question") or state["question"]
        state["outcomes"] = trade.get("outcomes") or state["outcomes"]
        state["outcome_idx"] = trade.get("outcome_idx") if trade.get("outcome_idx") is not None else state["outcome_idx"]
        state["resolved"] = trade.get("resolved") if trade.get("resolved") is not None else state["resolved"]
        state["payout_value"] = trade.get("payout_value") if trade.get("payout_value") is not None else state["payout_value"]
        state["category"] = trade.get("category") or state["category"]
        state["group_item_title"] = trade.get("group_item_title") or state["group_item_title"]
        state["slug"] = trade.get("slug") or state["slug"]
        state["last_price"] = trade.get("last_price") if trade.get("last_price") is not None else state["last_price"]
        state["wallet_position_updated_at"] = (
            trade.get("wallet_position_updated_at")
            if trade.get("wallet_position_updated_at") is not None
            else state["wallet_position_updated_at"]
        )
        state["entry_ts"] = min(x for x in [state["entry_ts"], trade.get("onchain_ts")] if x is not None)
        state["last_trade_ts"] = max(x for x in [state["last_trade_ts"], trade.get("paper_created_at") or trade.get("target_created_at")] if x is not None)

        filled = trade.get("paper_id") is not None and not trade.get("no_fill_reason") and (trade.get("paper_size") or 0) > 0
        trade["position_effect"] = "No Fill"
        trade["realized_pnl"] = 0.0
        trade["trade_pnl"] = None

        if trade.get("paper_id") is not None:
            latency_total += float(trade.get("total_delay_ms") or 0.0)
            latency_count += 1

        if filled:
            filled_count += 1
            state["filled_trades"] += 1
            slippage_total += float(trade.get("slippage") or 0.0)
            slippage_count += 1

            side = (trade.get("paper_side") or trade.get("target_side") or "").upper()
            paper_size = float(trade.get("paper_size") or 0.0)
            paper_price = float(trade.get("paper_price") or 0.0)
            paper_cost = float(trade.get("paper_cost") or 0.0)

            if side == "BUY":
                was_open = state["size"] > 0.0001
                state["cost_basis"] += paper_cost
                state["size"] += paper_size
                state["total_bought"] += paper_size
                state["total_cost"] += paper_cost
                trade["position_effect"] = "Added" if was_open else "Opened"
                if not was_open:
                    opened_count += 1
            elif side == "SELL":
                if state["size"] > 0.0001:
                    avg_entry = state["cost_basis"] / state["size"]
                    shares_to_close = min(paper_size, state["size"])
                    realized_delta = shares_to_close * (paper_price - avg_entry)
                    state["realized_pnl"] += realized_delta
                    state["total_sold"] += shares_to_close
                    state["total_proceeds"] += shares_to_close * paper_price
                    state["size"] -= shares_to_close
                    state["cost_basis"] -= shares_to_close * avg_entry
                    trade["realized_pnl"] = round(realized_delta, 2)
                    if state["size"] <= 0.0001:
                        state["size"] = 0.0
                        state["cost_basis"] = 0.0
                        trade["position_effect"] = "Closed"
                        closed_count += 1
                    else:
                        trade["position_effect"] = "Reduced"
                else:
                    trade["position_effect"] = "No Position"

            reference_price = trade.get("payout_value") if trade.get("resolved") else trade.get("last_price")
            if reference_price is None:
                # Missing market data should not invent trade PnL; fall back to our fill price.
                reference_price = paper_price
            reference_price = float(reference_price)
            if side == "BUY":
                trade["trade_pnl"] = round((reference_price - paper_price) * paper_size, 2)
            elif side == "SELL":
                trade["trade_pnl"] = round((paper_price - reference_price) * paper_size, 2)

            filled_trade_pnls.append(trade["trade_pnl"])
        else:
            no_fill_count += 1

        trade_rows.append(trade)

    positions = []
    total_realized = 0.0
    total_unrealized = 0.0
    active_positions = 0
    resolved_positions = 0

    for state in position_state.values():
        if state["filled_trades"] <= 0:
            continue

        size = float(state["size"] or 0.0)
        cost_basis = float(state["cost_basis"] or 0.0)
        realized = float(state["realized_pnl"] or 0.0)
        payout_value = state.get("payout_value")
        last_price = state.get("last_price")
        resolved = bool(state.get("resolved"))
        total_bought = float(state.get("total_bought") or 0.0)
        total_sold = float(state.get("total_sold") or 0.0)
        total_cost = float(state.get("total_cost") or 0.0)
        total_proceeds = float(state.get("total_proceeds") or 0.0)

        unrealized = 0.0
        status = "Closed"
        settlement_proceeds = 0.0
        if resolved:
            resolved_positions += 1
            if size > 0.0001:
                settlement_proceeds = float(payout_value or 0.0) * size
                realized += settlement_proceeds - cost_basis
            status = "Resolved"
        else:
            if size > 0.0001:
                mark_price = float(last_price) if last_price is not None else (cost_basis / size)
                unrealized = (mark_price * size) - cost_basis
                active_positions += 1
                status = "Open"

        avg_entry = (total_cost / total_bought) if total_bought > 0.0001 else None
        total_exit_size = total_sold + (size if resolved else 0.0)
        total_exit_value = total_proceeds + settlement_proceeds
        avg_exit_value = (total_exit_value / total_exit_size) if total_exit_size > 0.0001 else None
        total_pnl = realized + unrealized
        roi_pct = ((total_pnl / total_cost) * 100.0) if total_cost > 0.0001 else None

        total_realized += realized
        total_unrealized += unrealized

        positions.append({
            "token_id": state["token_id"],
            "question": state.get("question"),
            "outcomes": state.get("outcomes") or [],
            "outcome_idx": state.get("outcome_idx"),
            "resolved": 1 if resolved else 0,
            "payout_value": payout_value,
            "category": state.get("category"),
            "group_item_title": state.get("group_item_title"),
            "slug": state.get("slug"),
            "entry_ts": state.get("entry_ts"),
            "last_trade_ts": state.get("last_trade_ts"),
            "open_size": round(size, 4),
            "open_cost_basis": round(cost_basis, 2),
            "total_bought": round(total_bought, 4),
            "total_sold": round(total_sold, 4),
            "total_cost": round(total_cost, 2),
            "total_proceeds": round(total_proceeds + settlement_proceeds, 2),
            "avg_entry": round(avg_entry, 4) if avg_entry is not None else None,
            "avg_exit_value": round(avg_exit_value, 4) if avg_exit_value is not None else None,
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_pnl": round(total_pnl, 2),
            "roi_pct": round(roi_pct, 2) if roi_pct is not None else None,
            "last_price": last_price,
            "filled_trades": state["filled_trades"],
            "status": status,
        })

    positions.sort(key=lambda item: (item["status"] != "Open", item["total_pnl"]))
    trade_rows.reverse()
    pnl_timeline = _build_wallet_pnl_timeline(list(reversed(trade_rows)), position_state)

    summary = {
        "total_target_trades": len(rows),
        "paper_trade_rows": sum(1 for row in trade_rows if row.get("paper_id") is not None),
        "filled_trades": filled_count,
        "no_fill_trades": no_fill_count,
        "opened_positions": opened_count,
        "closed_positions": closed_count,
        "active_positions": active_positions,
        "resolved_positions": resolved_positions,
        "paper_volume": round(sum(float(row.get("paper_cost") or 0.0) for row in trade_rows), 2),
        "avg_slippage": round(slippage_total / slippage_count, 4) if slippage_count else 0.0,
        "avg_latency_ms": round(latency_total / latency_count, 1) if latency_count else 0.0,
        "realized_pnl": round(total_realized, 2),
        "unrealized_pnl": round(total_unrealized, 2),
        "total_pnl": round(total_realized + total_unrealized, 2),
        "winning_trades": sum(1 for value in filled_trade_pnls if value > 0.01),
        "losing_trades": sum(1 for value in filled_trade_pnls if value < -0.01),
        "flat_trades": sum(1 for value in filled_trade_pnls if abs(value) <= 0.01),
    }

    return {
        "wallet": wallet_data,
        "summary": summary,
        "positions": positions,
        "trades": trade_rows,
        "pnl_timeline": pnl_timeline,
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves the dashboard SPA and JSON API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path.startswith("/api/"):
            self._handle_api(path, params)
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if not path.startswith("/api/"):
            self._json_response({"error": "not found"}, 404)
            return

        content_len = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_len) if content_len else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except Exception:
            self._json_response({"error": "invalid json body"}, 400)
            return

        conn = get_connection()
        try:
            if path == "/api/wallets":
                self._api_add_wallet(conn, payload)
            elif path == "/api/wallets/toggle":
                self._api_toggle_wallet(conn, payload)
            else:
                self._json_response({"error": "not found"}, 404)
        finally:
            conn.close()

    def _json_response(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle_api(self, path, params):
        conn = get_connection()
        try:
            if path == "/api/summary":
                self._api_summary(conn)
            elif path == "/api/wallets":
                self._api_wallets(conn)
            elif path == "/api/trades":
                self._api_trades(conn, params)
            elif path == "/api/wallet_detail":
                self._api_wallet_detail(conn, params)
            elif path == "/api/positions":
                self._api_positions(conn, params)
            elif path == "/api/markets":
                self._api_markets(conn, params)
            elif path == "/api/pnl_over_time":
                self._api_pnl_over_time(conn, params)
            elif path == "/api/pnl_by_category":
                self._api_pnl_by_category(conn)
            elif path == "/api/orderbook":
                self._api_orderbook(conn, params)
            elif path == "/api/latency_stats":
                self._api_latency_stats(conn)
            elif path == "/api/leaderboard":
                self._api_leaderboard(params)
            else:
                self._json_response({"error": "not found"}, 404)
        finally:
            conn.close()

    def _api_summary(self, conn):
        total_target = conn.execute("SELECT COUNT(*) as c FROM target_trades").fetchone()["c"]
        total_paper = conn.execute("SELECT COUNT(*) as c FROM paper_trades").fetchone()["c"]
        total_wallets = conn.execute("SELECT COUNT(*) as c FROM wallets WHERE tracking_enabled = 1").fetchone()["c"]

        resolved = conn.execute("SELECT COUNT(*) as c FROM markets WHERE resolved = 1").fetchone()["c"]
        unresolved_positions = conn.execute(
            """SELECT COUNT(*) as c FROM positions p
               JOIN markets m ON p.token_id = m.token_id
               WHERE p.size > 0.0001 AND m.resolved = 0"""
        ).fetchone()["c"]

        realized = conn.execute("SELECT COALESCE(SUM(realized_pnl), 0) as s FROM positions").fetchone()["s"]

        # unrealized: for open positions, use latest paper_trade avg_price as estimate
        unrealized_rows = conn.execute(
            """SELECT p.token_id, p.size, p.cost_basis,
                      (SELECT pt.avg_price FROM paper_trades pt WHERE pt.token_id = p.token_id ORDER BY pt.created_at DESC LIMIT 1) as last_price
               FROM positions p WHERE p.size > 0.0001"""
        ).fetchall()
        unrealized = sum(
            ((r["last_price"] if r["last_price"] is not None else (r["cost_basis"] / r["size"])) * r["size"]) - r["cost_basis"]
            for r in unrealized_rows
        )

        avg_slippage = conn.execute("SELECT AVG(slippage) as s FROM paper_trades").fetchone()["s"] or 0
        avg_latency = conn.execute("SELECT AVG(total_delay_ms) as s FROM paper_trades").fetchone()["s"] or 0

        total_volume = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) as s FROM paper_trades").fetchone()["s"]

        self._json_response({
            "total_target_trades": total_target,
            "total_paper_trades": total_paper,
            "total_wallets": total_wallets,
            "resolved_markets": resolved,
            "unresolved_positions": unresolved_positions,
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_pnl": round(realized + unrealized, 2),
            "avg_slippage": round(avg_slippage, 4),
            "avg_latency_ms": round(avg_latency, 1),
            "total_volume": round(total_volume, 2),
        })

    def _api_wallets(self, conn):
        rows = conn.execute("""
            SELECT w.*,
                   (SELECT COUNT(*) FROM target_trades tt WHERE tt.wallet = w.address) as trade_count,
                   (SELECT COALESCE(SUM(pt.cost_usd), 0) FROM paper_trades pt
                    JOIN target_trades tt ON pt.target_trade_id = tt.id
                    WHERE tt.wallet = w.address) as paper_volume
            FROM wallets w
            ORDER BY w.tracking_enabled DESC, COALESCE(w.enabled_at, w.added_at) DESC, w.leaderboard_pnl DESC
        """).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            wallet = item["address"]
            if item.get("trade_count"):
                payload = _build_wallet_detail_payload(conn, wallet)
                summary = payload["summary"] if payload else {}
                item["realized_pnl"] = summary.get("realized_pnl", 0.0)
                item["wallet_total_pnl"] = summary.get("total_pnl", 0.0)
            else:
                item["realized_pnl"] = 0.0
                item["wallet_total_pnl"] = 0.0
            result.append(item)
        self._json_response(result)

    def _api_add_wallet(self, conn, payload):
        address = (payload.get("address") or "").strip().lower()
        alias = (payload.get("alias") or "").strip()

        if not address:
            self._json_response({"error": "address is required"}, 400)
            return

        conn.execute(
            """
            INSERT INTO wallets (address, alias, source, leaderboard_pnl, leaderboard_vol, added_at, tracking_enabled, enabled_at, disabled_at)
            VALUES (?, ?, 'manual', 0, 0, ?, 1, ?, NULL)
            ON CONFLICT(address) DO UPDATE SET
                alias = CASE WHEN excluded.alias != '' THEN excluded.alias ELSE wallets.alias END,
                source = 'manual',
                tracking_enabled = 1,
                enabled_at = CASE WHEN wallets.tracking_enabled = 0 THEN excluded.enabled_at ELSE COALESCE(wallets.enabled_at, excluded.enabled_at) END,
                disabled_at = NULL
            """,
            (address, alias, time.time(), time.time()),
        )
        conn.commit()
        self._json_response({"ok": True, "address": address})

    def _api_toggle_wallet(self, conn, payload):
        address = (payload.get("address") or "").strip().lower()
        enabled = payload.get("enabled")

        if not address or not isinstance(enabled, bool):
            self._json_response({"error": "address and enabled(bool) are required"}, 400)
            return

        now = time.time()
        updated = conn.execute(
            """
            UPDATE wallets
            SET tracking_enabled = ?,
                enabled_at = CASE WHEN ? = 1 THEN ? ELSE enabled_at END,
                disabled_at = CASE WHEN ? = 0 THEN ? ELSE NULL END
            WHERE address = ?
            """,
            (1 if enabled else 0, 1 if enabled else 0, now, 1 if enabled else 0, now, address),
        )
        conn.commit()

        if updated.rowcount == 0:
            self._json_response({"error": "wallet not found"}, 404)
            return

        self._json_response({"ok": True, "address": address, "tracking_enabled": enabled})

    def _api_trades(self, conn, params):
        wallet = params.get("wallet", [None])[0]
        token_id = params.get("token_id", [None])[0]
        resolved_filter = params.get("resolved", [None])[0]
        limit = int(params.get("limit", [100])[0])
        offset = int(params.get("offset", [0])[0])

        query = """
            SELECT tt.id as target_id, tt.wallet, tt.token_id, tt.tx_hash, tt.block_number,
                   tt.side, tt.size as target_size, tt.price as target_price, tt.cost_usd as target_cost,
                   tt.onchain_ts, tt.detected_ts,
                   pt.id as paper_id, pt.size as paper_size, pt.avg_price as paper_price,
                   pt.cost_usd as paper_cost, pt.slippage,
                   pt.orderbook_latency_ms, pt.detection_delay_ms, pt.execution_delay_ms, pt.total_delay_ms,
                   pt.no_fill_reason, pt.requested_size, pt.source_position_fraction,
                   pt.source_wallet_position_before, pt.position_mismatch_reason,
                   m.question, m.outcomes, m.outcome_idx, m.resolved, m.payout_value, m.category, m.group_item_title, m.slug
            FROM target_trades tt
            LEFT JOIN paper_trades pt ON pt.target_trade_id = tt.id
            LEFT JOIN markets m ON m.token_id = tt.token_id
            WHERE 1=1
        """
        category = params.get("category", [None])[0]
        bindings = []
        if wallet:
            query += " AND tt.wallet = ?"
            bindings.append(wallet.lower())
        if token_id:
            query += " AND tt.token_id = ?"
            bindings.append(token_id)
        if category:
            query += " AND m.category = ?"
            bindings.append(category)
        if resolved_filter == "resolved":
            query += " AND m.resolved = 1"
        elif resolved_filter == "unresolved":
            query += " AND (m.resolved = 0 OR m.resolved IS NULL)"
        query += " ORDER BY tt.created_at DESC LIMIT ? OFFSET ?"
        bindings.extend([limit, offset])

        rows = conn.execute(query, bindings).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("outcomes"):
                try:
                    d["outcomes"] = json.loads(d["outcomes"])
                except:
                    pass
            result.append(d)
        self._json_response(result)

    def _api_positions(self, conn, params):
        resolved_filter = params.get("resolved", [None])[0]

        query = """
            SELECT p.token_id, p.size, p.cost_basis, p.realized_pnl, p.updated_at,
                   m.question, m.outcomes, m.outcome_idx, m.resolved, m.payout_value, m.category, m.group_item_title, m.slug,
                   (SELECT pt.avg_price FROM paper_trades pt WHERE pt.token_id = p.token_id ORDER BY pt.created_at DESC LIMIT 1) as last_price,
                   (SELECT COUNT(DISTINCT tt.wallet)
                    FROM paper_trades pt
                    JOIN target_trades tt ON tt.id = pt.target_trade_id
                    WHERE pt.token_id = p.token_id) as source_wallet_count,
                   (SELECT GROUP_CONCAT(DISTINCT tt.wallet)
                    FROM paper_trades pt
                    JOIN target_trades tt ON tt.id = pt.target_trade_id
                    WHERE pt.token_id = p.token_id) as source_wallets,
                   (SELECT MIN(pt.created_at) FROM paper_trades pt WHERE pt.token_id = p.token_id) as entry_ts,
                   m.resolved_at as resolved_ts
            FROM positions p
            LEFT JOIN markets m ON m.token_id = p.token_id
            WHERE 1=1
        """
        bindings = []
        if resolved_filter == "0":
            query += " AND (m.resolved = 0 OR m.resolved IS NULL)"
        elif resolved_filter == "1":
            query += " AND m.resolved = 1"

        query += " ORDER BY p.updated_at DESC"
        rows = conn.execute(query, bindings).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            if d.get("outcomes"):
                try:
                    d["outcomes"] = json.loads(d["outcomes"])
                except:
                    pass
            # Calculate unrealized PnL
            if d["size"] > 0.0001 and not d.get("resolved"):
                price = d.get("last_price")
                if price is None:
                    price = d["cost_basis"] / d["size"]
                d["unrealized_pnl"] = round(price * d["size"] - d["cost_basis"], 2)
            else:
                d["unrealized_pnl"] = 0
            result.append(d)
        self._json_response(result)

    def _api_wallet_detail(self, conn, params):
        wallet = (params.get("wallet", [None])[0] or "").strip().lower()
        if not wallet:
            self._json_response({"error": "missing wallet"}, 400)
            return

        payload = _build_wallet_detail_payload(conn, wallet)
        if not payload:
            self._json_response({"error": "wallet not found"}, 404)
            return
        self._json_response(payload)

    def _api_markets(self, conn, params):
        resolved_filter = params.get("resolved", [None])[0]
        query = "SELECT * FROM markets WHERE 1=1"
        bindings = []
        if resolved_filter is not None:
            query += " AND resolved = ?"
            bindings.append(int(resolved_filter))
        query += " ORDER BY first_seen DESC"
        rows = conn.execute(query, bindings).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("outcomes"):
                try:
                    d["outcomes"] = json.loads(d["outcomes"])
                except:
                    pass
            result.append(d)
        self._json_response(result)

    def _api_pnl_over_time(self, conn, params):
        wallet = params.get("wallet", [None])[0]

        query = """
            SELECT pt.created_at as ts, pt.side, pt.size, pt.avg_price, pt.cost_usd, pt.slippage,
                   tt.wallet, m.question
            FROM paper_trades pt
            JOIN target_trades tt ON pt.target_trade_id = tt.id
            LEFT JOIN markets m ON m.token_id = pt.token_id
            WHERE 1=1
        """
        bindings = []
        if wallet:
            query += " AND tt.wallet = ?"
            bindings.append(wallet.lower())
        query += " ORDER BY pt.created_at ASC"

        rows = conn.execute(query, bindings).fetchall()

        cumulative = 0.0
        points = []
        for r in rows:
            d = dict(r)
            side = str(d.get("side") or "").upper()
            cost = float(d.get("cost_usd") or 0.0)
            # Approximate PnL contribution per trade (negative for buys, positive for sells)
            if side == "SELL":
                cumulative += cost
            else:
                cumulative -= cost
            points.append({
                "ts": d["ts"],
                "cumulative_cost": round(cumulative, 2),
                "wallet": d["wallet"],
                "question": d.get("question", ""),
            })

        self._json_response(points)

    def _api_pnl_by_category(self, conn):
        """Aggregate realized/unrealized PnL by market category."""
        # Get all positions with their categories
        rows = conn.execute("""
            SELECT m.category, p.size, p.cost_basis, p.realized_pnl, m.resolved,
                   (SELECT pt.avg_price FROM paper_trades pt WHERE pt.token_id = p.token_id ORDER BY pt.created_at DESC LIMIT 1) as last_price
            FROM positions p
            JOIN markets m ON p.token_id = m.token_id
        """).fetchall()

        stats = {} # category -> {realized, unrealized}
        for r in rows:
            cat = r["category"] or "Other"
            if cat not in stats:
                stats[cat] = {"realized": 0.0, "unrealized": 0.0, "volume": 0.0}
            
            stats[cat]["realized"] += r["realized_pnl"]
            
            if r["size"] > 0.0001 and not r["resolved"]:
                price = r["last_price"]
                if price is None:
                    price = r["cost_basis"] / r["size"]
                unrealized = (price * r["size"]) - r["cost_basis"]
                stats[cat]["unrealized"] += unrealized

        # Volume by category
        vol_rows = conn.execute("""
            SELECT m.category, SUM(pt.cost_usd) as vol
            FROM paper_trades pt
            JOIN markets m ON pt.token_id = m.token_id
            GROUP BY m.category
        """).fetchall()
        for r in vol_rows:
            cat = r["category"] or "Other"
            if cat in stats:
                stats[cat]["volume"] = r["vol"]

        self._json_response([{"category": k, **v} for k, v in stats.items()])

    def _api_orderbook(self, conn, params):
        target_id = params.get("target_trade_id", [None])[0]
        if not target_id:
            return self._json_response({"error": "missing target_trade_id"}, 400)
        
        row = conn.execute("""
            SELECT * FROM orderbook_snapshots WHERE target_trade_id = ?
        """, (target_id,)).fetchone()
        
        if not row:
            return self._json_response({"error": "not found"}, 404)
        
        d = dict(row)
        try:
            d["bids"] = json.loads(d.pop("bids_json", "[]"))
            d["asks"] = json.loads(d.pop("asks_json", "[]"))
        except:
            d["bids"] = []
            d["asks"] = []
            
        self._json_response(d)

    def _api_latency_stats(self, conn):
        rows = conn.execute("""
            SELECT detection_delay_ms, execution_delay_ms, total_delay_ms, orderbook_latency_ms
            FROM paper_trades ORDER BY created_at DESC LIMIT 200
        """).fetchall()
        self._json_response([dict(r) for r in rows])

    def _api_leaderboard(self, params):
        category = (params.get("category", ["overall"])[0] or "overall").lower()
        time_period = (params.get("time_period", ["MONTH"])[0] or "MONTH").upper()
        order_by = (params.get("order_by", ["PNL"])[0] or "PNL").upper()
        limit = min(max(int(params.get("limit", [20])[0]), 1), 100)

        try:
            rows = fetch_leaderboard(category, time_period, order_by, limit)
        except Exception as exc:
            self._json_response({"error": f"leaderboard fetch failed: {exc}"}, 502)
            return

        self._json_response(rows)

    def log_message(self, format, *args):
        request_log_line = format % args
        log.info(
            "http_request",
            client_ip=self.address_string(),
            method=getattr(self, "command", ""),
            path=getattr(self, "path", ""),
            status=getattr(self, "_last_response_status", None),
            request_log_line=request_log_line,
        )

    def send_response(self, code, message=None):
        self._last_response_status = code
        super().send_response(code, message)


def main():
    init_db()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    log.info("dashboard_start", url=f"http://localhost:{PORT}", port=PORT)
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("dashboard_stop")
        server.server_close()


if __name__ == "__main__":
    main()
