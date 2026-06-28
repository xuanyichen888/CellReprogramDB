"""
Fetch cell reprogramming patents from Google Patents and USPTO EFTS.

Outputs:
  patents_raw.csv     — all fetched patents (deduped by patent number)
  patents_done.json   — checkpoint (already-fetched numbers)

Usage:
  python3 fetch_patents.py              # fetch all queries
  python3 fetch_patents.py --limit 50   # stop after N results per query

Next step:
  python3 extract_patent_recipes.py     # LLM extraction of recipes from patents
"""

import json, re, time, argparse
from pathlib import Path
from urllib.parse import urlencode, quote
import requests

# ── Config ────────────────────────────────────────────────────────────────────
CHECKPOINT   = Path("patents_done.json")
OUTPUT_CSV   = Path("patents_raw.csv")
PER_PAGE     = 10   # Google Patents max per page
MAX_PAGES    = 15   # pages per query  (= 150 results max per query)
SLEEP        = 1.2  # seconds between requests (be polite)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://patents.google.com/",
    "X-Requested-With": "XMLHttpRequest",
}

# Focused queries — broad enough to get coverage, narrow enough to be relevant.
# Each query is combined with "cell" to avoid purely chemical patents.
QUERIES = [
    "cell reprogramming transcription factor induced pluripotent",
    "direct cell conversion lineage reprogramming",
    "somatic cell reprogramming iPSC transcription",
    "fibroblast reprogramming neuron direct conversion",
    "transdifferentiation transcription factor",
    "cardiomyocyte reprogramming direct conversion",
    "hepatocyte reprogramming transcription factor",
    "T cell reprogramming transcription factor",
    "chemical reprogramming induced pluripotent small molecule",
    "iPSC induction OCT4 SOX2 KLF4",
    "MYOD1 muscle reprogramming direct conversion",
    "neuronal reprogramming ASCL1 NGN2",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_checkpoint() -> set:
    if CHECKPOINT.exists():
        return set(json.loads(CHECKPOINT.read_text()))
    return set()

def save_checkpoint(done: set):
    CHECKPOINT.write_text(json.dumps(sorted(done)))

def load_existing() -> list[dict]:
    if not OUTPUT_CSV.exists():
        return []
    import csv
    with open(OUTPUT_CSV, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))

def append_rows(rows: list[dict]):
    import csv
    fieldnames = [
        "patent_number", "title", "inventor", "assignee",
        "priority_date", "filing_date", "grant_date", "publication_date",
        "snippet", "url", "language", "query",
    ]
    write_header = not OUTPUT_CSV.exists()
    with open(OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(rows)

def google_patents_search(query: str, page: int) -> tuple[list[dict], int]:
    """Return (list_of_patent_dicts, total_pages)."""
    # Build the url= parameter: encoded query string
    qs = urlencode({"q": query, "num": PER_PAGE, "start": page * PER_PAGE})
    api_url = f"https://patents.google.com/xhr/query?url={quote(qs, safe='')}"
    try:
        r = requests.get(api_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"    Google Patents error (page {page}): {e}")
        return [], 0

    results_obj = data.get("results", {})
    total_pages = int(results_obj.get("total_num_pages", 0))
    clusters    = results_obj.get("cluster", [])

    patents = []
    for cluster in clusters:
        for entry in cluster.get("result", []):
            p = entry.get("patent", {})
            num = p.get("publication_number", "").strip()
            if not num:
                continue
            patents.append({
                "patent_number":    num,
                "title":            p.get("title", "").strip(),
                "inventor":         p.get("inventor", "").strip(),
                "assignee":         p.get("assignee", "").strip(),
                "priority_date":    p.get("priority_date", ""),
                "filing_date":      p.get("filing_date", ""),
                "grant_date":       p.get("grant_date", ""),
                "publication_date": p.get("publication_date", ""),
                "snippet":          p.get("snippet", "").strip(),
                "url":              f"https://patents.google.com/patent/{num}/en",
                "language":         p.get("language", ""),
                "query":            query,
            })
    return patents, total_pages


def run(limit_per_query: int | None = None):
    done = load_checkpoint()
    total_new = 0

    for query in QUERIES:
        print(f"\nQuery: {query!r}")
        new_for_query = 0

        for page in range(MAX_PAGES):
            results, total_pages = google_patents_search(query, page)
            if not results:
                break

            fresh = [r for r in results if r["patent_number"] not in done]
            if fresh:
                append_rows(fresh)
                for r in fresh:
                    done.add(r["patent_number"])
                save_checkpoint(done)
                new_for_query += len(fresh)
                total_new += len(fresh)

            print(f"  page {page+1}/{min(total_pages, MAX_PAGES)} "
                  f"→ {len(results)} results, {len(fresh)} new  "
                  f"(total new: {total_new})")

            if page + 1 >= total_pages:
                break
            if limit_per_query and new_for_query >= limit_per_query:
                print(f"  hit limit ({limit_per_query}), moving to next query")
                break
            time.sleep(SLEEP)

    print(f"\nDone. Total new patents written: {total_new}")
    print(f"Output: {OUTPUT_CSV}  (total rows: {len(load_existing())})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Max new patents per query (for testing)")
    args = parser.parse_args()
    run(limit_per_query=args.limit)
