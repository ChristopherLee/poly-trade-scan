"""Postgres/Supabase persistence layer for the live paper trading simulator."""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

DB_PATH = Path(__file__).parent.parent / "paper_trades.db"

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_wallets_tracking_order ON wallets(tracking_enabled DESC, COALESCE(enabled_at, added_at) DESC, leaderboard_pnl DESC)",
    "CREATE INDEX IF NOT EXISTS idx_markets_resolved_first_seen ON markets(resolved, first_seen DESC)",
    "CREATE INDEX IF NOT EXISTS idx_markets_condition_id ON markets(condition_id)",
    "CREATE INDEX IF NOT EXISTS idx_target_wallet_created_at ON target_trades(wallet, created_at DESC, id)",
    "CREATE INDEX IF NOT EXISTS idx_target_token_created_at ON target_trades(token_id, created_at DESC, id)",
    "CREATE INDEX IF NOT EXISTS idx_paper_token_created_at ON paper_trades(token_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_created_at ON paper_trades(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ob_target ON orderbook_snapshots(target_trade_id)",
    "CREATE INDEX IF NOT EXISTS idx_ob_token ON orderbook_snapshots(token_id)",
    "CREATE INDEX IF NOT EXISTS idx_positions_updated_at ON positions(updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_wallet_positions_wallet_updated ON wallet_positions(wallet, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_wallet_positions_token_updated ON wallet_positions(token_id, updated_at DESC)",
)


def _translate_query(query: str) -> str:
    """Translate sqlite-style placeholders to Postgres placeholders."""
    return query.replace("?", "%s")


class ManagedCursor:
    """Cursor wrapper for sqlite compatibility (fetch methods + rowcount)."""

    def __init__(self, inner: psycopg.Cursor[Any]):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    @property
    def lastrowid(self) -> Optional[int]:
        return None

    def fetchone(self):
        return self._inner.fetchone()

    def fetchall(self):
        return self._inner.fetchall()


class ManagedConnection:
    """Thin wrapper that can suppress intermediate commits inside a transaction."""

    def __init__(self, inner: psycopg.Connection) -> None:
        self._inner = inner
        self._suppress_commit_depth = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def execute(self, query: str, params: Optional[tuple | list] = None) -> ManagedCursor:
        cur = self._inner.cursor(row_factory=dict_row)
        cur.execute(_translate_query(query), params or ())
        return ManagedCursor(cur)

    def executescript(self, script: str) -> None:
        for stmt in script.split(";"):
            statement = stmt.strip()
            if statement:
                self.execute(statement)

    def commit(self) -> None:
        if self._suppress_commit_depth > 0:
            return
        self._inner.commit()

    def rollback(self) -> None:
        self._inner.rollback()

    def close(self) -> None:
        self._inner.close()

    @contextmanager
    def suppress_commits(self):
        self._suppress_commit_depth += 1
        try:
            yield self
        finally:
            self._suppress_commit_depth -= 1


def get_connection(db_path: Optional[str] = None) -> ManagedConnection:
    """Return a configured connection for regular reads and writes."""
    dsn = db_path or os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "No database DSN configured. Set SUPABASE_DB_URL (preferred) or DATABASE_URL."
        )
    conn = psycopg.connect(dsn, connect_timeout=30)
    conn.execute("SET statement_timeout TO '30s'")
    return ManagedConnection(conn)


