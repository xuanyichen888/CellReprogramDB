"""
Export title-similarity PMID groups for manual duplicate review.

The exported workbook is for human review only; it does not change the database.
"""

import re
from collections import defaultdict
from difflib import SequenceMatcher

import pandas as pd

FILE = "recipes_master_v2.csv"
JOURNALS = "journals.csv"
OUTPUT = "title_similarity_review.xlsx"
SIMILARITY_THRESHOLD = 0.92


def normalize_title(title: str) -> str:
    title = title.lower().strip()
    title = re.sub(r"[^a-z0-9 ]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return re.sub(r"^(a|an|the) ", "", title)


def is_preprint_journal(journal: str) -> bool:
    return any(term in journal.lower() for term in ["biorxiv", "medrxiv", "research square", "preprint"])


def find_groups(pmid_info: pd.DataFrame) -> list[list[str]]:
    records = pmid_info.to_dict("records")
    parent = {r["pmid"]: r["pmid"] for r in records}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    blocks = defaultdict(list)
    for record in records:
        blocks[record["title_norm"][:1]].append(record)

    for block in blocks.values():
        for i, a in enumerate(block):
            for b in block[i + 1:]:
                if not a["title_norm"] or not b["title_norm"]:
                    continue
                length_ratio = min(len(a["title_norm"]), len(b["title_norm"])) / max(len(a["title_norm"]), len(b["title_norm"]))
                if length_ratio < 0.75:
                    continue
                score = SequenceMatcher(None, a["title_norm"], b["title_norm"]).ratio()
                if score >= SIMILARITY_THRESHOLD:
                    union(a["pmid"], b["pmid"])

    grouped = defaultdict(list)
    for pmid in parent:
        grouped[find(pmid)].append(pmid)
    return [sorted(pmids, key=int) for pmids in grouped.values() if len(pmids) > 1]


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")
    journals = pd.read_csv(JOURNALS, dtype=str).fillna("") if pd.io.common.file_exists(JOURNALS) else pd.DataFrame(columns=["pmid", "journal"])
    journal_map = journals.set_index("pmid")["journal"].to_dict()

    pmid_info = df[["pmid", "title", "year", "is_duplicate"]].drop_duplicates("pmid").copy()
    pmid_info["journal"] = pmid_info["pmid"].map(journal_map).fillna("")
    pmid_info["title_norm"] = pmid_info["title"].apply(normalize_title)
    groups = find_groups(pmid_info)

    rows = []
    info = pmid_info.set_index("pmid").to_dict("index")
    for group_no, pmids in enumerate(groups, 1):
        group_info = [info[p] for p in pmids]
        has_preprint = any(is_preprint_journal(x["journal"]) for x in group_info)
        non_preprint = [p for p in pmids if not is_preprint_journal(info[p]["journal"])]
        suggested_keep = max(non_preprint or pmids, key=lambda p: (int(info[p]["year"] or 0), int(p)))
        for pmid in pmids:
            item = info[pmid]
            rows.append({
                "group_id": f"title_{group_no:03d}",
                "pmid": pmid,
                "year": item["year"],
                "journal": item["journal"],
                "is_preprint_journal": is_preprint_journal(item["journal"]),
                "current_is_duplicate": item["is_duplicate"],
                "suggested_keep_pmid": suggested_keep if has_preprint else "",
                "title": item["title"],
                "manual_decision": "",
                "preferred_pmid": "",
                "notes": "",
            })

    out = pd.DataFrame(rows)
    out.to_excel(OUTPUT, index=False)
    print(f"导出 {len(groups)} 个 title-similarity 组，{len(out)} 行 -> {OUTPUT}")


if __name__ == "__main__":
    main()
