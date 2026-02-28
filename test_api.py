
import urllib.request
import json
import ssl

ssl_context = ssl._create_unverified_context()

def test_api(category, time_period):
    url = f"https://data-api.polymarket.com/v1/leaderboard?category={category}&timePeriod={time_period}&orderBy=PNL&limit=1"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        response = urllib.request.urlopen(req, context=ssl_context)
        print(f"SUCCESS: {category}, {time_period} -> {response.getcode()}")
    except Exception as e:
        print(f"FAILURE: {category}, {time_period} -> {e}")

test_api("politics", "WEEK")
test_api("politics", "weekly")
test_api("culture", "WEEK")
test_api("POP_CULTURE", "WEEK")
test_api("economics", "WEEK")
test_api("BUSINESS", "WEEK")