@contextmanager
def transaction(conn: Optional[ManagedConnection] = None, db_path: Optional[str] = None):
    """Context manager for a database transaction."""
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True

    try:
        with conn.suppress_commits():
            yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if should_close:
            conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    """Create all tables if they don't exist."""
    conn = get_connection(db_path)

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS wallets (
        address         TEXT PRIMARY KEY,
        alias           TEXT,
        source          TEXT,
        leaderboard_pnl DOUBLE PRECISION DEFAULT 0,
        leaderboard_vol DOUBLE PRECISION DEFAULT 0,
        added_at        DOUBLE PRECISION,
        tracking_enabled INTEGER DEFAULT 1,
        enabled_at      DOUBLE PRECISION,
        disabled_at     DOUBLE PRECISION
    );

    CREATE TABLE IF NOT EXISTS markets (
        token_id        TEXT PRIMARY KEY,
        condition_id    TEXT,
        question        TEXT,
        outcomes        TEXT,
        outcome_idx     INTEGER,
        slug            TEXT,
        category        TEXT,
        group_item_title TEXT,
        tags            TEXT,
        resolved        INTEGER DEFAULT 0,
        winning_outcome INTEGER,
        payout_value    DOUBLE PRECISION,
        last_resolution_check DOUBLE PRECISION,
        next_resolution_check DOUBLE PRECISION,
        resolution_check_failures INTEGER DEFAULT 0,
        resolved_at     DOUBLE PRECISION,
        first_seen      DOUBLE PRECISION
    );

    CREATE TABLE IF NOT EXISTS target_trades (
        id              BIGSERIAL PRIMARY KEY,
        wallet          TEXT NOT NULL,
        token_id        TEXT NOT NULL,
        tx_hash         TEXT,
        block_number    INTEGER,
        side            TEXT NOT NULL,
        size            DOUBLE PRECISION NOT NULL,
        price           DOUBLE PRECISION NOT NULL,
        cost_usd        DOUBLE PRECISION NOT NULL,
        onchain_ts      DOUBLE PRECISION NOT NULL,
        detected_ts     DOUBLE PRECISION NOT NULL,
        created_at      DOUBLE PRECISION NOT NULL,
        FOREIGN KEY (wallet) REFERENCES wallets(address),
        FOREIGN KEY (token_id) REFERENCES markets(token_id)
    );

    CREATE TABLE IF NOT EXISTS paper_trades (
        id              BIGSERIAL PRIMARY KEY,
        target_trade_id BIGINT NOT NULL,
        token_id        TEXT NOT NULL,
        side            TEXT NOT NULL,
        size            DOUBLE PRECISION NOT NULL,
        avg_price       DOUBLE PRECISION NOT NULL,
        cost_usd        DOUBLE PRECISION NOT NULL,
        slippage        DOUBLE PRECISION NOT NULL,
        orderbook_latency_ms DOUBLE PRECISION,
        detection_delay_ms   DOUBLE PRECISION,
        execution_delay_ms   DOUBLE PRECISION,
        total_delay_ms       DOUBLE PRECISION,
        no_fill_reason  TEXT,
        requested_size DOUBLE PRECISION,
        source_position_fraction DOUBLE PRECISION,
        source_wallet_position_before DOUBLE PRECISION,
        position_mismatch_reason TEXT,
        created_at      DOUBLE PRECISION NOT NULL,
        FOREIGN KEY (target_trade_id) REFERENCES target_trades(id),
        FOREIGN KEY (token_id) REFERENCES markets(token_id)
    );

    CREATE TABLE IF NOT EXISTS orderbook_snapshots (
        id              BIGSERIAL PRIMARY KEY,
        target_trade_id BIGINT NOT NULL,
        token_id        TEXT NOT NULL,
        side            TEXT NOT NULL,
        bids_json       TEXT NOT NULL,
        asks_json       TEXT NOT NULL,
        best_bid        DOUBLE PRECISION,
        best_ask        DOUBLE PRECISION,
        total_bid_liquidity_usd DOUBLE PRECISION,
        total_ask_liquidity_usd DOUBLE PRECISION,
        captured_at     DOUBLE PRECISION NOT NULL,
        FOREIGN KEY (target_trade_id) REFERENCES target_trades(id)
    );

    CREATE TABLE IF NOT EXISTS positions (
        token_id        TEXT PRIMARY KEY,
        size            DOUBLE PRECISION DEFAULT 0,
        cost_basis      DOUBLE PRECISION DEFAULT 0,
        realized_pnl    DOUBLE PRECISION DEFAULT 0,
        updated_at      DOUBLE PRECISION,
        FOREIGN KEY (token_id) REFERENCES markets(token_id)
    );

    CREATE TABLE IF NOT EXISTS wallet_positions (
        wallet          TEXT NOT NULL,
        token_id        TEXT NOT NULL,
        size            DOUBLE PRECISION DEFAULT 0,
        cost_basis      DOUBLE PRECISION DEFAULT 0,
        realized_pnl    DOUBLE PRECISION DEFAULT 0,
        updated_at      DOUBLE PRECISION,
        PRIMARY KEY (wallet, token_id),
        FOREIGN KEY (wallet) REFERENCES wallets(address),
        FOREIGN KEY (token_id) REFERENCES markets(token_id)
    );

    CREATE TABLE IF NOT EXISTS run_state (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_target_wallet    ON target_trades(wallet);
    CREATE INDEX IF NOT EXISTS idx_target_token     ON target_trades(token_id);
    CREATE INDEX IF NOT EXISTS idx_paper_token      ON paper_trades(token_id);
    CREATE INDEX IF NOT EXISTS idx_paper_target     ON paper_trades(target_trade_id);
    CREATE INDEX IF NOT EXISTS idx_market_resolved  ON markets(resolved);
    """)

    conn.commit()
    _migrate(conn)
    conn.close()


def _migrate(conn: ManagedConnection) -> None:
    """Apply incremental schema changes without destroying existing data."""
    conn.executescript("""
    ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS no_fill_reason TEXT;
    ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS requested_size DOUBLE PRECISION;
    ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS source_position_fraction DOUBLE PRECISION;
    ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS source_wallet_position_before DOUBLE PRECISION;
    ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS position_mismatch_reason TEXT;

    ALTER TABLE markets ADD COLUMN IF NOT EXISTS category TEXT;
    ALTER TABLE markets ADD COLUMN IF NOT EXISTS tags TEXT;
    ALTER TABLE markets ADD COLUMN IF NOT EXISTS last_resolution_check DOUBLE PRECISION;
    ALTER TABLE markets ADD COLUMN IF NOT EXISTS next_resolution_check DOUBLE PRECISION;
    ALTER TABLE markets ADD COLUMN IF NOT EXISTS resolution_check_failures INTEGER DEFAULT 0;
    ALTER TABLE markets ADD COLUMN IF NOT EXISTS group_item_title TEXT;

    ALTER TABLE wallets ADD COLUMN IF NOT EXISTS tracking_enabled INTEGER DEFAULT 1;
    ALTER TABLE wallets ADD COLUMN IF NOT EXISTS enabled_at DOUBLE PRECISION;
    ALTER TABLE wallets ADD COLUMN IF NOT EXISTS disabled_at DOUBLE PRECISION;

    CREATE TABLE IF NOT EXISTS orderbook_snapshots (
        id              BIGSERIAL PRIMARY KEY,
        target_trade_id BIGINT NOT NULL,
        token_id        TEXT NOT NULL,
        side            TEXT NOT NULL,
        bids_json       TEXT NOT NULL,
        asks_json       TEXT NOT NULL,
        best_bid        DOUBLE PRECISION,
        best_ask        DOUBLE PRECISION,
        total_bid_liquidity_usd DOUBLE PRECISION,
        total_ask_liquidity_usd DOUBLE PRECISION,
        captured_at     DOUBLE PRECISION NOT NULL,
        FOREIGN KEY (target_trade_id) REFERENCES target_trades(id)
    );

    CREATE TABLE IF NOT EXISTS wallet_positions (
        wallet          TEXT NOT NULL,
        token_id        TEXT NOT NULL,
        size            DOUBLE PRECISION DEFAULT 0,
        cost_basis      DOUBLE PRECISION DEFAULT 0,
        realized_pnl    DOUBLE PRECISION DEFAULT 0,
        updated_at      DOUBLE PRECISION,
        PRIMARY KEY (wallet, token_id),
        FOREIGN KEY (wallet) REFERENCES wallets(address),
        FOREIGN KEY (token_id) REFERENCES markets(token_id)
    );
    """)

    conn.execute(
        """
        UPDATE wallets
        SET enabled_at = COALESCE(enabled_at, added_at, %s)
        WHERE tracking_enabled = 1 AND enabled_at IS NULL
        """,
        (time.time(),),
    )

    _backfill_wallet_positions(conn)

    for statement in INDEX_STATEMENTS:
        conn.execute(statement)
    conn.commit()


def _backfill_wallet_positions(conn: ManagedConnection) -> None:
    """Populate wallet_positions and recompute aggregate positions when missing."""
    wallet_position_count = conn.execute(
        "SELECT COUNT(*) AS c FROM wallet_positions"
    ).fetchone()["c"]
    if wallet_position_count:
        _recompute_all_aggregate_positions(conn)
        return

    rows = conn.execute(
        """
        SELECT tt.wallet, pt.token_id, pt.side, pt.size, pt.avg_price,
               pt.created_at, pt.id
        FROM paper_trades pt
        JOIN target_trades tt ON tt.id = pt.target_trade_id
        WHERE COALESCE(pt.no_fill_reason, '') = ''
          AND COALESCE(pt.size, 0) > 0
        ORDER BY pt.created_at ASC, pt.id ASC
        """
    ).fetchall()

    for row in rows:
        wallet = row["wallet"]
        token_id = row["token_id"]
        pos = get_wallet_position(conn, wallet, token_id)
        size = float(row["size"] or 0.0)
        price = float(row["avg_price"] or 0.0)
        if str(row["side"] or "").upper() == "BUY":
            pos["size"] += size
            pos["cost_basis"] += size * price
        elif pos["size"] > 0.0001:
            avg_entry = pos["cost_basis"] / pos["size"]
            shares_to_close = min(size, pos["size"])
            pos["realized_pnl"] += shares_to_close * (price - avg_entry)
            pos["size"] -= shares_to_close
            pos["cost_basis"] -= shares_to_close * avg_entry
            if pos["size"] <= 0.0001:
                pos["size"] = 0.0
                pos["cost_basis"] = 0.0
        upsert_wallet_position(
            conn,
            wallet,
            token_id,
            pos["size"],
            pos["cost_basis"],
            pos["realized_pnl"],
        )

    _recompute_all_aggregate_positions(conn)


def _recompute_all_aggregate_positions(conn: ManagedConnection) -> None:
    token_ids = [
        row["token_id"]
        for row in conn.execute("SELECT DISTINCT token_id FROM wallet_positions").fetchall()
    ]
    conn.execute("DELETE FROM positions")
    for token_id in token_ids:
        recompute_aggregate_position(conn, token_id)
    conn.commit()


# ── Wallet helpers ────────────────────────────────────────────────

def upsert_wallet(conn: ManagedConnection, address: str, alias: str = "",
                  source: str = "manual", pnl: float = 0, vol: float = 0) -> None:
    now = time.time()
    conn.execute("""
        INSERT INTO wallets (address, alias, source, leaderboard_pnl, leaderboard_vol, added_at, tracking_enabled, enabled_at)
        VALUES (%s, %s, %s, %s, %s, %s, 1, %s)
        ON CONFLICT(address) DO UPDATE SET
            alias = CASE WHEN EXCLUDED.alias != '' THEN EXCLUDED.alias ELSE wallets.alias END,
            source = EXCLUDED.source,
            leaderboard_pnl = EXCLUDED.leaderboard_pnl,
            leaderboard_vol = EXCLUDED.leaderboard_vol
    """, (address.lower(), alias, source, pnl, vol, now, now))
    conn.execute(
        """
        UPDATE wallets
        SET enabled_at = COALESCE(enabled_at, %s)
        WHERE address = %s AND tracking_enabled = 1
        """,
        (now, address.lower()),
    )
    conn.commit()


def set_wallet_tracking(conn: ManagedConnection, address: str, enabled: bool) -> None:
    now = time.time()
    conn.execute(
        """
        UPDATE wallets
        SET tracking_enabled = %s,
            enabled_at = CASE WHEN %s = 1 THEN COALESCE(enabled_at, %s) ELSE enabled_at END,
            disabled_at = CASE WHEN %s = 0 THEN %s ELSE NULL END
        WHERE address = %s
        """,
        (1 if enabled else 0, 1 if enabled else 0, now, 1 if enabled else 0, now, address.lower()),
    )
    conn.commit()


def get_enabled_wallets(conn: ManagedConnection) -> list[str]:
    rows = conn.execute(
        "SELECT address FROM wallets WHERE tracking_enabled = 1 ORDER BY COALESCE(enabled_at, added_at) ASC"
    ).fetchall()
    return [row["address"] for row in rows]


# ── Market helpers ────────────────────────────────────────────────

def upsert_market(conn: ManagedConnection, token_id: str,
                  question: str = "", outcomes: str = "[]",
                  outcome_idx: int = 0, condition_id: str = "",
                  slug: str = "", category: str = "",
                  group_item_title: str = "", tags: str = "[]") -> None:
    conn.execute("""
        INSERT INTO markets (token_id, condition_id, question, outcomes, outcome_idx, slug, category, group_item_title, tags, first_seen)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(token_id) DO UPDATE SET
            question = CASE
                WHEN EXCLUDED.question = 'Unknown / Pending Metadata' AND markets.question != '' AND markets.question IS NOT NULL
                THEN markets.question
                ELSE COALESCE(NULLIF(EXCLUDED.question, ''), markets.question)
            END,
            outcomes = COALESCE(NULLIF(EXCLUDED.outcomes, '[]'), markets.outcomes),
            outcome_idx = EXCLUDED.outcome_idx,
            condition_id = COALESCE(NULLIF(EXCLUDED.condition_id, ''), markets.condition_id),
            category = COALESCE(NULLIF(EXCLUDED.category, ''), markets.category),
            group_item_title = COALESCE(NULLIF(EXCLUDED.group_item_title, ''), markets.group_item_title),
            tags = COALESCE(NULLIF(EXCLUDED.tags, '[]'), markets.tags)
    """, (token_id, condition_id, question, outcomes, outcome_idx, slug, category, group_item_title, tags, time.time()))
    conn.commit()


def mark_resolved(conn: ManagedConnection, token_id: str,
                  winning_outcome: int, payout_value: float) -> None:
    conn.execute("""
        UPDATE markets
        SET resolved = 1,
            winning_outcome = %s,
            payout_value = %s,
            resolved_at = %s,
            last_resolution_check = NULL,
            next_resolution_check = NULL,
            resolution_check_failures = 0
        WHERE token_id = %s
    """, (winning_outcome, payout_value, time.time(), token_id))
    conn.commit()


# ── Trade insert helpers ──────────────────────────────────────────

def insert_target_trade(conn: ManagedConnection, wallet: str, token_id: str,
                        tx_hash: str, block_number: int, side: str,
                        size: float, price: float, cost_usd: float,
                        onchain_ts: float, detected_ts: float) -> int:
    cur = conn.execute("""
        INSERT INTO target_trades (wallet, token_id, tx_hash, block_number, side, size, price, cost_usd, onchain_ts, detected_ts, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (wallet.lower(), token_id, tx_hash, block_number, side, size, price,
          cost_usd, onchain_ts, detected_ts, time.time()))
    conn.commit()
    return cur.fetchone()["id"]


def insert_paper_trade(conn: ManagedConnection, target_trade_id: int,
                       token_id: str, side: str, size: float, avg_price: float,
                       cost_usd: float, slippage: float,
                       orderbook_latency_ms: float, detection_delay_ms: float,
                       execution_delay_ms: float, total_delay_ms: float,
                       no_fill_reason: Optional[str] = None,
                       requested_size: Optional[float] = None,
                       source_position_fraction: Optional[float] = None,
                       source_wallet_position_before: Optional[float] = None,
                       position_mismatch_reason: Optional[str] = None) -> int:
    cur = conn.execute("""
        INSERT INTO paper_trades
            (target_trade_id, token_id, side, size, avg_price, cost_usd, slippage,
             orderbook_latency_ms, detection_delay_ms, execution_delay_ms, total_delay_ms,
             no_fill_reason, requested_size, source_position_fraction,
             source_wallet_position_before, position_mismatch_reason, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (target_trade_id, token_id, side, size, avg_price, cost_usd, slippage,
          orderbook_latency_ms, detection_delay_ms, execution_delay_ms,
          total_delay_ms, no_fill_reason, requested_size, source_position_fraction,
          source_wallet_position_before, position_mismatch_reason, time.time()))
    conn.commit()
    return cur.fetchone()["id"]


def insert_orderbook_snapshot(conn: ManagedConnection, target_trade_id: int,
                              token_id: str, side: str,
                              bids: list, asks: list) -> int:
    """Persist the full order book snapshot for a triggered trade."""

    def _best(levels: list, reverse: bool) -> Optional[float]:
        if not levels:
            return None
        return float(sorted(levels, key=lambda x: float(x['price']), reverse=reverse)[0]['price'])

    def _total_liquidity(levels: list) -> float:
        return sum(float(x['price']) * float(x['size']) for x in levels)

    cur = conn.execute("""
        INSERT INTO orderbook_snapshots
            (target_trade_id, token_id, side, bids_json, asks_json,
             best_bid, best_ask,
             total_bid_liquidity_usd, total_ask_liquidity_usd, captured_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        target_trade_id, token_id, side,
        json.dumps(bids), json.dumps(asks),
        _best(bids, reverse=True),
        _best(asks, reverse=False),
        _total_liquidity(bids),
        _total_liquidity(asks),
        time.time(),
    ))
    conn.commit()
    return cur.fetchone()["id"]


# ── Position helpers ──────────────────────────────────────────────

def get_position(conn: ManagedConnection, token_id: str) -> dict:
    row = conn.execute("SELECT * FROM positions WHERE token_id = %s", (token_id,)).fetchone()
    if row:
        return dict(row)
    return {"token_id": token_id, "size": 0.0, "cost_basis": 0.0, "realized_pnl": 0.0}


def upsert_position(conn: ManagedConnection, token_id: str,
                    size: float, cost_basis: float, realized_pnl: float) -> None:
    conn.execute("""
        INSERT INTO positions (token_id, size, cost_basis, realized_pnl, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(token_id) DO UPDATE SET
            size = EXCLUDED.size,
            cost_basis = EXCLUDED.cost_basis,
            realized_pnl = EXCLUDED.realized_pnl,
            updated_at = EXCLUDED.updated_at
    """, (token_id, size, cost_basis, realized_pnl, time.time()))
    conn.commit()


def get_wallet_position(conn: ManagedConnection, wallet: str, token_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM wallet_positions WHERE wallet = %s AND token_id = %s",
        (wallet.lower(), token_id),
    ).fetchone()
    if row:
        return dict(row)
    return {
        "wallet": wallet.lower(),
        "token_id": token_id,
        "size": 0.0,
        "cost_basis": 0.0,
        "realized_pnl": 0.0,
    }


def upsert_wallet_position(conn: ManagedConnection, wallet: str, token_id: str,
                           size: float, cost_basis: float, realized_pnl: float) -> None:
    conn.execute("""
        INSERT INTO wallet_positions (wallet, token_id, size, cost_basis, realized_pnl, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT(wallet, token_id) DO UPDATE SET
            size = EXCLUDED.size,
            cost_basis = EXCLUDED.cost_basis,
            realized_pnl = EXCLUDED.realized_pnl,
            updated_at = EXCLUDED.updated_at
    """, (wallet.lower(), token_id, size, cost_basis, realized_pnl, time.time()))
    conn.commit()


def recompute_aggregate_position(conn: ManagedConnection, token_id: str) -> None:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(size), 0) AS size,
               COALESCE(SUM(cost_basis), 0) AS cost_basis,
               COALESCE(SUM(realized_pnl), 0) AS realized_pnl
        FROM wallet_positions
        WHERE token_id = %s
        """,
        (token_id,),
    ).fetchone()
    upsert_position(
        conn,
        token_id,
        float(row["size"] or 0.0),
        float(row["cost_basis"] or 0.0),
        float(row["realized_pnl"] or 0.0),
    )


def get_target_wallet_open_size_before_trade(conn: ManagedConnection, wallet: str,
                                             token_id: str, target_trade_id: int) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(
            CASE
                WHEN UPPER(side) = 'BUY' THEN size
                WHEN UPPER(side) = 'SELL' THEN -size
                ELSE 0
            END
        ), 0) AS open_size
        FROM target_trades
        WHERE wallet = %s AND token_id = %s AND id < %s
        """,
        (wallet.lower(), token_id, target_trade_id),
    ).fetchone()
    return max(0.0, float(row["open_size"] or 0.0))


