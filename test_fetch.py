import urllib.request
import json
import ssl

ssl_context = ssl._create_unverified_context()

try:
    url = 'https://clob.polymarket.com/data/trades?proxy_wallet=0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    response = urllib.request.urlopen(req, context=ssl_context)
    data = json.loads(response.read())
    print("Proxy wallet trades:", len(data.get('data', [])))
    for d in data.get('data', [])[:2]:
        print(d)
        
    url2 = 'https://clob.polymarket.com/data/trades?maker=0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11'
    req2 = urllib.request.Request(url2, headers={'User-Agent': 'Mozilla/5.0'})
    response2 = urllib.request.urlopen(req2, context=ssl_context)
    data2 = json.loads(response2.read())
    print("\nMaker wallet trades:", len(data2.get('data', [])))
    for d in data2.get('data', [])[:2]:
        print(d)

except Exception as e:
    print("Error:", e)
