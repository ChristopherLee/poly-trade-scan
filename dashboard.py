"""Dashboard web server — serves API + static HTML for paper trade visualization."""
import json
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import urllib.request

from src.db import get_connection, init_db

STATIC_DIR = Path(__file__).parent / "dashboard"
PORT = 8050


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
            (r["last_price"] or 0.5) * r["size"] - r["cost_basis"]
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
        self._json_response([dict(r) for r in rows])

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
                   pt.no_fill_reason,
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
                price = d.get("last_price") or 0.5
                d["unrealized_pnl"] = round(price * d["size"] - d["cost_basis"], 2)
            else:
                d["unrealized_pnl"] = 0
            result.append(d)
        self._json_response(result)

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
                price = r["last_price"] or 0.5
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
        """Suppress default access logs for cleaner output."""
        pass


def main():
    init_db()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Dashboard running at http://localhost:{PORT}")
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
