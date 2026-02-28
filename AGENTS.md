# Agent Guide: Poly Trade Scan 🕵️‍♂️📈

Welcome, Agent. This document provides the essential context, architectural mapping, and technical "gotchas" for working on the Poly Trade Scan project. Use this to orient yourself before making changes.

## 🎯 Project Objective
Poly Trade Scan is a **Real-Time Paper Trading Simulator** for Polymarket. It monitors high-performance wallets (whales/top traders) on the Polygon blockchain and simulates "copy-trading" them. 
Key differentiator: It doesn't assume flat slippage; it fetches the **actual L2 orderbook** at the moment of trade detection to simulate realistic fills based on liquidity.

---

## 🏗️ System Architecture

1.  **Monitor (`src/monitor.py`)**: Connects to Polygon RPC/WebSockets to listen for transaction logs.
2.  **Decoder (`src/core/`)**: Parses raw EVM logs into structured `TradeData` (Token ID, Side, Size).
3.  **Simulator (`live_paper_trade.py`)**: 
    - Triggered by the Monitor.
    - Fetches Market Metadata (Gamma API).
    - Fetches L2 Orderbook (CLOB API).
    - Simulates "walking the book" to find the average fill price for a fixed USD size.
    - Persists results to SQLite.
4.  **Database (`src/db.py` / `paper_trades.db`)**: Central storage for wallets, markets, trades, and positions.
5.  **Dashboard (`dashboard.py` + `dashboard/`)**: A Python-based API server + Vanilla JS frontend for visualizing performance, latency, and slippage.

---

## 📂 Key Files & Directories

- `live_paper_trade.py`: The "brain" of the live simulation. Entry point for the bot.
- `dashboard.py`: API endpoints for the UI.
- `src/db.py`: The SQLite persistence layer. All schema migrations happen here in `init_db`.
- `src/api/polymarket.py`: WebSocket client for Polymarket-specific events (like resolutions).
- `src/core/block_processor.py`: Low-level block listener logic.

---

## 📡 External APIs (Polymarket)

| Purpose | Base URL | Key Tip |
| :--- | :--- | :--- |
| **Market Metadata** | `gamma-api.polymarket.com` | **CRITICAL**: Use `clob_token_ids` (snake_case) for filtering, NOT `clobTokenId`. |
| **Orderbook L2** | `clob.polymarket.com` | Fetch `bids` and `asks` for a specific `token_id`. |
| **Leaderboards** | `data-api.polymarket.com` | Used to discover top wallets to follow automatically. |

---

## 💾 Database Schema Highlights

- `wallets`: Traders being followed.
- `markets`: Metadata for specific outcome tokens (Question, Category, Outcomes).
- `target_trades`: On-chain trades made by tracked wallets.
- `paper_trades`: Our simulated execution (price, slippage, latency).
- `orderbook_snapshots`: Raw book state at the time of execution (for debugging).
- `positions`: Aggregated cost basis and PnL per market.

---

## 🛠️ Setup & Operations

### Run the Simulator
```powershell
# Follow top 20 Crypto and Weather traders with $100 per trade
python live_paper_trade.py --size 100 --category "CRYPTO,WEATHER" --limit 20
```

### Run the Dashboard
```powershell
python dashboard.py
# Access at http://localhost:8050
```

---

## 💡 Agent Tips & "Gotchas"

1.  **API Naming Discrepancies**: Polymarket APIs are inconsistent. Gamma API uses `clob_token_ids` in query params but `clobTokenIds` in the response JSON. Always verify fields with a test script before mass-applying.
2.  **Database Locking**: The dashboard and the live simulator both access `paper_trades.db`. Always use `PRAGMA journal_mode=WAL` and set a `timeout` (e.g., 30s) in `sqlite3.connect` to prevent `database is locked` errors during concurrent writes.
3.  **Token IDs vs. Condition IDs**: A single "Market" (Condition) has multiple "Tokens" (e.g., Yes token and No token). Everything in this system is indexed by `token_id`.
4.  **Simulation Logic**: "Walking the book" means iterating through `asks` for a BUY and `bids` for a SELL. If liquidity is insufficient, the trade is recorded as a "No Fill".

---

## 🔮 Future Roadmap
- [ ] **Instant Outcome Resolution**: Listening to resolution events via WS to update PnL immediately.
- [ ] **Category-based Analytics**: Better grouping of PnL by market type (Politics vs. Sports).
- [ ] **Slippage Optimization**: Analyzing if delaying entries after a whale trade improves fill price.

---

*This guide is maintained by the Antigravity AI suite. Update it whenever you uncover new structural truths.*
