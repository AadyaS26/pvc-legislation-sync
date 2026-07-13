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
import asyncio
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

API_KEY = os.environ.get("CONGRESS_API_KEY", "")
BASE_URL = "https://api.congress.gov/v3"
CACHE_PATH = Path("bills_cache.json")
CACHE_TTL_SECONDS = 60 * 60 * 24  # refresh once a day

# Condition keywords to search for, pulled from the real Autoimmune Association
# disease list. Widening this list is the honest way to search more broadly —
# note that most rare conditions will simply return zero real bills, because
# most rare conditions genuinely have no federal legislation naming them.
# That's not a bug to fix; it's the real state of things.
CONDITION_KEYWORDS = [
    'Achalasia',
    'Acute disseminated encephalomyelitis',
    "Addison's disease",
    "Adult Still's disease",
    'Agammaglobulinemia',
    'Alopecia areata',
    'Amyloidosis',
    'Ankylosing spondylitis',
    'Anti-GBM/anti-TBM nephritis',
    'Antiphospholipid syndrome',
    'Antisynthetase syndrome',
    'Atopic dermatitis',
    'Autoimmune angioedema',
    'Autoimmune dysautonomia',
    'Autoimmune encephalomyelitis',
    'Autoimmune enteropathy',
    'Autoimmune hepatitis',
    'Autoimmune inner ear disease',
    'Autoimmune lymphoproliferative syndrome',
    'Autoimmune myocarditis',
    'Autoimmune oophoritis',
    'Autoimmune orchitis',
    'Autoimmune pancreatitis',
    'Autoimmune progesterone dermatitis',
    'Autoimmune retinopathy',
    'Autoimmune urticaria',
    'Axonal and neuronal neuropathy',
    'Baló disease',
    "Behçet's disease",
    'Benign mucosal pemphigoid',
    'Bullous pemphigoid',
    'Castleman disease',
    'Celiac disease',
    'Chagas disease',
    'Chronic inflammatory demyelinating polyneuropathy',
    'Chronic recurrent multifocal osteomyelitis',
    'Churg-Strauss syndrome',
    'Cicatricial pemphigoid',
    "Cogan's syndrome",
    'Cold agglutinin disease',
    'Congenital heart block',
    'Coxsackie myocarditis',
    'CREST syndrome',
    "Crohn's disease",
    'Dermatitis herpetiformis',
    'Dermatomyositis',
    "Devic's disease",
    'Discoid lupus',
    "Dressler's syndrome",
    'Endometriosis',
    'Eosinophilic esophagitis',
    'Eosinophilic fasciitis',
    'Erythema nodosum',
    'Essential mixed cryoglobulinemia',
    'Evans syndrome',
    'Fibromyalgia',
    'Fibrosing alveolitis',
    'Giant cell arteritis',
    'Giant cell myocarditis',
    'Glomerulonephritis',
    "Goodpasture's syndrome",
    'Granulomatosis with polyangiitis',
    "Graves' disease",
    'Guillain-Barré syndrome',
    "Hashimoto's encephalopathy",
    "Hashimoto's thyroiditis",
    'Hemolytic anemia',
    'Henoch-Schönlein purpura',
    'Herpes gestationis',
    'Hidradenitis suppurativa',
    'Hypogammaglobulinemia',
    'IgA nephropathy',
    'IgG4-related disease',
    'Immune thrombocytopenic purpura',
    'Inclusion body myositis',
    'Interstitial cystitis',
    'Juvenile arthritis',
    'Juvenile myositis',
    'Kawasaki disease',
    'Lambert-Eaton myasthenic syndrome',
    'Leukocytoclastic vasculitis',
    'Lichen planus',
    'Lichen sclerosus',
    'Linear IgA disease',
    'Lupus',
    "Ménière's disease",
    'Microscopic polyangiitis',
    'Miller-Fisher syndrome',
    'Mixed connective tissue disease',
    "Mooren's ulcer",
    'Multifocal motor neuropathy',
    'Multiple sclerosis',
    'Myasthenia gravis',
    'Narcolepsy',
    'Neutropenia',
    'Ocular cicatricial pemphigoid',
    'Optic neuritis',
    'Palindromic rheumatism',
    'Paraneoplastic cerebellar degeneration',
    'Paroxysmal nocturnal hemoglobinuria',
    'Parry-Romberg syndrome',
    'Pars planitis',
    'Parsonage-Turner syndrome',
    'Pemphigus',
    'Peripheral neuropathy',
    'Pernicious anemia',
    'POEMS syndrome',
    'Polyarteritis nodosa',
    'Polyglandular autoimmune syndrome',
    'Polymyalgia rheumatica',
    'Polymyositis',
    'Primary biliary cholangitis',
    'Primary sclerosing cholangitis',
    'Psoriasis',
    'Psoriatic arthritis',
    'Pure red cell aplasia',
    'Pyoderma gangrenosum',
    "Raynaud's phenomenon",
    'Reactive arthritis',
    'Reflex sympathetic dystrophy',
    'Relapsing polychondritis',
    'Restless legs syndrome',
    'Retroperitoneal fibrosis',
    'Rheumatic fever',
    'Rheumatoid arthritis',
    'Sarcoidosis',
    'Schmidt syndrome',
    'Scleritis',
    'Scleroderma',
    "Sjögren's disease",
    'Stiff person syndrome',
    "Susac's syndrome",
    'Sympathetic ophthalmia',
    "Takayasu's arteritis",
    'Temporal arteritis',
    'Tolosa-Hunt syndrome',
    'Transverse myelitis',
    'Type 1 diabetes',
    'Ulcerative colitis',
    'Undifferentiated connective tissue disease',
    'Uveitis',
    'Vasculitis',
    'Vitiligo',
    'Vogt-Koyanagi-Harada disease',
    'gluten-free labeling',
    'step therapy',
    'medical foods',
    'orphan disease',
    'rare disease',
    'chronic illness insurance',
    'biologics access',
    'autoimmune disease',
]

