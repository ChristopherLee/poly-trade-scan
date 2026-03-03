#!/usr/bin/env python
"""Fetch Polymarket wallet activity and save it to JSON."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API_URL = "https://data-api.polymarket.com/activity"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
}


def fetch_activity(wallet: str, page_size: int, max_offset: int, max_pages: int) -> list[dict]:
    rows: list[dict] = []
    pages = 0
    for offset in range(0, max_offset + 1, page_size):
        if max_pages and pages >= max_pages:
            break

        query = urllib.parse.urlencode(
            {
                "user": wallet,
                "limit": page_size,
                "offset": offset,
            }
        )
        req = urllib.request.Request(f"{API_URL}?{query}", headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as response:
            batch = json.loads(response.read().decode("utf-8"))
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected Polymarket response at offset {offset}: {batch!r}")
        rows.extend(batch)
        pages += 1
        if len(batch) < page_size:
            break
        time.sleep(0.15)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Polymarket wallet activity into a JSON file.")
    parser.add_argument("--wallet", required=True, help="Wallet address to query.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path. Defaults to assets/polymarket-activity-<wallet>.json",
    )
    parser.add_argument("--page-size", type=int, default=1000, help="API page size, max 1000.")
    parser.add_argument(
        "--max-offset",
        type=int,
        default=3000,
        help="Largest API offset to request. Polymarket currently rejects values above 3000.",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="Optional page cap (0 means no extra cap).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output or Path("assets") / f"polymarket-activity-{args.wallet.lower()}.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        rows = fetch_activity(
            wallet=args.wallet,
            page_size=min(1000, max(1, args.page_size)),
            max_offset=max(0, args.max_offset),
            max_pages=max(0, args.max_pages),
        )
    except urllib.error.HTTPError as exc:
        print(f"HTTP error {exc.code}: {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1

    output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Saved {len(rows)} activity rows to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
