"""SQLite persistence layer for the live paper trading simulator."""
import sqlite3
import json
import time
from pathlib import Path
from typing import Optional

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


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return a configured connection for regular reads and writes."""
    conn = sqlite3.connect(db_path or str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


from contextlib import contextmanager

@contextmanager
def transaction(conn: Optional[sqlite3.Connection] = None, db_path: Optional[str] = None):
    """Context manager for a database transaction. 
    If a connection is provided, it uses it and DOES NOT close it (for nested use).
    If no connection is provided, it opens one, manages it, and closes it.
    """
    should_close = False
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    
    try:
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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS wallets (
        address         TEXT PRIMARY KEY,
        alias           TEXT,           -- pseudonym / username from leaderboard
        source          TEXT,           -- 'leaderboard' or 'manual'
        leaderboard_pnl REAL DEFAULT 0,
        leaderboard_vol REAL DEFAULT 0,
        added_at        REAL,           -- epoch
        tracking_enabled INTEGER DEFAULT 1,
        enabled_at      REAL,
        disabled_at     REAL
    );

    CREATE TABLE IF NOT EXISTS markets (
        token_id        TEXT PRIMARY KEY,
        condition_id    TEXT,
        question        TEXT,
        outcomes        TEXT,           -- JSON array e.g. '["Yes","No"]'
        outcome_idx     INTEGER,        -- which index this token represents
        slug            TEXT,
        category        TEXT,           -- e.g. 'Weather', 'Politics', 'Crypto'
        group_item_title TEXT,          -- granular series/instrument bucket from Gamma
        tags            TEXT,           -- JSON array of tag strings
        resolved        INTEGER DEFAULT 0,
        winning_outcome INTEGER,        -- 0 or 1
        payout_value    REAL,           -- 1.0 or 0.0 for this token
        last_resolution_check REAL,     -- epoch when Gamma was last queried
        next_resolution_check REAL,     -- epoch when Gamma should be queried next
        resolution_check_failures INTEGER DEFAULT 0,
        resolved_at     REAL,           -- epoch
        first_seen      REAL            -- epoch
    );

    CREATE TABLE IF NOT EXISTS target_trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet          TEXT NOT NULL,
        token_id        TEXT NOT NULL,
        tx_hash         TEXT,
        block_number    INTEGER,
        side            TEXT NOT NULL,   -- 'BUY' or 'SELL'
        size            REAL NOT NULL,   -- shares
        price           REAL NOT NULL,
        cost_usd        REAL NOT NULL,
        onchain_ts      REAL NOT NULL,   -- block timestamp epoch
        detected_ts     REAL NOT NULL,   -- our ws detection epoch
        created_at      REAL NOT NULL,
        FOREIGN KEY (wallet) REFERENCES wallets(address),
        FOREIGN KEY (token_id) REFERENCES markets(token_id)
    );

    CREATE TABLE IF NOT EXISTS paper_trades (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        target_trade_id INTEGER NOT NULL, -- links to the target_trade that triggered this
        token_id        TEXT NOT NULL,
        side            TEXT NOT NULL,
        size            REAL NOT NULL,    -- shares filled
        avg_price       REAL NOT NULL,
        cost_usd        REAL NOT NULL,
        slippage        REAL NOT NULL,    -- price slippage vs target
        orderbook_latency_ms REAL,
        detection_delay_ms   REAL,
        execution_delay_ms   REAL,
        total_delay_ms       REAL,
        no_fill_reason  TEXT,            -- NULL if filled; reason string if not
        created_at      REAL NOT NULL,
        FOREIGN KEY (target_trade_id) REFERENCES target_trades(id),
        FOREIGN KEY (token_id) REFERENCES markets(token_id)
    );

    CREATE TABLE IF NOT EXISTS orderbook_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        target_trade_id INTEGER NOT NULL,
        token_id        TEXT NOT NULL,
        side            TEXT NOT NULL,   -- 'BUY' or 'SELL'
        bids_json       TEXT NOT NULL,   -- full bids list as JSON
        asks_json       TEXT NOT NULL,   -- full asks list as JSON
        best_bid        REAL,            -- top-of-book bid price (NULL if empty)
        best_ask        REAL,            -- top-of-book ask price (NULL if empty)
        total_bid_liquidity_usd REAL,    -- sum(price*size) across all bids
        total_ask_liquidity_usd REAL,    -- sum(price*size) across all asks
        captured_at     REAL NOT NULL,   -- epoch when snapshot was taken
        FOREIGN KEY (target_trade_id) REFERENCES target_trades(id)
    );

    CREATE TABLE IF NOT EXISTS positions (
        token_id        TEXT PRIMARY KEY,
        size            REAL DEFAULT 0,
        cost_basis      REAL DEFAULT 0,
        realized_pnl    REAL DEFAULT 0,
        updated_at      REAL,
        FOREIGN KEY (token_id) REFERENCES markets(token_id)
    );

    CREATE TABLE IF NOT EXISTS wallet_positions (
        wallet          TEXT NOT NULL,
        token_id        TEXT NOT NULL,
        size            REAL DEFAULT 0,
        cost_basis      REAL DEFAULT 0,
        realized_pnl    REAL DEFAULT 0,
        updated_at      REAL,
        PRIMARY KEY (wallet, token_id),
        FOREIGN KEY (wallet) REFERENCES wallets(address),
        FOREIGN KEY (token_id) REFERENCES markets(token_id)
    );

    CREATE TABLE IF NOT EXISTS run_state (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

    -- Indexes for common queries
    CREATE INDEX IF NOT EXISTS idx_target_wallet    ON target_trades(wallet);
    CREATE INDEX IF NOT EXISTS idx_target_token     ON target_trades(token_id);
    CREATE INDEX IF NOT EXISTS idx_paper_token      ON paper_trades(token_id);
    CREATE INDEX IF NOT EXISTS idx_paper_target     ON paper_trades(target_trade_id);
    CREATE INDEX IF NOT EXISTS idx_market_resolved  ON markets(resolved);
    """)

    conn.commit()

    # ── Safe migrations for existing databases ────────────────────
    # Add columns / tables introduced after initial release.
    _migrate(conn)

    conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply incremental schema changes without destroying existing data."""
    paper_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(paper_trades)").fetchall()
    }
    if "no_fill_reason" not in paper_cols:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN no_fill_reason TEXT")
        conn.commit()
    if "requested_size" not in paper_cols:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN requested_size REAL")
        conn.commit()
    if "source_position_fraction" not in paper_cols:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN source_position_fraction REAL")
        conn.commit()
    if "source_wallet_position_before" not in paper_cols:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN source_wallet_position_before REAL")
        conn.commit()
    if "position_mismatch_reason" not in paper_cols:
        conn.execute("ALTER TABLE paper_trades ADD COLUMN position_mismatch_reason TEXT")
        conn.commit()

    market_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(markets)").fetchall()
    }
    if "category" not in market_cols:
        conn.execute("ALTER TABLE markets ADD COLUMN category TEXT")
        conn.commit()
    if "tags" not in market_cols:
        conn.execute("ALTER TABLE markets ADD COLUMN tags TEXT")
        conn.commit()
    if "last_resolution_check" not in market_cols:
        conn.execute("ALTER TABLE markets ADD COLUMN last_resolution_check REAL")
        conn.commit()
    if "next_resolution_check" not in market_cols:
        conn.execute("ALTER TABLE markets ADD COLUMN next_resolution_check REAL")
        conn.commit()
    if "resolution_check_failures" not in market_cols:
        conn.execute("ALTER TABLE markets ADD COLUMN resolution_check_failures INTEGER DEFAULT 0")
        conn.commit()
    if "group_item_title" not in market_cols:
        conn.execute("ALTER TABLE markets ADD COLUMN group_item_title TEXT")
        conn.commit()

    wallet_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(wallets)").fetchall()
    }
    if "tracking_enabled" not in wallet_cols:
        conn.execute("ALTER TABLE wallets ADD COLUMN tracking_enabled INTEGER DEFAULT 1")
        conn.commit()
    if "enabled_at" not in wallet_cols:
        conn.execute("ALTER TABLE wallets ADD COLUMN enabled_at REAL")
        conn.commit()
    if "disabled_at" not in wallet_cols:
        conn.execute("ALTER TABLE wallets ADD COLUMN disabled_at REAL")
        conn.commit()

    conn.execute(
        """
        UPDATE wallets
        SET enabled_at = COALESCE(enabled_at, added_at, ?)
        WHERE tracking_enabled = 1
        """,
        (time.time(),),
    )
    conn.commit()

    existing_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "orderbook_snapshots" not in existing_tables:
        conn.executescript("""
        CREATE TABLE orderbook_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            target_trade_id INTEGER NOT NULL,
            token_id        TEXT NOT NULL,
            side            TEXT NOT NULL,
            bids_json       TEXT NOT NULL,
            asks_json       TEXT NOT NULL,
            best_bid        REAL,
            best_ask        REAL,
            total_bid_liquidity_usd REAL,
            total_ask_liquidity_usd REAL,
            captured_at     REAL NOT NULL,
            FOREIGN KEY (target_trade_id) REFERENCES target_trades(id)
        );
        CREATE INDEX IF NOT EXISTS idx_ob_target ON orderbook_snapshots(target_trade_id);
        CREATE INDEX IF NOT EXISTS idx_ob_token  ON orderbook_snapshots(token_id);
        """)
        conn.commit()

    if "wallet_positions" not in existing_tables:
        conn.executescript("""
        CREATE TABLE wallet_positions (
            wallet          TEXT NOT NULL,
            token_id        TEXT NOT NULL,
            size            REAL DEFAULT 0,
            cost_basis      REAL DEFAULT 0,
            realized_pnl    REAL DEFAULT 0,
            updated_at      REAL,
            PRIMARY KEY (wallet, token_id),
            FOREIGN KEY (wallet) REFERENCES wallets(address),
            FOREIGN KEY (token_id) REFERENCES markets(token_id)
        );
        """)
        conn.commit()

    _backfill_wallet_positions(conn)

    for statement in INDEX_STATEMENTS:
        conn.execute(statement)
    conn.commit()


def _backfill_wallet_positions(conn: sqlite3.Connection) -> None:
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


def _recompute_all_aggregate_positions(conn: sqlite3.Connection) -> None:
    token_ids = [
        row["token_id"]
        for row in conn.execute("SELECT DISTINCT token_id FROM wallet_positions").fetchall()
    ]
    conn.execute("DELETE FROM positions")
    for token_id in token_ids:
        recompute_aggregate_position(conn, token_id)
    conn.commit()



# ── Wallet helpers ────────────────────────────────────────────────

def upsert_wallet(conn: sqlite3.Connection, address: str, alias: str = "",
                  source: str = "manual", pnl: float = 0, vol: float = 0) -> None:
    now = time.time()
    conn.execute("""
        INSERT INTO wallets (address, alias, source, leaderboard_pnl, leaderboard_vol, added_at, tracking_enabled, enabled_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(address) DO UPDATE SET
            alias = CASE WHEN excluded.alias != '' THEN excluded.alias ELSE wallets.alias END,
            source = excluded.source,
            leaderboard_pnl = excluded.leaderboard_pnl,
            leaderboard_vol = excluded.leaderboard_vol
    """, (address.lower(), alias, source, pnl, vol, now, now))
    conn.execute(
        """
        UPDATE wallets
        SET enabled_at = COALESCE(enabled_at, ?)
        WHERE address = ? AND tracking_enabled = 1
        """,
        (now, address.lower()),
    )
    conn.commit()


def set_wallet_tracking(conn: sqlite3.Connection, address: str, enabled: bool) -> None:
    now = time.time()
    conn.execute(
        """
        UPDATE wallets
        SET tracking_enabled = ?,
            enabled_at = CASE WHEN ? = 1 THEN COALESCE(enabled_at, ?) ELSE enabled_at END,
            disabled_at = CASE WHEN ? = 0 THEN ? ELSE NULL END
        WHERE address = ?
        """,
        (1 if enabled else 0, 1 if enabled else 0, now, 1 if enabled else 0, now, address.lower()),
    )
    conn.commit()


def get_enabled_wallets(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT address FROM wallets WHERE tracking_enabled = 1 ORDER BY COALESCE(enabled_at, added_at) ASC"
    ).fetchall()
    return [row["address"] for row in rows]


# ── Market helpers ────────────────────────────────────────────────

def upsert_market(conn: sqlite3.Connection, token_id: str,
                  question: str = "", outcomes: str = "[]",
                  outcome_idx: int = 0, condition_id: str = "",
                  slug: str = "", category: str = "",
                  group_item_title: str = "", tags: str = "[]") -> None:
    conn.execute("""
        INSERT INTO markets (token_id, condition_id, question, outcomes, outcome_idx, slug, category, group_item_title, tags, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(token_id) DO UPDATE SET
            question = CASE 
                WHEN excluded.question = 'Unknown / Pending Metadata' AND markets.question != '' AND markets.question IS NOT NULL 
                THEN markets.question 
                ELSE COALESCE(NULLIF(excluded.question, ''), markets.question)
            END,
            outcomes = COALESCE(NULLIF(excluded.outcomes, '[]'), markets.outcomes),
            outcome_idx = excluded.outcome_idx,
            condition_id = COALESCE(NULLIF(excluded.condition_id, ''), markets.condition_id),
            category = COALESCE(NULLIF(excluded.category, ''), markets.category),
            group_item_title = COALESCE(NULLIF(excluded.group_item_title, ''), markets.group_item_title),
            tags = COALESCE(NULLIF(excluded.tags, '[]'), markets.tags)
    """, (token_id, condition_id, question, outcomes, outcome_idx, slug, category, group_item_title, tags, time.time()))
    conn.commit()


def mark_resolved(conn: sqlite3.Connection, token_id: str,
                  winning_outcome: int, payout_value: float) -> None:
    conn.execute("""
        UPDATE markets
        SET resolved = 1,
            winning_outcome = ?,
            payout_value = ?,
            resolved_at = ?,
            last_resolution_check = NULL,
            next_resolution_check = NULL,
            resolution_check_failures = 0
        WHERE token_id = ?
    """, (winning_outcome, payout_value, time.time(), token_id))
    conn.commit()


# ── Trade insert helpers ──────────────────────────────────────────

def insert_target_trade(conn: sqlite3.Connection, wallet: str, token_id: str,
                        tx_hash: str, block_number: int, side: str,
                        size: float, price: float, cost_usd: float,
                        onchain_ts: float, detected_ts: float) -> int:
    cur = conn.execute("""
        INSERT INTO target_trades (wallet, token_id, tx_hash, block_number, side, size, price, cost_usd, onchain_ts, detected_ts, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (wallet.lower(), token_id, tx_hash, block_number, side, size, price,
          cost_usd, onchain_ts, detected_ts, time.time()))
    conn.commit()
    return cur.lastrowid


def insert_paper_trade(conn: sqlite3.Connection, target_trade_id: int,
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (target_trade_id, token_id, side, size, avg_price, cost_usd, slippage,
          orderbook_latency_ms, detection_delay_ms, execution_delay_ms,
          total_delay_ms, no_fill_reason, requested_size, source_position_fraction,
          source_wallet_position_before, position_mismatch_reason, time.time()))
    conn.commit()
    return cur.lastrowid


def insert_orderbook_snapshot(conn: sqlite3.Connection, target_trade_id: int,
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    return cur.lastrowid


# ── Position helpers ──────────────────────────────────────────────

def get_position(conn: sqlite3.Connection, token_id: str) -> dict:
    row = conn.execute("SELECT * FROM positions WHERE token_id = ?", (token_id,)).fetchone()
    if row:
        return dict(row)
    return {"token_id": token_id, "size": 0.0, "cost_basis": 0.0, "realized_pnl": 0.0}


def upsert_position(conn: sqlite3.Connection, token_id: str,
                     size: float, cost_basis: float, realized_pnl: float) -> None:
    conn.execute("""
        INSERT INTO positions (token_id, size, cost_basis, realized_pnl, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(token_id) DO UPDATE SET
            size = excluded.size,
            cost_basis = excluded.cost_basis,
            realized_pnl = excluded.realized_pnl,
            updated_at = excluded.updated_at
    """, (token_id, size, cost_basis, realized_pnl, time.time()))
    conn.commit()


def get_wallet_position(conn: sqlite3.Connection, wallet: str, token_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM wallet_positions WHERE wallet = ? AND token_id = ?",
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


def upsert_wallet_position(conn: sqlite3.Connection, wallet: str, token_id: str,
                           size: float, cost_basis: float, realized_pnl: float) -> None:
    conn.execute("""
        INSERT INTO wallet_positions (wallet, token_id, size, cost_basis, realized_pnl, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(wallet, token_id) DO UPDATE SET
            size = excluded.size,
            cost_basis = excluded.cost_basis,
            realized_pnl = excluded.realized_pnl,
            updated_at = excluded.updated_at
    """, (wallet.lower(), token_id, size, cost_basis, realized_pnl, time.time()))
    conn.commit()


def recompute_aggregate_position(conn: sqlite3.Connection, token_id: str) -> None:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(size), 0) AS size,
               COALESCE(SUM(cost_basis), 0) AS cost_basis,
               COALESCE(SUM(realized_pnl), 0) AS realized_pnl
        FROM wallet_positions
        WHERE token_id = ?
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


def get_target_wallet_open_size_before_trade(conn: sqlite3.Connection, wallet: str,
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
        WHERE wallet = ? AND token_id = ? AND id < ?
        """,
        (wallet.lower(), token_id, target_trade_id),
    ).fetchone()
    return max(0.0, float(row["open_size"] or 0.0))


def settle_wallet_positions_for_token(conn: sqlite3.Connection, token_id: str, payout_value: float) -> None:
    rows = conn.execute(
        "SELECT * FROM wallet_positions WHERE token_id = ? AND size > 0.0001",
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

def get_state(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM run_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("""
        INSERT INTO run_state (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()
