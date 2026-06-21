"""
Trace review-derived recipes back to the original papers cited by those reviews.

This does not modify the master table. It writes a CSV of PubMed references
from review PMIDs and marks whether each referenced PMID is already covered by
recipes_master_v2.csv or papers.csv.

Example:
  python3 build_review_reference_backcheck.py --limit-reviews 50
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd
from Bio import Entrez


Entrez.email = "xuanyichen888@gmail.com"


REPROGRAMMING_TITLE_RE = (
    r"reprogram|conversion|convert|transdifferentiat|induced pluripotent|"
    r"induced neuron|induced hepatocyte|induced cardiomyocyte|insulin-producing|"
    r"beta[- ]?cell|fibroblast|defined factor|transcription factor"
)

REVIEW_LIKE_RE = r"\breview\b|meta-analysis|perspective|overview|protocol|trends|progress|advances"


def chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def clean(value) -> str:
    return str(value or "").strip()


def load_existing(path: str) -> set[str]:
    try:
        df = pd.read_csv(path, dtype=str).fillna("")
    except FileNotFoundError:
        return set()
    if "pmid" not in df.columns:
        return set()
    return set(df["pmid"].dropna().astype(str))


def load_review_pmids(master_path: str, limit: int | None) -> pd.DataFrame:
    df = pd.read_csv(master_path, dtype=str).fillna("")
    reviews = df[df["paper_type"].astype(str).str.lower().eq("review")].copy()
    grouped = (
        reviews.groupby("pmid", as_index=False)
        .agg(
            review_title=("title", "first"),
            review_year=("year", "first"),
            review_recipe_count=("pmid", "size"),
            review_recipes=(
                "target_cell",
                lambda values: "; ".join(
                    list(dict.fromkeys(clean(v) for v in values if clean(v)))[:8]
                ),
            ),
        )
        .sort_values(["review_year", "pmid"], ascending=[False, False])
        .rename(columns={"pmid": "review_pmid"})
    )
    if limit:
        grouped = grouped.head(limit)
    return grouped


def fetch_references(pmids: list[str], batch_size: int = 50, sleep_s: float = 0.35) -> dict[str, list[str]]:
    refs_by_review: dict[str, list[str]] = {}
    for batch in chunks(pmids, batch_size):
        handle = Entrez.elink(
            dbfrom="pubmed",
            db="pubmed",
            id=",".join(batch),
            linkname="pubmed_pubmed_refs",
        )
        records = Entrez.read(handle)
        handle.close()
        for record in records:
            review_ids = [str(x) for x in record.get("IdList", [])]
            review_pmid = review_ids[0] if review_ids else ""
            refs: list[str] = []
            for linkset in record.get("LinkSetDb", []):
                for link in linkset.get("Link", []):
                    ref = clean(link.get("Id", ""))
                    if ref:
                        refs.append(ref)
            refs_by_review[review_pmid] = list(dict.fromkeys(refs))
        time.sleep(sleep_s)
    return refs_by_review


def fetch_pubmed_titles(pmids: list[str], batch_size: int = 200, sleep_s: float = 0.35) -> pd.DataFrame:
    rows = []
    for batch in chunks(pmids, batch_size):
        handle = Entrez.efetch(db="pubmed", id=",".join(batch), rettype="xml", retmode="xml")
        records = Entrez.read(handle)
        handle.close()
        for article in records["PubmedArticle"]:
            medline = article["MedlineCitation"]
            pmid = str(medline["PMID"])
            art = medline["Article"]
            title = clean(art.get("ArticleTitle", ""))
            journal = clean(art["Journal"].get("Title", ""))
            try:
                year = clean(art["Journal"]["JournalIssue"]["PubDate"]["Year"])
            except Exception:
                year = clean(art["Journal"]["JournalIssue"]["PubDate"].get("MedlineDate", ""))[:4]
            pub_types = "; ".join(str(t) for t in art.get("PublicationTypeList", []))
            rows.append({
                "referenced_pmid": pmid,
                "referenced_year": year,
                "referenced_title": title,
                "referenced_journal": journal,
                "referenced_publication_types": pub_types,
            })
        time.sleep(sleep_s)
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description="Build review citation backcheck CSV.")
    parser.add_argument("--master", default="recipes_master_v2.csv")
    parser.add_argument("--papers", default="papers.csv")
    parser.add_argument("--output", default="outputs/review_reference_backcheck.csv")
    parser.add_argument("--priority-output", default="")
    parser.add_argument("--limit-reviews", type=int, default=0)
    parser.add_argument("--no-metadata", action="store_true")
    args = parser.parse_args()

    reviews = load_review_pmids(args.master, args.limit_reviews or None)
    master_pmids = load_existing(args.master)
    paper_pmids = load_existing(args.papers)

    refs_by_review = fetch_references(reviews["review_pmid"].astype(str).tolist())
    rows = []
    for _, review in reviews.iterrows():
        review_pmid = clean(review["review_pmid"])
        refs = refs_by_review.get(review_pmid, [])
        if not refs:
            rows.append({
                **review.to_dict(),
                "referenced_pmid": "",
                "reference_found": "False",
                "ref_in_master": "False",
                "ref_in_papers": "False",
            })
            continue
        for ref in refs:
            rows.append({
                **review.to_dict(),
                "referenced_pmid": ref,
                "reference_found": "True",
                "ref_in_master": str(ref in master_pmids),
                "ref_in_papers": str(ref in paper_pmids),
            })

    out = pd.DataFrame(rows)
    if len(out) and not args.no_metadata:
        ref_pmids = sorted({p for p in out["referenced_pmid"].astype(str) if p})
        meta = fetch_pubmed_titles(ref_pmids)
        out = out.merge(meta, on="referenced_pmid", how="left")
        title = out["referenced_title"].fillna("")
        pub_types = out["referenced_publication_types"].fillna("")
        out["reprogramming_title_hit"] = title.str.contains(
            REPROGRAMMING_TITLE_RE, case=False, regex=True, na=False
        ).astype(str)
        out["referenced_review_like"] = (
            title.str.contains(REVIEW_LIKE_RE, case=False, regex=True, na=False)
            | pub_types.str.contains(r"Review|Meta-Analysis", case=False, regex=True, na=False)
        ).astype(str)

    out["ref_missing_from_master"] = (
        out["reference_found"].eq("True") & out["ref_in_master"].eq("False")
    ).astype(str)
    out["ref_missing_from_master_and_papers"] = (
        out["reference_found"].eq("True")
        & out["ref_in_master"].eq("False")
        & out["ref_in_papers"].eq("False")
    ).astype(str)
    out["backcheck_priority"] = "low"
    if "reprogramming_title_hit" in out.columns:
        review_like = (
            out["referenced_review_like"]
            if "referenced_review_like" in out.columns
            else pd.Series("False", index=out.index)
        )
        high = (
            out["ref_missing_from_master_and_papers"].eq("True")
            & out["reprogramming_title_hit"].eq("True")
            & review_like.eq("False")
        )
        medium = (
            out["ref_missing_from_master_and_papers"].eq("True")
            & out["reprogramming_title_hit"].eq("True")
            & review_like.eq("True")
        )
        out.loc[medium, "backcheck_priority"] = "medium"
        out.loc[high, "backcheck_priority"] = "high"

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)

    if args.priority_output:
        priority = out[out["backcheck_priority"].isin(["high", "medium"])].copy()
        priority = priority.sort_values(
            ["backcheck_priority", "referenced_year", "referenced_pmid"],
            ascending=[True, False, False],
        )
        priority_path = Path(args.priority_output)
        priority_path.parent.mkdir(parents=True, exist_ok=True)
        priority.to_csv(priority_path, index=False)

    n_reviews = reviews["review_pmid"].nunique()
    n_refs = out["referenced_pmid"].astype(str).replace("", pd.NA).dropna().nunique()
    n_missing_master = out[out["ref_missing_from_master"].eq("True")]["referenced_pmid"].nunique()
    n_missing_all = out[out["ref_missing_from_master_and_papers"].eq("True")]["referenced_pmid"].nunique()
    print(f"Review PMIDs checked: {n_reviews}")
    print(f"Referenced PMIDs found: {n_refs}")
    print(f"Referenced PMIDs missing from master: {n_missing_master}")
    print(f"Referenced PMIDs missing from master+papers: {n_missing_all}")
    print(f"Wrote {len(out)} rows -> {output}")
    if args.priority_output:
        print(f"Wrote priority rows -> {args.priority_output}")


if __name__ == "__main__":
    main()
