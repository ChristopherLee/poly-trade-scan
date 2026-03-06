"""Standalone live trading runner with risk controls + auditable execution records."""
import argparse
import asyncio
import hashlib
import importlib
import importlib.util
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import TYPE_CHECKING, Any

from live_paper_trade import fetch_market_metadata, fetch_orderbook, fetch_top_wallets, normalize_target_trade
from src import db
from src.core.models import TradeData
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from src.monitor import TradeMonitor

log = get_logger(__name__)
EPS = 1e-7


@dataclass
class RiskConfig:
    per_trade_usd_cap: float
    max_open_notional_usd: float
    max_daily_loss_usd: float
    max_open_positions: int
    min_cash_buffer_usd: float


@dataclass
class ExecutionResult:
    status: str
    requested_size: float
    filled_size: float
    avg_price: float
    notional_usd: float
    risk_flags: list[str]
    exchange_order_id: str | None = None
    exchange_tx_hash: str | None = None
    error_message: str | None = None


class RiskManager:
    def __init__(self, cfg: RiskConfig, starting_equity_usd: float):
        self.cfg = cfg
        self.starting_equity_usd = starting_equity_usd

    def evaluate_buy(self, conn, token_id: str, planned_notional_usd: float) -> tuple[bool, list[str], str | None]:
        flags: list[str] = []

        if planned_notional_usd > self.cfg.per_trade_usd_cap:
            return False, ["PER_TRADE_CAP"], f"planned notional ${planned_notional_usd:.2f} > cap ${self.cfg.per_trade_usd_cap:.2f}"

        open_notional = conn.execute(
            "SELECT COALESCE(SUM(cost_basis), 0) AS s FROM live_wallet_positions WHERE size > 0.0001"
        ).fetchone()["s"]
        if (open_notional + planned_notional_usd) > self.cfg.max_open_notional_usd:
            return False, ["OPEN_NOTIONAL_CAP"], (
                f"open notional ${open_notional:.2f} + planned ${planned_notional_usd:.2f} > cap ${self.cfg.max_open_notional_usd:.2f}"
            )

        open_positions = conn.execute(
            "SELECT COUNT(*) AS c FROM live_wallet_positions WHERE size > 0.0001"
        ).fetchone()["c"]
        token_already_open = conn.execute(
            "SELECT 1 FROM live_wallet_positions WHERE token_id = ? AND size > 0.0001", (token_id,)
        ).fetchone()
        if not token_already_open and open_positions >= self.cfg.max_open_positions:
            return False, ["OPEN_POSITION_CAP"], f"open positions {open_positions} >= cap {self.cfg.max_open_positions}"

        realized = conn.execute("SELECT COALESCE(SUM(realized_pnl), 0) AS s FROM live_wallet_positions").fetchone()["s"]
        if realized <= -abs(self.cfg.max_daily_loss_usd):
            return False, ["DAILY_LOSS_LIMIT"], f"realized pnl ${realized:.2f} breached loss limit ${-abs(self.cfg.max_daily_loss_usd):.2f}"

        equity_left = self.starting_equity_usd + realized - open_notional - planned_notional_usd
        if equity_left < self.cfg.min_cash_buffer_usd:
            return False, ["CASH_BUFFER"], f"cash buffer ${equity_left:.2f} < min ${self.cfg.min_cash_buffer_usd:.2f}"

        if planned_notional_usd >= 0.8 * self.cfg.per_trade_usd_cap:
            flags.append("NEAR_TRADE_CAP")

        return True, flags, None


class TradeExecutor:
    mode = "BASE"

    async def execute_buy(self, token_id: str, desired_notional_usd: float, orderbook: dict) -> ExecutionResult:
        raise NotImplementedError

    async def execute_sell(self, token_id: str, desired_size_shares: float, orderbook: dict) -> ExecutionResult:
        raise NotImplementedError


