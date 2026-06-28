"""
Fetch cell reprogramming patents via Lens.org and PatentsView (USPTO).

Lens.org API (preferred):
  - Free academic API, 10K results/query, 50K req/month
  - Get key at: https://www.lens.org/lens/user/subscriptions#api
  - Set env:  LENS_API_KEY=<key>

PatentsView API (USPTO fallback, no key needed):
  - US patents only, good structured data
  - Runs automatically if Lens key not set

Outputs:
  patents_raw.csv        — all fetched patents (deduped by number)
  patents_done.json      — checkpoint

Next step:
  ANTHROPIC_API_KEY=<key> python3 extract_patent_recipes.py
"""

import json, os, re, time, csv, argparse
from pathlib import Path
import requests

CHECKPOINT = Path("patents_done.json")
OUTPUT_CSV = Path("patents_raw.csv")
SLEEP      = 1.0

QUERIES = [
    "induced pluripotent stem cell OCT4 SOX2 KLF4",
    "cell reprogramming transcription factor direct conversion",
    "iPSC reprogramming Yamanaka method",
    "chemical reprogramming pluripotent small molecule",
    "fibroblast neuron direct conversion NeuroD1 ASCL1",
    "cardiomyocyte reprogramming GATA4 MEF2C TBX5",
    "hepatocyte reprogramming FOXA2 HNF4A",
    "pancreatic beta cell PDX1 NGN3 reprogramming",
    "transdifferentiation transcription factor lineage conversion",
    "T cell reprogramming transcription factor",
    "hematopoietic cell reprogramming",
    "endothelial cell reprogramming ETV2",
    "skeletal muscle MYOD1 direct reprogramming",
    "neuronal reprogramming NGN2 brain direct",
]

FIELDNAMES = [
    "patent_number", "title", "inventor", "assignee",
    "priority_date", "filing_date", "grant_date",
    "abstract", "url", "source", "query",
]


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def load_done() -> set:
    return set(json.loads(CHECKPOINT.read_text())) if CHECKPOINT.exists() else set()

def save_done(done: set):
    CHECKPOINT.write_text(json.dumps(sorted(done)))

