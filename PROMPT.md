# Live Paper Trading Simulation Task

## Objective
Build a live paper trading simulator that perfectly copies a target wallet's trades in real-time. Instead of assuming flat slippage, this simulator must ping the Polymarket Level 2 Orderbook Data API the millisecond a target trade is detected on the Polygon blockchain via WebSockets. It should then simulate a real "fill" by walking up the available liquidity in the orderbook to calculate the exact cost basis for our paper trade size.

## Context & Existing Infrastructure
1. **Repository:** `C:\Users\chris\OneDrive\Documents\projects\poly-trade-scan`
2. **Current Capabilities:** 
   - A fast Polygon WebSocket block listener is already implemented in the `poly-trade-scan` repository (`src/core/block_processor.py` and `src/core/decoder.py`).
   - The original historical paper trading backtester (`paper_trade.py`) demonstrated the strategy's profitability against the target wallet `0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11`.
3. **Data APIs:**
   - **Trade API:** `https://data-api.polymarket.com/trades?asset={TOKEN_ID}`
   - **Orderbook (Need to implement):** Polymarket Data API / CLOB API should be used to fetch the L2 orderbook (`bids` and `asks`) for a specific `asset` (Condition ID / Token ID) to calculate exact fill prices based on volume.

## Requirements
1. **Target Wallet Monitoring:** Spin up the existing WebSocket infrastructure to listen exclusively for the target wallet (`0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11`).
2. **Event Trigger:** When a trade by the target wallet is decoded from the block stream:
   - Identify the `asset` (Token ID), `side` (BUY/SELL), and `size` of the trade.
3. **Orderbook Fill Simulation:**
   - Immediately invoke the Polymarket Orderbook API for that specific asset.
   - If the target wallet bought, our paper bot should simulate a BUY by iterating through the `asks` (selling prices starting from the cheapest) and summing up the cost until our desired paper trade size (e.g., $100 or matching the whale's exact size) is fully filled.
   - If the target wallet sold, the bot should simulate a SELL by walking down the `bids` (buying prices starting from the highest).
4. **Latency Measurement:** Calculate the time delta between the block timestamp / WebSocket receipt time and the API return time to log real-world latency.
5. **Reporting:** Log the paper trade details, including the target wallet's execution price, our simulated orderbook fill price, the exact slippage incurred based on the book, and the simulated realized/unrealized PnL.
