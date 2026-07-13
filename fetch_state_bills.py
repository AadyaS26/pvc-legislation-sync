"""
PVC State Bill Fetcher — LegiScan version
--------------------------------------------
Fetches real STATE legislation (all 50 states + DC) from the official
LegiScan API and writes it to state_bills.json in this repo. This is the
state-level counterpart to fetch_bills.py, which only covers federal
Congress bills.

Get a free API key: https://legiscan.com/legiscan
Free tier: 30,000 queries/month — this script uses ~1 query per keyword
per run (thanks to state=ALL searching every state at once), so a daily
run costs well under 100 queries/month. Very cheap.

DESIGN NOTE — same accuracy-over-size rule as the federal script
--------------------------------------------------------------------
Only counts a match if the full condition phrase appears in the bill
title. LegiScan's own search relevance is looser than that, so this filter
is what keeps the results honest instead of full of unrelated bills.
"""

import os
import sys
import json
import time
import httpx

API_KEY = os.environ.get("LEGISCAN_API_KEY", "").strip()
BASE_URL = "https://api.legiscan.com/"
OUTPUT_PATH = "state_bills.json"

# Same keyword list as the federal fetcher, so state and federal results
# stay consistent with each other.
CONDITION_KEYWORDS = [
    "celiac disease",
    "gluten-free",
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
    "sarcoidosis",
    "Addison's disease",
    "autoimmune hepatitis",
    "narcolepsy",
    "fibromyalgia",
    "hidradenitis suppurativa",
    "step therapy",
    "medical foods",
    "biomarker testing",
    "copay accumulator",
]


def search_keyword(client: httpx.Client, keyword: str) -> list[dict]:
    """Search every state at once for a keyword, using LegiScan's getSearch."""
    resp = client.get(
        BASE_URL,
        params={"key": API_KEY, "op": "getSearch", "state": "ALL", "query": keyword},
    )
    if resp.status_code != 200:
        print(f"  [{keyword}] API error {resp.status_code}")
        return []

    data = resp.json()
    if data.get("status") != "OK":
        print(f"  [{keyword}] LegiScan status: {data.get('status')} — {data.get('alert', {}).get('message', '')}")
        return []

    results = data.get("searchresult", {})
    bills = []
    for key, item in results.items():
        if key == "summary":
            continue  # metadata block, not a real bill
        title = item.get("title", "") or item.get("bill_number", "")
        # Full-phrase match only, same rule as the federal fetcher.
        if keyword.lower() not in title.lower():
            continue

        bills.append(
            {
                "number": item.get("bill_number", ""),
                "title": title,
                "state": item.get("state", ""),
                "relevance": item.get("relevance"),
                "lastAction": item.get("last_action", ""),
                "lastActionDate": item.get("last_action_date", ""),
                "url": item.get("url", ""),
                "matched_keyword": keyword,
            }
        )
    return bills


def main():
    if not API_KEY:
        print("ERROR: LEGISCAN_API_KEY environment variable is not set.")
        sys.exit(1)

    all_bills = {}
    with httpx.Client(timeout=30) as client:
        for keyword in CONDITION_KEYWORDS:
            print(f"Searching all 50 states for: {keyword}")
            bills = search_keyword(client, keyword)
            for b in bills:
                dedupe_key = f"{b['state']}-{b['number']}"
                all_bills[dedupe_key] = b
            print(f"  -> {len(bills)} real matches")
            time.sleep(0.5)  # be polite to the API

    sorted_bills = sorted(all_bills.values(), key=lambda b: b.get("lastActionDate") or "", reverse=True)
    states_covered = sorted(set(b["state"] for b in sorted_bills if b.get("state")))

    payload = {
        "updated_at": time.time(),
        "updated_at_readable": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "count": len(sorted_bills),
        "states_covered": states_covered,
        "states_covered_count": len(states_covered),
        "bills": sorted_bills,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"\nDone. {len(sorted_bills)} real state bills across {len(states_covered)} states written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