class DryRunExecutor(TradeExecutor):
    mode = "DRY_RUN"

    async def execute_buy(self, token_id: str, desired_notional_usd: float, orderbook: dict) -> ExecutionResult:
        filled_size, spent = _simulate_buy_fill(orderbook, desired_notional_usd)
        avg_price = (spent / filled_size) if filled_size > EPS else 0.0
        status = "FILLED" if filled_size > EPS else "NO_FILL"
        return ExecutionResult(
            status=status,
            requested_size=desired_notional_usd,
            filled_size=filled_size,
            avg_price=avg_price,
            notional_usd=spent,
            risk_flags=["DRY_RUN"],
            error_message="insufficient ask liquidity" if status == "NO_FILL" else None,
        )

    async def execute_sell(self, token_id: str, desired_size_shares: float, orderbook: dict) -> ExecutionResult:
        filled_size, proceeds = _simulate_sell_fill(orderbook, desired_size_shares)
        avg_price = (proceeds / filled_size) if filled_size > EPS else 0.0
        status = "FILLED" if filled_size > EPS else "NO_FILL"
        return ExecutionResult(
            status=status,
            requested_size=desired_size_shares,
            filled_size=filled_size,
            avg_price=avg_price,
            notional_usd=proceeds,
            risk_flags=["DRY_RUN", "EQUAL_WEIGHTED_SELL"],
            error_message="insufficient bid liquidity" if status == "NO_FILL" else None,
        )


class PolymarketClobExecutor(TradeExecutor):
    mode = "LIVE_CLOB"

    def __init__(self, host: str, chain_id: int, private_key: str, funder: str | None, signature_type: int):
        self.host = host
        self.chain_id = chain_id
        self.private_key = private_key
        self.funder = funder
        self.signature_type = signature_type
        self._client: Any = None

    def _build_client(self):
        if self._client is not None:
            return self._client
        if importlib.util.find_spec("py_clob_client") is None:
            raise RuntimeError("py_clob_client package is not installed. Install dependencies to place live orders.")

        client_module = importlib.import_module("py_clob_client.client")
        self._client = client_module.ClobClient(
            self.host,
            key=self.private_key,
            chain_id=self.chain_id,
            signature_type=self.signature_type,
            funder=self.funder,
        )
        if hasattr(self._client, "create_or_derive_api_creds") and hasattr(self._client, "set_api_creds"):
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)
        return self._client

    async def execute_buy(self, token_id: str, desired_notional_usd: float, orderbook: dict) -> ExecutionResult:
        return self._place_market_order(token_id, desired_notional_usd, "BUY", orderbook)

    async def execute_sell(self, token_id: str, desired_size_shares: float, orderbook: dict) -> ExecutionResult:
        return self._place_market_order(token_id, desired_size_shares, "SELL", orderbook)

    def _place_market_order(self, token_id: str, amount: float, side: str, orderbook: dict) -> ExecutionResult:
        client = self._build_client()
        if not hasattr(client, "create_market_order") or not hasattr(client, "post_order"):
            raise RuntimeError("Installed py_clob_client does not expose expected order methods")

        types_mod = importlib.import_module("py_clob_client.clob_types")
        constants_mod = importlib.import_module("py_clob_client.order_builder.constants")
        side_value = getattr(constants_mod, side)

        market_order_args = types_mod.MarketOrderArgs(
            token_id=str(token_id),
            amount=Decimal(str(amount)),
            side=side_value,
        )
        signed_order = client.create_market_order(market_order_args)
        placed = client.post_order(signed_order)

        success = True
        order_id = None
        tx_hash = None
        message = None
        if isinstance(placed, dict):
            success = bool(placed.get("success", True))
            order_id = placed.get("orderID") or placed.get("orderId")
            tx_hash = placed.get("transactionHash") or placed.get("txHash")
            if not success:
                message = placed.get("errorMsg") or placed.get("error") or "order rejected"

        if side == "BUY":
            filled_size, cash_value = _simulate_buy_fill(orderbook, amount)
            flags = ["LIVE_ORDER_ATTEMPTED"]
        else:
            filled_size, cash_value = _simulate_sell_fill(orderbook, amount)
            flags = ["LIVE_ORDER_ATTEMPTED", "EQUAL_WEIGHTED_SELL"]

        avg_price = (cash_value / filled_size) if filled_size > EPS else 0.0
        status = "FILLED" if success else "REJECTED"

        return ExecutionResult(
            status=status,
            requested_size=amount,
            filled_size=filled_size,
            avg_price=avg_price,
            notional_usd=cash_value,
            risk_flags=flags,
            exchange_order_id=order_id,
            exchange_tx_hash=tx_hash,
            error_message=message,
        )


