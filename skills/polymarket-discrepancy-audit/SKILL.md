---
name: polymarket-discrepancy-audit
description: Audit Polymarket wallet activity against the local paper trading database to find target-trade, copied-trade, and market-resolution discrepancies. Use when a user asks to verify wallet trades, compare DB records to Polymarket APIs, investigate PnL differences, or identify sizing/price/outcome mismatches.
---

# Polymarket Discrepancy Audit

Use the repo scripts instead of re-deriving the same API and SQL logic by hand.

## Workflow

1. Resolve the wallet from the DB first. Accept either alias or address.
2. Fetch activity from Polymarket or reuse a cached JSON file.
3. Run the reconciliation script against `paper_trades.db`.
4. Read the report and then inspect only the suspicious transactions or markets.

## Commands

Fetch and cache activity:

```powershell
python scripts/polymarket_wallet_activity.py --wallet 0x... --output assets\wallet-activity.json
```

Reconcile Polymarket activity with the DB:

```powershell
python scripts/polymarket_db_reconcile.py --wallet k9Q2mX4L8A7ZP3R
```

Reuse cached activity to avoid repeated API calls:

```powershell
python scripts/polymarket_db_reconcile.py --wallet k9Q2mX4L8A7ZP3R --activity-cache assets\wallet-activity.json
```

Write machine-readable output:

```powershell
python scripts/polymarket_db_reconcile.py --wallet 0x... --output-json assets\reconcile.json
```

## What The Scripts Check

- Match `target_trades` rows to Polymarket activity by `tx_hash`, `token_id`, and `side`.
- Compare DB size, price, and timestamp to Polymarket activity.
- Summarize paper-trade linkage and copied USD from `paper_trades`.
- Verify the highest-loss resolved tokens against Gamma market metadata.
- Surface the largest size-ratio mismatches first.

## Interpretation Rules

- Trust tx hash, token, side, and timestamp matches more than size until disproven.
- Treat persistent `DB size / API size` inflation as a decoder bug, not a market-behavior conclusion.
- Use the market verification section to separate bad copied execution from correct market resolution.
- If activity older than Polymarket's offset cap is missing, state that the API capped verification and distinguish verified rows from unverifiable rows.

## References

- Read [references/queries.md](./references/queries.md) when you need example commands or want to narrow the audit.
