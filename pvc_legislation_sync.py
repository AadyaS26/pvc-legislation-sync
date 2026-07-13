"""
PVC Legislation Sync Service
-----------------------------
Pulls real bill data from the official Congress.gov API (api.congress.gov)
and stores it for the legislation database frontend to read.

This is the legitimate way to track bills "at scale" — Congress.gov's own
website (congress.gov) blocks scraping via robots.txt, but they publish an
official API specifically so people don't have to scrape it.

SETUP
-----
1. Get a free API key: https://api.congress.gov/sign-up/
2. pip install fastapi uvicorn httpx --break-system-packages
3. export CONGRESS_API_KEY="your_key_here"
4. uvicorn pvc_legislation_sync:app --reload

WHAT THIS DOES
--------------
- Queries the Congress.gov API for bills matching your condition keywords
  (celiac, autoimmune, lupus, psoriasis, etc.) across the current Congress.
- Handles pagination so you can pull hundreds or thousands of results,
  not just the first page.
- Caches results to disk so the frontend can read them without hitting
  the API on every page load (the API is rate-limited).
- Exposes a simple /bills endpoint your React legislation page can call
  instead of using the hardcoded BILLS array.

This is a starting point, not a finished production service — you'll want
to add real error handling, a proper database instead of a JSON file, and
probably a scheduled job (cron, or a simple while-loop with sleep) to
refresh the cache daily rather than on every request.
"""

import os
import json
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

API_KEY = os.environ.get("CONGRESS_API_KEY", "")
BASE_URL = "https://api.congress.gov/v3"
CACHE_PATH = Path("bills_cache.json")
CACHE_TTL_SECONDS = 60 * 60 * 24  # refresh once a day

# Condition keywords to search for. Expand this list freely — this is
# where "covering more conditions" actually happens, for real, without
# anyone hand-typing bill numbers.
CONDITION_KEYWORDS = [
    "celiac disease",
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
    "Sjogren's",
    "Hashimoto's",
    "Graves' disease",
    "scleroderma",
    "vasculitis",
    "myasthenia gravis",
    "vitiligo",
    "alopecia areata",
    "gluten-free labeling",
]

app = FastAPI(title="PVC Legislation Sync")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your actual frontend domain in production
    allow_methods=["GET"],
)


async def fetch_bills_for_keyword(client: httpx.AsyncClient, keyword: str, limit: int = 250) -> list[dict]:
    """
    Pull bills matching a keyword from the Congress.gov API, paginating
    until we've collected `limit` results or run out of results.
    """
    results = []
    offset = 0
    page_size = 250  # API max per request

    while len(results) < limit:
        resp = await client.get(
            f"{BASE_URL}/bill",
            params={
                "api_key": API_KEY,
                "query": keyword,
                "limit": page_size,
                "offset": offset,
                "format": "json",
            },
        )
        if resp.status_code != 200:
            break

        data = resp.json()
        bills = data.get("bills", [])
        if not bills:
            break

        for b in bills:
            results.append(
                {
                    "number": f"{b.get('type', '')}{b.get('number', '')}",
                    "title": b.get("title", ""),
                    "congress": b.get("congress"),
                    "latestActionText": (b.get("latestAction") or {}).get("text", ""),
                    "latestActionDate": (b.get("latestAction") or {}).get("actionDate", ""),
                    "url": b.get("url", ""),
                    "matched_keyword": keyword,
                }
            )

        offset += page_size
        if len(bills) < page_size:
            break  # last page

    return results[:limit]


async def refresh_cache():
    """Query every condition keyword and merge into one deduplicated cache file."""
    all_bills = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for keyword in CONDITION_KEYWORDS:
            bills = await fetch_bills_for_keyword(client, keyword)
            for b in bills:
                # dedupe on bill number since one bill can match multiple keywords
                all_bills[b["number"]] = b

    payload = {"updated_at": time.time(), "count": len(all_bills), "bills": list(all_bills.values())}
    CACHE_PATH.write_text(json.dumps(payload, indent=2))
    return payload


def load_cache() -> Optional[dict]:
    if not CACHE_PATH.exists():
        return None
    data = json.loads(CACHE_PATH.read_text())
    if time.time() - data.get("updated_at", 0) > CACHE_TTL_SECONDS:
        return None  # stale, caller should refresh
    return data


@app.get("/bills")
async def get_bills(condition: Optional[str] = Query(None), limit: int = 100):
    """
    Frontend calls this instead of reading a hardcoded array.
    Example: GET /bills?condition=celiac&limit=50
    """
    cache = load_cache()
    if cache is None:
        cache = await refresh_cache()

    bills = cache["bills"]
    if condition:
        bills = [b for b in bills if condition.lower() in b["matched_keyword"].lower()]

    return {"count": len(bills), "updated_at": cache["updated_at"], "bills": bills[:limit]}


@app.post("/refresh")
async def force_refresh():
    """Manually trigger a re-sync from Congress.gov, e.g. from a daily cron job."""
    cache = await refresh_cache()
    return {"status": "ok", "count": cache["count"]}