def build_executor(args: argparse.Namespace) -> TradeExecutor:
    if not args.allow_live_orders:
        return DryRunExecutor()

    private_key = args.clob_private_key or os.getenv("POLY_CLOB_PRIVATE_KEY")
    if not private_key:
        raise RuntimeError("Live order mode requested but no private key provided. Use --clob-private-key or POLY_CLOB_PRIVATE_KEY")

    host = args.clob_host or os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com")
    funder = args.clob_funder or os.getenv("POLY_CLOB_FUNDER")
    chain_id = int(args.clob_chain_id or os.getenv("POLY_CLOB_CHAIN_ID", "137"))
    signature_type = int(args.clob_signature_type or os.getenv("POLY_CLOB_SIGNATURE_TYPE", "1"))
    return PolymarketClobExecutor(host=host, chain_id=chain_id, private_key=private_key, funder=funder, signature_type=signature_type)


def _make_audit_ref(trade: TradeData) -> str:
    payload = f"{trade.transaction_hash}:{trade.log_index}:{trade.token_id}:{trade.wallet}:{trade.timestamp}"
    return f"audit-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def _simulate_buy_fill(orderbook: dict, max_notional: float) -> tuple[float, float]:
    shares = 0.0
    spent = 0.0
    asks = sorted(orderbook.get("asks", []), key=lambda x: float(x["price"]))
    for ask in asks:
        price = float(ask["price"])
        size = float(ask["size"])
        level_cost = price * size
        if spent + level_cost >= max_notional:
            remaining = max_notional - spent
            if remaining > 0:
                shares += remaining / price
                spent += remaining
            break
        shares += size
        spent += level_cost
    return shares, spent


def _simulate_sell_fill(orderbook: dict, desired_size: float) -> tuple[float, float]:
    shares = 0.0
    proceeds = 0.0
    remaining = float(desired_size)
    bids = sorted(orderbook.get("bids", []), key=lambda x: float(x["price"]), reverse=True)
    for bid in bids:
        if remaining <= EPS:
            break
        price = float(bid["price"])
        size = float(bid["size"])
        fill = min(size, remaining)
        if fill <= EPS:
            continue
        shares += fill
        proceeds += fill * price
        remaining -= fill
    return shares, proceeds


def _apply_buy_to_live_position(position: dict, filled_size: float, avg_price: float) -> dict:
    position["size"] = float(position.get("size") or 0.0) + filled_size
    position["cost_basis"] = float(position.get("cost_basis") or 0.0) + (filled_size * avg_price)
    return position


def _apply_sell_to_live_position(position: dict, filled_size: float, avg_price: float) -> dict:
    size = float(position.get("size") or 0.0)
    cost_basis = float(position.get("cost_basis") or 0.0)
    realized_pnl = float(position.get("realized_pnl") or 0.0)

    if size <= EPS or filled_size <= EPS:
        position["size"] = max(0.0, size)
        position["cost_basis"] = max(0.0, cost_basis)
        position["realized_pnl"] = realized_pnl
        return position

    avg_entry = cost_basis / size
    shares_to_close = min(size, filled_size)
    realized_pnl += shares_to_close * (avg_price - avg_entry)
    size -= shares_to_close
    cost_basis -= shares_to_close * avg_entry

    if size <= 0.0001:
        size = 0.0
        cost_basis = 0.0

    position["size"] = size
    position["cost_basis"] = cost_basis
    position["realized_pnl"] = realized_pnl
    return position