app = FastAPI(title="PVC Legislation Sync")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your actual frontend domain in production
    allow_methods=["GET"],
)


async def fetch_bills_for_keyword(client: httpx.AsyncClient, keyword: str, semaphore: asyncio.Semaphore, limit: int = 60) -> list[dict]:
    """
    Pull bills matching a keyword from the Congress.gov API, paginating
    until we've collected `limit` results or run out of results.

    Restricted to the 119th Congress (2025-2026) so we don't pull in
    decades of unrelated old bills. Uses a semaphore so we're not firing
    100+ requests at once — that's what caused the free-tier instance to
    exceed its 512MB memory limit and crash-loop earlier.
    """
    results = []
    offset = 0
    page_size = 100  # smaller than the API's 250 max, to keep memory per-request down

    async with semaphore:
        while len(results) < limit:
            resp = await client.get(
                f"{BASE_URL}/bill/119",
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
                title = b.get("title", "")
                # Extra relevance filter: require the actual keyword phrase (or its
                # core words) to appear in the title, since the API's own matching
                # is looser than a real phrase search.
                keyword_words = [w.lower() for w in keyword.split() if len(w) > 3]
                if not any(w in title.lower() for w in keyword_words):
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
                break  # last page

    return results[:limit]


async def refresh_cache():
    """Query every condition keyword and merge into one deduplicated cache file.

    Uses a semaphore capped at 5 concurrent requests. With ~150 keywords in the
    list, running them ALL at once (as an earlier version did) is what caused
    the free-tier instance to exceed its 512MB memory limit and crash-loop —
    each concurrent request holds its own response payload in memory at the
    same time. Capping concurrency keeps memory bounded no matter how long
    the keyword list gets, at the cost of the refresh taking a bit longer.
    """
    all_bills = {}
    semaphore = asyncio.Semaphore(5)
    async with httpx.AsyncClient(timeout=30) as client:
        tasks = [fetch_bills_for_keyword(client, keyword, semaphore, limit=30) for keyword in CONDITION_KEYWORDS]
        results_per_keyword = await asyncio.gather(*tasks, return_exceptions=True)

    for bills in results_per_keyword:
        if isinstance(bills, Exception):
            continue  # one keyword failing shouldn't take down the whole refresh
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


@app.get("/")
async def root():
    return {
        "service": "PVC Legislation Sync",
        "status": "running",
        "try": "/bills — real bill data from Congress.gov",
        "also": "/refresh — force a re-sync (POST)",
    }


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