def append_rows(rows: list[dict]):
    write_header = not OUTPUT_CSV.exists()
    with open(OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(rows)


# ── Lens.org ──────────────────────────────────────────────────────────────────

LENS_URL = "https://api.lens.org/patent/search"

def lens_search(query: str, api_key: str, offset: int = 0, size: int = 100) -> tuple[list[dict], int]:
    payload = {
        "query": {"query_string": {"query": query, "fields": ["title", "abstract"]}},
        "size": size,
        "from": offset,
        "include": ["lens_id", "title", "abstract", "date_published",
                    "priority_date", "filing_date", "grant_date",
                    "inventors", "applicants", "publication_number"],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        r = requests.post(LENS_URL, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"    Lens error: {e}")
        return [], 0

    total = data.get("total", 0)
    rows = []
    for item in data.get("data", []):
        num = item.get("publication_number") or item.get("lens_id", "")
        invs = ", ".join(
            f"{p.get('first_name','')} {p.get('last_name','')}".strip()
            for p in (item.get("inventors") or [])
        )
        assignees = ", ".join(
            a.get("name", "") for a in (item.get("applicants") or [])
        )
        rows.append({
            "patent_number": num,
            "title":         item.get("title", ""),
            "inventor":      invs,
            "assignee":      assignees,
            "priority_date": item.get("priority_date", ""),
            "filing_date":   item.get("filing_date", ""),
            "grant_date":    item.get("grant_date", ""),
            "abstract":      (item.get("abstract") or "")[:2000],
            "url":           f"https://www.lens.org/lens/patent/{item.get('lens_id','')}",
            "source":        "lens",
            "query":         query,
        })
    return rows, total


def run_lens(api_key: str, max_per_query: int = 500):
    done = load_done()
    total_new = 0

    for query in QUERIES:
        print(f"\n[Lens] {query!r}")
        offset, new_q = 0, 0

        while True:
            results, total = lens_search(query, api_key, offset=offset)
            if not results:
                break

            fresh = [r for r in results if r["patent_number"] not in done]
            if fresh:
                append_rows(fresh)
                for r in fresh:
                    done.add(r["patent_number"])
                save_done(done)
                new_q    += len(fresh)
                total_new += len(fresh)

            print(f"  offset {offset:4d}/{total} → {len(fresh)} new  (query total: {new_q})")
            offset += len(results)
            if offset >= total or offset >= max_per_query or len(fresh) == 0:
                break
            time.sleep(SLEEP)

    print(f"\nLens done. New patents: {total_new}")


# ── PatentsView (USPTO) ────────────────────────────────────────────────────────

PV_URL = "https://search.patentsview.org/api/v1/patent/"

def patentsview_search(query: str, page: int = 1, per_page: int = 100) -> tuple[list[dict], int]:
    params = {
        "q":  json.dumps({"_text_phrase": {"patent_abstract": query}}),
        "f":  json.dumps(["patent_number", "patent_title", "patent_date",
                           "patent_abstract", "inventors.inventor_name_first",
                           "inventors.inventor_name_last", "assignees.assignee_organization",
                           "applications.app_date"]),
        "o":  json.dumps({"page": page, "per_page": per_page}),
    }
    try:
        r = requests.get(PV_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"    PatentsView error: {e}")
        return [], 0

    total = data.get("total_patent_count", 0)
    rows = []
    for p in data.get("patents") or []:
        num = p.get("patent_number", "")
        invs = ", ".join(
            f"{inv.get('inventor_name_first','')} {inv.get('inventor_name_last','')}".strip()
            for inv in (p.get("inventors") or [])
        )
        assignee = ", ".join(
            a.get("assignee_organization", "") for a in (p.get("assignees") or [])
        )
        rows.append({
            "patent_number": f"US{num}",
            "title":         p.get("patent_title", ""),
            "inventor":      invs,
            "assignee":      assignee,
            "priority_date": "",
            "filing_date":   (p.get("applications") or [{}])[0].get("app_date", ""),
            "grant_date":    p.get("patent_date", ""),
            "abstract":      (p.get("patent_abstract") or "")[:2000],
            "url":           f"https://patents.google.com/patent/US{num}/en",
            "source":        "patentsview",
            "query":         query,
        })
    return rows, total


def run_patentsview(max_per_query: int = 300):
    done = load_done()
    total_new = 0

    for query in QUERIES:
        print(f"\n[PatentsView] {query!r}")
        page, new_q = 1, 0

        while True:
            results, total = patentsview_search(query, page=page)
            if not results:
                break

            fresh = [r for r in results if r["patent_number"] not in done]
            if fresh:
                append_rows(fresh)
                for r in fresh:
                    done.add(r["patent_number"])
                save_done(done)
                new_q    += len(fresh)
                total_new += len(fresh)

            offset = (page - 1) * 100 + len(results)
            print(f"  page {page}, offset {offset}/{total} → {len(fresh)} new  (query total: {new_q})")

            if offset >= total or offset >= max_per_query or len(fresh) == 0:
                break
            page += 1
            time.sleep(SLEEP)

    print(f"\nPatentsView done. New patents: {total_new}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["lens", "patentsview", "both"],
                        default="both", help="Which API to use")
    parser.add_argument("--max", type=int, default=500,
                        help="Max patents per query")
    args = parser.parse_args()

    lens_key = os.environ.get("LENS_API_KEY", "")

    if args.source in ("lens", "both"):
        if lens_key:
            run_lens(lens_key, max_per_query=args.max)
        else:
            print("LENS_API_KEY not set — skipping Lens.org")
            print("Get a free key at: https://www.lens.org/lens/user/subscriptions#api")

    if args.source in ("patentsview", "both"):
        run_patentsview(max_per_query=args.max)
