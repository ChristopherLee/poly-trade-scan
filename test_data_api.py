import urllib.request
import json
import ssl

ssl_context = ssl._create_unverified_context()
url = "https://data-api.polymarket.com/trades?user=0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11&limit=5"
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    response = urllib.request.urlopen(req, context=ssl_context)
    data = json.loads(response.read())
    print("Trades for user length:", len(data))
    for d in data[:2]:
        print(d)
except Exception as e:
    print("Error user:", e)

url2 = "https://data-api.polymarket.com/trades?proxyWallet=0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11&limit=5"
try:
    req = urllib.request.Request(url2, headers={'User-Agent': 'Mozilla/5.0'})
    response = urllib.request.urlopen(req, context=ssl_context)
    data = json.loads(response.read())
    print("Trades for proxyWallet length:", len(data))
    for d in data[:2]:
        print(d)
except Exception as e:
    print("Error proxyWallet:", e)
    
url3 = "https://data-api.polymarket.com/trades?maker=0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11&limit=5"
try:
    req = urllib.request.Request(url3, headers={'User-Agent': 'Mozilla/5.0'})
    response = urllib.request.urlopen(req, context=ssl_context)
    data = json.loads(response.read())
    print("Trades for maker length:", len(data))
    for d in data[:2]:
        print(d)
except Exception as e:
    print("Error maker:", e)
