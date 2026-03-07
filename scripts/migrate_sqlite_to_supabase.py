"""One-way migration from local sqlite DB into Supabase/Postgres.

Usage:
  SUPABASE_DB_URL=postgresql://... python scripts/migrate_sqlite_to_supabase.py --sqlite paper_trades.db
"""

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import db

TABLE_ORDER = [
    "wallets",
    "markets",
    "target_trades",
    "paper_trades",
    "orderbook_snapshots",
    "positions",
    "wallet_positions",
    "run_state",
    "live_source_positions",
    "live_wallet_positions",
    "live_risk_events",
    "live_trades",
]


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def pg_columns(conn, table: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()
    return [row["column_name"] for row in rows]


def sync_identity_sequence(conn, table: str) -> None:
    if "id" not in pg_columns(conn, table):
        return

    seq_row = conn.execute(
        "SELECT pg_get_serial_sequence(%s, 'id') AS seq_name",
        (table,),
    ).fetchone()
    seq_name = seq_row["seq_name"] if seq_row else None
    if not seq_name:
        return

    max_row = conn.execute(f"SELECT MAX(id) AS max_id FROM {table}").fetchone()
    max_id = max_row["max_id"] if max_row else None
    if max_id is None:
        conn.execute("SELECT setval(%s::regclass, 1, false)", (seq_name,))
        return

    conn.execute("SELECT setval(%s::regclass, %s, true)", (seq_name, max_id))


def copy_table_rows(src: sqlite3.Connection, dest, table: str, cols: list[str], batch_size: int = 1000) -> int:
    col_list = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    total = 0
    select_cur = src.execute(f"SELECT {col_list} FROM {table}")
    with dest._inner.cursor() as cur:
        while True:
            rows = select_cur.fetchmany(batch_size)
            if not rows:
                break
            values = [tuple(row[col] for col in cols) for row in rows]
            cur.executemany(insert_sql, values)
            total += len(values)
    return total


def migrate(sqlite_path: str) -> None:
    print(f"[1/4] Initializing Supabase schema...")
    db.init_db()

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    print("[2/4] Opening Supabase connection...")
    dest = db.get_connection()

    with db.transaction(dest):
        print("[3/4] Truncating destination tables...")
        dest.execute(
            "TRUNCATE TABLE live_trades, live_risk_events, live_wallet_positions, live_source_positions, orderbook_snapshots, paper_trades, target_trades, wallet_positions, positions, run_state, markets, wallets RESTART IDENTITY CASCADE"
        )

        print("[4/4] Copying rows...")
        for table in TABLE_ORDER:
            src_cols = sqlite_columns(src, table)
            dst_cols = pg_columns(dest, table)
            cols = [c for c in src_cols if c in dst_cols]
            if not cols:
                print(f"- {table}: skipped (no overlapping columns)")
                continue

            copied = copy_table_rows(src, dest, table, cols)
            print(f"- {table}: {copied} rows")

        for table in TABLE_ORDER:
            sync_identity_sequence(dest, table)

    src.close()
    dest.close()
    print("Migration complete.")


def main():
    parser = argparse.ArgumentParser(description="Migrate sqlite DB to Supabase/Postgres")
    parser.add_argument("--sqlite", default="paper_trades.db", help="Path to sqlite database")
    args = parser.parse_args()
    migrate(args.sqlite)


if __name__ == "__main__":
    main()
