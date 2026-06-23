"""
Mine reference lists from ALL reprogramming review papers in PubMed.

For every review paper matching reprogramming terms, uses the PubMed elink
API (pubmed_pubmed_refs) to retrieve its cited references, then collects
the union of all cited PMIDs not yet in our master or papers tables.

Output CSV is formatted to be fed directly into extract_recipes.py --input.

Usage:
    python3 mine_review_references.py --preview-only
    python3 mine_review_references.py --output outputs/review_ref_candidates_YYYYMMDD.csv
    python3 mine_review_references.py --output outputs/review_ref_candidates_YYYYMMDD.csv \\
        --checkpoint outputs/review_ref_checkpoint.json

Checkpointing: elink progress is saved after each review so the run can be
interrupted and resumed without re-fetching.
"""
import argparse, json, os, time
from datetime import date
import pandas as pd
from Bio import Entrez

Entrez.email = "xuanyichen888@gmail.com"

REVIEW_QUERY = (
    '("cell reprogramming"[Title/Abstract] OR '
    '"induced pluripotent"[Title/Abstract] OR '
    '"direct reprogramming"[Title/Abstract] OR '
    '"lineage reprogramming"[Title/Abstract] OR '
    '"cell fate conversion"[Title/Abstract] OR '
    '"transdifferentiation"[Title/Abstract]) '
    'AND Review[Publication Type]'
)

MASTER_PATH = "recipes_master_v2.csv"
PAPERS_PATH = "papers.csv"


def search_all_reviews(max_results=10000):
    handle = Entrez.esearch(db="pubmed", term=REVIEW_QUERY, retmax=max_results)
    rec = Entrez.read(handle)
    handle.close()
    return list(rec["IdList"])


def fetch_refs_via_elink(pmid):
    """Return list of PMIDs cited by this paper (empty if not in PMC)."""
    try:
        handle = Entrez.elink(dbfrom="pubmed", db="pubmed", id=str(pmid),
                               linkname="pubmed_pubmed_refs")
        rec = Entrez.read(handle)
        handle.close()
        refs = []
        for linkset in rec:
            for linksetdb in linkset.get("LinkSetDb", []):
                if linksetdb["LinkName"] == "pubmed_pubmed_refs":
                    refs.extend(l["Id"] for l in linksetdb["Link"])
        return refs
    except Exception as e:
        return []


def fetch_abstracts_batch(pmids, batch_size=200):
    """Fetch title + abstract for a list of PMIDs via efetch XML."""
    rows = []
    for i in range(0, len(pmids), batch_size):
        chunk = pmids[i:i + batch_size]
        try:
            handle = Entrez.efetch(db="pubmed", id=",".join(chunk),
                                    rettype="medline", retmode="text")
            from Bio import Medline
            records = list(Medline.parse(handle))
            handle.close()
            for r in records:
                rows.append({
                    "pmid":     r.get("PMID", ""),
                    "title":    r.get("TI", ""),
                    "abstract": r.get("AB", ""),
                    "year":     (r.get("DP", "") or "")[:4],
                    "journal":  r.get("TA", ""),
                    "authors":  "; ".join((r.get("AU") or [])[:3]),
                })
        except Exception as e:
            print(f"  efetch batch error: {e}")
        time.sleep(0.34)
    return rows


def load_existing_pmids():
    pmids = set()
    for path in [MASTER_PATH, PAPERS_PATH]:
        if os.path.exists(path):
            df = pd.read_csv(path, dtype=str, usecols=["pmid"]).fillna("")
            pmids.update(df["pmid"].str.strip().tolist())
    return pmids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=f"outputs/review_ref_candidates_{date.today().strftime('%Y%m%d')}.csv")
    ap.add_argument("--checkpoint", default="outputs/review_ref_checkpoint.json")
    ap.add_argument("--preview-only", action="store_true",
                    help="Count reviews and estimate scope only, no elink calls.")
    ap.add_argument("--max-reviews", type=int, default=10000,
                    help="Cap on number of reviews to process (default: all).")
    args = ap.parse_args()

    # --- Step 1: find all review PMIDs ---
    print("Step 1: 搜索所有综述论文...")
    review_pmids = search_all_reviews(max_results=args.max_reviews)
    print(f"  找到 {len(review_pmids)} 篇综述")

    if args.preview_only:
        existing = load_existing_pmids()
        print(f"  主库 + papers 已收录: {len(existing)} 篇")
        print("Preview-only 模式，不执行 elink。")
        return

    # --- Step 2: load checkpoint ---
    ckpt_path = args.checkpoint
    os.makedirs(os.path.dirname(ckpt_path) or ".", exist_ok=True)
    if os.path.exists(ckpt_path):
        with open(ckpt_path) as f:
            ckpt = json.load(f)
        done_reviews = set(ckpt["done_reviews"])
        collected_refs = set(ckpt["collected_refs"])
        print(f"  续跑 checkpoint: 已处理 {len(done_reviews)} 篇，已收集引用 PMID {len(collected_refs)} 个")
    else:
        done_reviews = set()
        collected_refs = set()

    # --- Step 3: elink for each review ---
    existing = load_existing_pmids()
    todo = [p for p in review_pmids if p not in done_reviews]
    print(f"\nStep 2: elink 抓引用（共 {len(todo)} 篇待处理，已完成 {len(done_reviews)} 篇）")

    for n, pmid in enumerate(todo, 1):
        refs = fetch_refs_via_elink(pmid)
        collected_refs.update(refs)
        done_reviews.add(pmid)
        time.sleep(0.34)

        if n % 50 == 0 or n == len(todo):
            new_so_far = collected_refs - existing
            print(f"  [{n}/{len(todo)}] 累计引用 PMID: {len(collected_refs)} | 新增候选: {len(new_so_far)}")
            with open(ckpt_path, "w") as f:
                json.dump({"done_reviews": list(done_reviews),
                           "collected_refs": list(collected_refs)}, f)

    # --- Step 4: compute new PMIDs ---
    new_pmids = list(collected_refs - existing)
    print(f"\nStep 3: 新候选 PMID（不在主库/papers）: {len(new_pmids)}")

    if not new_pmids:
        print("没有新 PMID，退出。")
        return

    # --- Step 5: fetch abstracts for new PMIDs ---
    print(f"Step 4: 抓 {len(new_pmids)} 个新 PMID 的标题和摘要...")
    rows = fetch_abstracts_batch(new_pmids)
    df = pd.DataFrame(rows)

    # keep only those with an abstract (otherwise can't extract)
    df = df[df["abstract"].str.strip().ne("")]
    print(f"  有摘要的: {len(df)} 条")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    df.to_csv(args.output, index=False, encoding="utf-8")
    print(f"\n写出 {len(df)} 行 -> {args.output}")
    print("下一步: python3 extract_recipes.py --input", args.output)


if __name__ == "__main__":
    main()