def settle_wallet_positions_for_token(conn: ManagedConnection, token_id: str, payout_value: float) -> None:
    rows = conn.execute(
        "SELECT * FROM wallet_positions WHERE token_id = %s AND size > 0.0001",
        (token_id,),
    ).fetchall()
    if not rows:
        pos = get_position(conn, token_id)
        if pos and float(pos["size"] or 0.0) > 0.0001:
            realized_gain = (payout_value * pos["size"]) - pos["cost_basis"]
            upsert_position(
                conn,
                token_id,
                0.0,
                0.0,
                float(pos["realized_pnl"] or 0.0) + realized_gain,
            )
        return

    for row in rows:
        pos = dict(row)
        realized_gain = (payout_value * pos["size"]) - pos["cost_basis"]
        upsert_wallet_position(
            conn,
            pos["wallet"],
            token_id,
            0.0,
            0.0,
            float(pos["realized_pnl"] or 0.0) + realized_gain,
        )
    recompute_aggregate_position(conn, token_id)


# ── Run state helpers (for restartability) ────────────────────────

def get_state(conn: ManagedConnection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM run_state WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: ManagedConnection, key: str, value: str) -> None:
    conn.execute("""
        INSERT INTO run_state (key, value) VALUES (%s, %s)
        ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value
    """, (key, value))
    conn.commit()
