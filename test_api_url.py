
import urllib.request
import json
import ssl

ssl_context = ssl._create_unverified_context()

def test_api(token_id):
    urls = [
        f"https://gamma-api.polymarket.com/markets?clobTokenId={token_id}",
        f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}",
    ]
    
    for url in urls:
        print(f"Testing URL: {url}")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, context=ssl_context) as resp:
                data = json.loads(resp.read())
                print(f"  Got {len(data)} results.")
                if data:
                    print(f"  First result question: {data[0].get('question')}")
                    # Check if token exists in clobTokenIds
                    clob_ids = json.loads(data[0].get("clobTokenIds", "[]"))
                    if token_id in clob_ids:
                        print(f"  TOKEN FOUND IN RESULTS!")
                        return True
        except Exception as e:
            print(f"  Error: {e}")
    return False

if __name__ == "__main__":
    # Sample token from the failed list
    token_id = "85355400561598906663413260551758601880698890840287698899046091433529370868197"
    test_api(token_id)
