"""
PVC Bill Fetcher — GitHub Actions version
-------------------------------------------
Fetches real bill data from the official Congress.gov API and writes it to
bills.json in this repo. Meant to be run on a schedule by GitHub Actions
(see .github/workflows/update-bills.yml), NOT as a live server.

Why this instead of a live backend (Render, etc.):
- No server to crash, spin down, or run out of memory under traffic
- Updates itself automatically on schedule — no hand-editing needed
- The frontend just fetches a plain JSON file, which is fast and can't
  time out or 500 the way a live API call can

DESIGN NOTE — accuracy over size
----------------------------------
Bills only count as a match if the FULL condition name appears in the
title. This is deliberately strict: an earlier "any word matches" version
let totally unrelated bills through (a sickle-cell bill matched on the
word "disease"). Most rare condition names never appear verbatim in a
bill title — most keywords below will return zero results, and that's
correct, not broken.
"""

import os
import sys
import json
import time
import httpx

API_KEY = os.environ.get("CONGRESS_API_KEY", "").strip()
BASE_URL = "https://api.congress.gov/v3"
OUTPUT_PATH = "bills.json"

CONDITION_KEYWORDS = [
    "celiac disease",
    "gluten-free labeling",
    "autoimmune disease",
    "lupus",
    "rheumatoid arthritis",
    "multiple sclerosis",
    "psoriasis",
    "psoriatic arthritis",
    "Crohn's disease",
    "ulcerative colitis",
    "inflammatory bowel disease",
    "type 1 diabetes",
    "juvenile diabetes",
    "Sjogren's",
    "Hashimoto's",
    "Graves' disease",
    "scleroderma",
    "vasculitis",
    "myasthenia gravis",
    "vitiligo",
    "alopecia areata",
    "ankylosing spondylitis",
    "Guillain-Barre",
    "sarcoidosis",
    "polymyalgia rheumatica",
    "Addison's disease",
    "autoimmune hepatitis",
    "primary biliary cholangitis",
    "narcolepsy",
    "fibromyalgia",
    "hidradenitis suppurativa",
    "eosinophilic esophagitis",
    "IgA nephropathy",
    "pemphigus",
    "polymyositis",
    "dermatomyositis",
    "Behcet's disease",
    "Raynaud's",
    "step therapy",
    "medical foods",
]


def fetch_bills_for_keyword(client: httpx.Client, keyword: str, limit: int = 50) -> list[dict]:
    """Pull bills matching a keyword, paginating, restricted to the 119th Congress."""
    results = []
    offset = 0
    page_size = 100

    while len(results) < limit:
        try:
            resp = client.get(
                f"{BASE_URL}/bill/119",
                params={"api_key": API_KEY, "query": keyword, "limit": page_size, "offset": offset, "format": "json"},
            )
        except httpx.TimeoutException:
            # A single slow request from Congress.gov shouldn't kill the whole
            # run — just skip this keyword and let the rest continue. This is
            # what caused the crash on a real run: one request timed out and
            # there was no error handling around the network call.
            print(f"  [{keyword}] Timed out, skipping this keyword")
            break
        except httpx.RequestError as e:
            print(f"  [{keyword}] Request failed ({e}), skipping this keyword")
            break

        if resp.status_code != 200:
            print(f"  [{keyword}] API error {resp.status_code}, skipping")
            break

        data = resp.json()
        bills = data.get("bills", [])
        if not bills:
            break

        for b in bills:
            title = b.get("title", "")
            # Full-phrase match only — see module docstring for why.
            if keyword.lower() not in title.lower():
                continue

            results.append(
                {
                    "number": f"{b.get('type', '')}{b.get('number', '')}",
                    "title": title,
                    "congress": b.get("congress"),
                    "latestActionText": (b.get("latestAction") or {}).get("text", ""),
                    "latestActionDate": (b.get("latestAction") or {}).get("actionDate", ""),
                    "url": b.get("url", ""),
                    "matched_keyword": keyword,
                }
            )

        offset += page_size
        if len(bills) < page_size:
            break

    return results[:limit]


def main():
    if not API_KEY:
        print("ERROR: CONGRESS_API_KEY environment variable is not set.")
        sys.exit(1)

    all_bills = {}
    with httpx.Client(timeout=60) as client:
        for keyword in CONDITION_KEYWORDS:
            print(f"Searching: {keyword}")
            bills = fetch_bills_for_keyword(client, keyword)
            for b in bills:
                all_bills[b["number"]] = b
            print(f"  -> {len(bills)} real matches")
            time.sleep(0.5)  # be polite to the API, no need to hammer it

    sorted_bills = sorted(all_bills.values(), key=lambda b: b.get("latestActionDate") or "", reverse=True)

    payload = {
        "updated_at": time.time(),
        "updated_at_readable": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "count": len(sorted_bills),
        "bills": sorted_bills,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"\nDone. {len(sorted_bills)} real bills written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
