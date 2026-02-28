import urllib.request
import json
import ssl
import time

ssl_context = ssl._create_unverified_context()

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    response = urllib.request.urlopen(req, context=ssl_context)
    return json.loads(response.read())

TARGET_USER = "0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11"
cutoff_time = time.time() - (7 * 24 * 60 * 60)

trades = []
offset = 0
limit = 100

while True:
    print(f"Fetching offset {offset}...")
    url = f"https://data-api.polymarket.com/trades?user={TARGET_USER}&limit={limit}&offset={offset}"
    try:
        data = fetch_json(url)
    except Exception as e:
        print("Error:", e)
        break
    
    if not data:
        break
        
    trades.extend(data)
    offset += len(data)
    
    # Check if the oldest trade in the batch is older than the cutoff
    # Data API seems to return trades from newest to oldest
    oldest_in_batch = min(t['timestamp'] for t in data)
    if oldest_in_batch < cutoff_time:
        break
        
    if len(data) < limit:
        break

print(f"Found {len(trades)} trades total.")
trades = [t for t in trades if t['timestamp'] >= cutoff_time]
print(f"Found {len(trades)} trades in last 7 days.")