async def on_transaction(trade: TradeData, args: argparse.Namespace, risk_mgr: RiskManager, monitor: "TradeMonitor", executor: TradeExecutor):
    side, target_size, _, _ = normalize_target_trade(trade)
    token_id = trade.token_id
    source_wallet = trade.wallet.lower()
    audit_ref = _make_audit_ref(trade)

    orderbook = await asyncio.to_thread(fetch_orderbook, token_id)
    if not orderbook:
        with db.transaction(db_path=args.db) as conn:
            db.insert_live_trade(
                conn, token_id=token_id, source_wallet=source_wallet, side=side,
                requested_size=0.0, filled_size=0.0, avg_price=0.0, notional_usd=0.0,
                status="REJECTED", risk_flags=["ORDERBOOK_UNAVAILABLE", executor.mode],
                audit_ref=audit_ref, execution_mode=executor.mode,
                error_message="Orderbook unavailable", tx_hash=trade.transaction_hash,
            )
        return

    market_meta = await asyncio.to_thread(fetch_market_metadata, token_id)
    if market_meta and market_meta.get("question"):
        log.info("Live candidate", audit_ref=audit_ref, token_id=token_id, question=market_meta["question"], side=side)

    source_before = 0.0
    with db.transaction(db_path=args.db) as conn:
        source_pos = db.get_live_source_position(conn, source_wallet, token_id)
        source_before = float(source_pos.get("size") or 0.0)

    try:
        if side == "BUY":
            planned_notional = float(args.trade_usd)
            with db.transaction(db_path=args.db) as conn:
                allowed, risk_flags, reason = risk_mgr.evaluate_buy(conn, token_id, planned_notional)
                if not allowed:
                    db.insert_live_risk_event(conn, "WARNING", "TRADE_REJECTED", reason or "risk rejection", {
                        "token_id": token_id, "wallet": source_wallet, "audit_ref": audit_ref, "flags": risk_flags,
                    })
                    db.insert_live_trade(conn, token_id, source_wallet, side, 0.0, 0.0, 0.0, planned_notional,
                                         "REJECTED", risk_flags + [executor.mode], audit_ref,
                                         tx_hash=trade.transaction_hash, execution_mode=executor.mode, error_message=reason)
                    db.upsert_live_source_position(conn, source_wallet, token_id, source_before + target_size)
                    return

            result = await executor.execute_buy(token_id, planned_notional, orderbook)

            with db.transaction(db_path=args.db) as conn:
                db.insert_live_trade(conn, token_id, source_wallet, side, result.requested_size, result.filled_size,
                                     result.avg_price, result.notional_usd, result.status,
                                     result.risk_flags + [executor.mode], audit_ref,
                                     tx_hash=result.exchange_tx_hash or trade.transaction_hash,
                                     exchange_order_id=result.exchange_order_id,
                                     execution_mode=executor.mode,
                                     error_message=result.error_message)

                copied = db.get_live_wallet_position(conn, source_wallet, token_id)
                copied = _apply_buy_to_live_position(copied, result.filled_size, result.avg_price)
                db.upsert_live_wallet_position(conn, source_wallet, token_id, copied["size"], copied["cost_basis"], copied["realized_pnl"])
                db.upsert_live_source_position(conn, source_wallet, token_id, source_before + target_size)

        elif side == "SELL":
            with db.transaction(db_path=args.db) as conn:
                copied = db.get_live_wallet_position(conn, source_wallet, token_id)
                copied_size_before = float(copied.get("size") or 0.0)
                if source_before <= EPS:
                    fraction = 0.0
                else:
                    fraction = min(1.0, float(target_size) / source_before)
                desired_sell_size = copied_size_before * fraction

                if desired_sell_size <= EPS:
                    reason = "no copied inventory or no source inventory for equal-weighted sell"
                    db.insert_live_trade(conn, token_id, source_wallet, side, 0.0, 0.0, 0.0, 0.0,
                                         "REJECTED", ["EQUAL_WEIGHTED_SELL", executor.mode], audit_ref,
                                         tx_hash=trade.transaction_hash, execution_mode=executor.mode, error_message=reason)
                    db.upsert_live_source_position(conn, source_wallet, token_id, max(0.0, source_before - target_size))
                    return

            result = await executor.execute_sell(token_id, desired_sell_size, orderbook)

            with db.transaction(db_path=args.db) as conn:
                db.insert_live_trade(conn, token_id, source_wallet, side, result.requested_size, result.filled_size,
                                     result.avg_price, result.notional_usd, result.status,
                                     result.risk_flags + [executor.mode], audit_ref,
                                     tx_hash=result.exchange_tx_hash or trade.transaction_hash,
                                     exchange_order_id=result.exchange_order_id,
                                     execution_mode=executor.mode,
                                     error_message=result.error_message)

                copied = db.get_live_wallet_position(conn, source_wallet, token_id)
                copied = _apply_sell_to_live_position(copied, result.filled_size, result.avg_price)
                db.upsert_live_wallet_position(conn, source_wallet, token_id, copied["size"], copied["cost_basis"], copied["realized_pnl"])
                db.upsert_live_source_position(conn, source_wallet, token_id, max(0.0, source_before - target_size))
        else:
            return

    except Exception as exc:
        with db.transaction(db_path=args.db) as conn:
            db.insert_live_risk_event(conn, "ERROR", "ORDER_SUBMIT_FAILED", f"Order submission failed: {exc}", {
                "token_id": token_id, "wallet": source_wallet, "audit_ref": audit_ref, "mode": executor.mode,
            })
            db.insert_live_trade(conn, token_id, source_wallet, side, 0.0, 0.0, 0.0, 0.0, "REJECTED",
                                 ["SUBMIT_FAILED", executor.mode], audit_ref,
                                 tx_hash=trade.transaction_hash, execution_mode=executor.mode, error_message=str(exc))

    if args.max_trades > 0:
        args.max_trades -= 1
        if args.max_trades <= 0:
            monitor.stop()


