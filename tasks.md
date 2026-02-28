# ◈ Poly Trade Scanner: Roadmap & Tasks

This file tracks planned features and technical debt to improve the paper trading strategy and monitoring.

## 🟢 High Priority (Strategy & Accuracy)
- [ ] **Advanced Fill Logic (Price Impact)**: Currently, orders fill at top-of-book if liquidity exists. Update to "walk the book" by calculating the volume-weighted average price (VWAP) for larger trades that exceed the first price level.
- [ ] **Follower Profitability Analysis**: Add a table/chart comparing "Paper PnL vs. Target Wallet PnL" per wallet. Identify which traders are "copyable" in practice (low slippage) vs. those who are too fast to follow (high slippage).
- [ ] **Partial Fills**: Implement logic to allow trades to partially fill if liquidity is insufficient for the full $100, rather than a binary "Fill vs No-Fill."

## 🟡 Medium Priority (UI & UX)
- [ ] **Real-time Order Book Depth Chart**: Inside the "View Book" modal, add a visual depth chart (area chart) showing the "Walls" of liquidity.
- [ ] **Slippage Heatmap**: A chart showing Slippage vs. Order Size to determine the "Liquidity Capacity" of various market categories.
- [ ] **Manual Position Management**: Add an "Emergency Close" button to the dashboard to manually resolve paper positions if the Oracle is delayed.
- [ ] **Notification Webhooks**: Integrate Discord/Telegram alerts for trade execution, slippage warnings, and daily PnL summaries.

## 🔵 Low Priority (Architecture & Scale)
- [ ] **Historical Backtester**: Build a script to pipe historical Polymarket transaction CSVs through the `live_paper_trade.py` matching logic to test strategies over months of data.
- [ ] **Multi-Wallet Grouping**: Ability to tag wallets (e.g., "Whales," "Weather Experts," "Insiders") and see aggregated PnL by group.
- [ ] **Export to CSV**: Add an "Export All Trades" button for external analysis in Excel/Python.

## ✅ Completed Tasks
- [x] Market Category persistence and UI filtering.
- [x] PnL breakdown by category.
- [x] Order Book Snapshots (DB + UI Modal).
- [x] "No-Fill" reason logging for debugging.
- [x] Multi-category leaderboard support.
