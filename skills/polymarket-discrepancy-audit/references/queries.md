# Query Patterns

## Common uses

Audit one wallet alias:

```powershell
python scripts/polymarket_db_reconcile.py --wallet k9Q2mX4L8A7ZP3R
```

Audit one wallet address:

```powershell
python scripts/polymarket_db_reconcile.py --wallet 0xd0d6053c3c37e727402d84c14069780d360993aa
```

Limit API breadth while iterating:

```powershell
python scripts/polymarket_db_reconcile.py --wallet 0x... --max-pages 1 --market-check-limit 3
```

Fetch once, compare many times:

```powershell
python scripts/polymarket_wallet_activity.py --wallet 0x... --output assets\wallet.json
python scripts/polymarket_db_reconcile.py --wallet 0x... --activity-cache assets\wallet.json
```

## Report reading

- `Missing in API`: DB target trades that were not found in fetched Polymarket activity.
- `Extra API rows`: Polymarket activity rows with no matching DB target trade.
- `Size ratio median`: Fast signal for order-size vs fill-size bugs.
- `Top Market Losses`: Internal copy-trading loss concentration by condition.
- `Market Verification`: DB resolution state compared to Gamma.