async def main():
    parser = argparse.ArgumentParser(description="Standalone live trading runner with risk/audit controls")
    parser.add_argument("--wallets", type=str, default="", help="Comma-separated tracked wallets")
    parser.add_argument("--category", type=str, default="CRYPTO", help="Leaderboard category if wallets not provided")
    parser.add_argument("--limit", type=int, default=10, help="Wallet count per category")
    parser.add_argument("--db", type=str, default=None, help="SQLite DB path")
    parser.add_argument("--trade-usd", type=float, default=25.0, help="Max USD to attempt per copied BUY trade")
    parser.add_argument("--starting-equity-usd", type=float, default=1000.0)
    parser.add_argument("--per-trade-usd-cap", type=float, default=50.0)
    parser.add_argument("--max-open-notional-usd", type=float, default=500.0)
    parser.add_argument("--max-daily-loss-usd", type=float, default=100.0)
    parser.add_argument("--max-open-positions", type=int, default=15)
    parser.add_argument("--min-cash-buffer-usd", type=float, default=150.0)
    parser.add_argument("--allow-live-orders", action="store_true", help="Enable real Polymarket CLOB order submission")
    parser.add_argument("--clob-private-key", type=str, default="", help="EVM private key for CLOB signing")
    parser.add_argument("--clob-funder", type=str, default="", help="Optional funder address for CLOB")
    parser.add_argument("--clob-host", type=str, default="https://clob.polymarket.com", help="CLOB host URL")
    parser.add_argument("--clob-chain-id", type=int, default=137, help="Chain id used for signing")
    parser.add_argument("--clob-signature-type", type=int, default=1, help="CLOB signature type")
    parser.add_argument("--max-trades", type=int, default=0, help="Stop after N observed candidates")
    args = parser.parse_args()

    db.init_db(args.db)
    executor = build_executor(args)

    with db.transaction(db_path=args.db) as conn:
        wallets: list[str] = []
        if args.wallets.strip():
            wallets = [w.strip().lower() for w in args.wallets.split(",") if w.strip()]
        else:
            for category in [c.strip().lower() for c in args.category.split(",") if c.strip()]:
                for wd in fetch_top_wallets(category, "MONTH", "PNL", args.limit):
                    wallets.append(wd["address"].lower())
        wallets = sorted(set(wallets))
        for wallet in wallets:
            db.upsert_wallet(conn, wallet, source="live-trading")

        db.insert_live_risk_event(conn, "INFO", "ENGINE_START", "live trading engine started", {
            "started_at": datetime.utcnow().isoformat(),
            "wallet_count": len(wallets),
            "trade_usd": args.trade_usd,
            "allow_live_orders": bool(args.allow_live_orders),
            "execution_mode": executor.mode,
            "sell_mode": "equal_weighted_by_source_position_fraction",
        })

    if not wallets:
        log.warning("No wallets configured for live trading")
        return

    from src.monitor import TradeMonitor

    risk_mgr = RiskManager(
        RiskConfig(
            per_trade_usd_cap=float(args.per_trade_usd_cap),
            max_open_notional_usd=float(args.max_open_notional_usd),
            max_daily_loss_usd=float(args.max_daily_loss_usd),
            max_open_positions=int(args.max_open_positions),
            min_cash_buffer_usd=float(args.min_cash_buffer_usd),
        ),
        starting_equity_usd=float(args.starting_equity_usd),
    )

    monitor = TradeMonitor()
    monitor.on("transaction", partial(on_transaction, args=args, risk_mgr=risk_mgr, monitor=monitor, executor=executor))
    log.warning("Live trading engine started", mode=executor.mode)
    await monitor.start(wallets)


if __name__ == "__main__":
    asyncio.run(main())
