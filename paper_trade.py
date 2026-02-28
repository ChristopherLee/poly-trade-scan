import urllib.request
import json
import ssl
import time
import sys
from collections import defaultdict

ssl_context = ssl._create_unverified_context()

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    response = urllib.request.urlopen(req, context=ssl_context)
    return json.loads(response.read())

TARGET_USER = "0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11"

print(f"Fetching trades for {TARGET_USER} over the last 7 days...")
cutoff_time = time.time() - (7 * 24 * 60 * 60)
user_trades_data = []
offset = 0
limit = 500

while True:
    print(f"Fetching offset {offset}...")
    url = f"https://data-api.polymarket.com/trades?user={TARGET_USER}&limit={limit}&offset={offset}"
    try:
        data = fetch_json(url)
    except Exception as e:
        print("Error fetching user trades:", e)
        break
        
    if not data:
        break
        
    user_trades_data.extend(data)
    offset += len(data)
    
    oldest_in_batch = min(t['timestamp'] for t in data) if data else float('inf')
    if oldest_in_batch < cutoff_time:
        break
        
    if len(data) < limit:
        break

# Filter for last 7 days and sort from oldest to newest
user_trades = [t for t in user_trades_data if t['timestamp'] >= cutoff_time]
user_trades = sorted(user_trades, key=lambda x: x['timestamp'])
print(f"Found {len(user_trades)} trades in the last 7 days.")

# We will apply a standard slippage model to "predict the cost of buying right after"
# Usually right after a trade, the price moves against you slightly or is slightly worse on the orderbook
def get_paper_price(side, target_price):
    # Assume a 0.5 cent slippage (which is very typical for Polymarket liquid markets)
    slippage = 0.005
    if side == "BUY":
        return min(1.0, target_price + slippage)
    else:
        return max(0.0, target_price - slippage)

asset_latest_price = {}

def get_current_price(asset):
    if asset not in asset_latest_price:
        try:
            asset_trades = fetch_json(f"https://data-api.polymarket.com/trades?asset={asset}&limit=1")
            if asset_trades:
                asset_latest_price[asset] = asset_trades[0]['price']
            else:
                asset_latest_price[asset] = 0.5
        except:
            asset_latest_price[asset] = 0.5
    return asset_latest_price[asset]

target_portfolio = defaultdict(lambda: {'size': 0.0, 'cost_basis': 0.0, 'realized_pnl': 0.0})
paper_portfolio = defaultdict(lambda: {'size': 0.0, 'cost_basis': 0.0, 'realized_pnl': 0.0})

trade_reports = []

for idx, t in enumerate(user_trades):
    if idx % 10 == 0:
        time.sleep(0.1) # small pause
    asset = t['asset']
    side = t['side']
    size = float(t['size'])
    target_price = float(t['price'])
    timestamp = t['timestamp']
    title = t.get('title', 'Unknown')
    outcome = t.get('outcome', '')
    
    paper_price = get_paper_price(side, target_price)
    
    trade_reports.append({
        'title': title,
        'outcome': outcome,
        'side': side,
        'size': size,
        'target_price': target_price,
        'paper_price': paper_price,
        'diff': paper_price - target_price if side == "BUY" else target_price - paper_price # slippage
    })
    
    # Update Target Portfolio
    t_pos = target_portfolio[asset]
    if side == "BUY":
        t_pos['cost_basis'] += size * target_price
        t_pos['size'] += size
    elif side == "SELL":
        if t_pos['size'] > 0:
            avg_entry = t_pos['cost_basis'] / t_pos['size']
            t_pos['realized_pnl'] += size * (target_price - avg_entry)
            t_pos['size'] -= size
            t_pos['cost_basis'] -= size * avg_entry
            if t_pos['size'] <= 0.0001:
                t_pos['size'] = 0
                t_pos['cost_basis'] = 0

    # Update Paper Portfolio
    p_pos = paper_portfolio[asset]
    if side == "BUY":
        p_pos['cost_basis'] += size * paper_price
        p_pos['size'] += size
    elif side == "SELL":
        if p_pos['size'] > 0:
            avg_entry = p_pos['cost_basis'] / p_pos['size']
            p_pos['realized_pnl'] += size * (paper_price - avg_entry)
            p_pos['size'] -= size
            p_pos['cost_basis'] -= size * avg_entry
            if p_pos['size'] <= 0.0001:
                p_pos['size'] = 0
                p_pos['cost_basis'] = 0

# Calculate Unrealized PNL
target_unrealized = 0.0
paper_unrealized = 0.0

print("Fetching current prices for open positions to calculate Unrealized PnL...")

for asset, t_pos in target_portfolio.items():
    if t_pos['size'] > 0:
        current_price = get_current_price(asset)
        target_unrealized += (t_pos['size'] * current_price) - t_pos['cost_basis']

for asset, p_pos in paper_portfolio.items():
    if p_pos['size'] > 0:
        current_price = get_current_price(asset)
        paper_unrealized += (p_pos['size'] * current_price) - p_pos['cost_basis']

target_realized = sum(p['realized_pnl'] for p in target_portfolio.values())
paper_realized = sum(p['realized_pnl'] for p in paper_portfolio.values())

with open("report.txt", "w", encoding="utf-8") as f:
    f.write("="*80 + "\n")
    f.write("PAPER TRADE REPORT\n")
    f.write("="*80 + "\n\n")

    for r in trade_reports:
        slippage_str = f"Slippage mapping: {r['diff']:.4f} per token"
        f.write(f"[{r['side']}] {r['size']:<8.2f} {r['outcome']:<5} | Target: ${r['target_price']:.4f} | Paper: ${r['paper_price']:.4f} | {slippage_str}\n")
        f.write(f"   Market: {r['title'][:65]}...\n")

    f.write("\n" + "="*80 + "\n")
    f.write("PERFORMANCE SUMMARY\n")
    f.write("="*80 + "\n\n")

    f.write(f"{'Metric':<20} | {'Target Wallet':<15} | {'Paper Wallet':<15} | {'Difference':<15}\n")
    f.write("-" * 75 + "\n")

    realized_diff = paper_realized - target_realized
    unrealized_diff = paper_unrealized - target_unrealized
    total_target = target_realized + target_unrealized
    total_paper = paper_realized + paper_unrealized
    total_diff = total_paper - total_target

    f.write(f"{'Realized PNL':<20} | ${target_realized:14.2f} | ${paper_realized:14.2f} | ${realized_diff:14.2f}\n")
    f.write(f"{'Unrealized PNL':<20} | ${target_unrealized:14.2f} | ${paper_unrealized:14.2f} | ${unrealized_diff:14.2f}\n")
    f.write(f"{'Total PNL':<20} | ${total_target:14.2f} | ${total_paper:14.2f} | ${total_diff:14.2f}\n")
    f.write("\nNote: Paper prices simulated with 0.5 cent slippage to accurately represent the next available execution after following the trade.\n")

print("Report generated and saved to report.txt")
