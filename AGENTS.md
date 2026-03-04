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

Dashboard verification tips:
- After backend changes, verify the live server, not just the source tree. A direct check like `curl http://localhost:8050/api/summary` or `curl "http://localhost:8050/api/wallet_detail?wallet=0x..."` is the fastest sanity test.
- If the browser still shows old behavior after code changes, assume the dashboard process is stale before assuming the patch failed.
- For UI regressions, use Playwright against `http://localhost:8050/` so you are testing the running dashboard, including API wiring.
- For any dashboard code change, perform the live verification flow automatically before replying. Do not wait for the user to ask.
- If the live server is stale, stop the stale `dashboard.py` process, restart it, then repeat API and UI verification before replying.


### Dashboard Test Data (Recommended)
Use the commands below to get deterministic local data for UI testing:

```bash
# 1) Build a sample DB with one wallet/market/trade/position
python scripts/generate_sample_db.py

# 2) Point the dashboard at this sample data
cp assets/sample_paper_trades.db paper_trades.db
python dashboard.py
```

If you want live data and only need a single captured trade, use the trade cap:

```bash
python live_paper_trade.py --db assets/live_one_trade.db --category crypto --limit 5 --size 25 --max-trades 1
```

Notes:
- `--max-trades 1` exits automatically after the first processed trade is saved.
- If Polygon websocket access fails in the environment (e.g. HTTP 502), fall back to `scripts/generate_sample_db.py` for dashboard QA.

---

## 💡 Agent Tips & "Gotchas"

1.  **API Naming Discrepancies**: Polymarket APIs are inconsistent. Gamma API uses `clob_token_ids` in query params but `clobTokenIds` in the response JSON. Always verify fields with a test script before mass-applying.
2.  **Database Locking**: The dashboard and the live simulator both access `paper_trades.db`. Always use `PRAGMA journal_mode=WAL` and set a `timeout` (e.g., 30s) in `sqlite3.connect` to prevent `database is locked` errors during concurrent writes.
3.  **Token IDs vs. Condition IDs**: A single "Market" (Condition) has multiple "Tokens" (e.g., Yes token and No token). Everything in this system is indexed by `token_id`.
4.  **Simulation Logic**: "Walking the book" means iterating through `asks` for a BUY and `bids` for a SELL. If liquidity is insufficient, the trade is recorded as a "No Fill".
5.  **Dashboard Process Staleness**: It is easy to end up with an old `dashboard.py` process still bound to port `8050`. If a new endpoint returns `{"error":"not found"}` even though the code exists locally, check the live server first with `curl`, then inspect listeners with `netstat -ano | findstr :8050`, stop stale Python processes, and restart the dashboard.
6.  **API/UI Verification Order**: For dashboard work, test in this order: `(1)` direct API call with `curl`, `(2)` reload the page, `(3)` Playwright/browser interaction. This isolates whether the issue is backend routing, stale frontend state, or rendering.

---

## 🔮 Future Roadmap
- [ ] **Instant Outcome Resolution**: Listening to resolution events via WS to update PnL immediately.
- [ ] **Category-based Analytics**: Better grouping of PnL by market type (Politics vs. Sports).
- [ ] **Slippage Optimization**: Analyzing if delaying entries after a whale trade improves fill price.

---

*This guide is maintained by the Antigravity AI suite. Update it whenever you uncover new structural truths.*
