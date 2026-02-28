import asyncio
import json
import urllib.request
import ssl
from typing import Optional
ssl_context = ssl._create_unverified_context()

def fetch_json(url: str) -> Optional[dict | list]:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        response = urllib.request.urlopen(req, context=ssl_context)
        return json.loads(response.read())
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def fetch_market_metadata(token_id: str) -> Optional[dict]:
    url = f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}"
    data = fetch_json(url)
    if not data:
        return None
    for m in data:
        clob_ids = json.loads(m.get("clobTokenIds", "[]"))
        if token_id in clob_ids:
            outcome_idx = clob_ids.index(token_id)
            outcomes = m.get("outcomes", "[]")
            if isinstance(outcomes, str):
                outcomes_list = json.loads(outcomes)
            else:
                outcomes_list = outcomes
            
            raw_tags = m.get("tags", [])
            if isinstance(raw_tags, str):
                try:
                    raw_tags = json.loads(raw_tags)
                except Exception:
                    raw_tags = []
            tag_labels = [
                t.get("label", t) if isinstance(t, dict) else str(t)
                for t in raw_tags
            ]
            
            primary_category = (m.get("category") or "").strip()
            group_item_title = (m.get("groupItemTitle") or "").strip()

            return {
                "question": m.get("question", ""),
                "outcomes_json": json.dumps(outcomes_list),
                "outcome_idx": outcome_idx,
                "condition_id": m.get("conditionId", ""),
                "slug": m.get("slug", ""),
                "category": primary_category or group_item_title,
                "group_item_title": group_item_title,
                "tags": json.dumps(tag_labels),
            }
    return None

from src import db

def backfill_metadata():
    # Use our optimized DB module
    db.init_db() 
    
    # We'll fetch the IDs first, then update one by one to avoid long-held locks during network IO
    with db.transaction() as conn:
        rows = conn.execute("SELECT token_id FROM markets WHERE question = 'Unknown / Pending Metadata' LIMIT 50").fetchall()
    
    print(f"Found {len(rows)} markets with unknown metadata.")
    
    for row in rows:
        token_id = row['token_id']
        print(f"Fetching for {token_id}...")
        meta = fetch_market_metadata(token_id)
        if meta:
            print(f"  Success: {meta['question']}")
            try:
                with db.transaction() as conn:
                    db.upsert_market(
                        conn, token_id,
                        question=meta["question"],
                        outcomes=meta["outcomes_json"],
                        outcome_idx=meta["outcome_idx"],
                        condition_id=meta["condition_id"],
                        slug=meta["slug"],
                        category=meta["category"],
                        group_item_title=meta.get("group_item_title", ""),
                        tags=meta["tags"]
                    )
                print(f"  Updated DB.")
            except Exception as e:
                print(f"  Database error: {e}")
        else:
            print(f"  Failed to fetch metadata (API might still not have it).")
        
        # Sleep a bit to avoid rate limits
        import time
        time.sleep(0.5)
    
    conn.close()

if __name__ == "__main__":
    backfill_metadata()
