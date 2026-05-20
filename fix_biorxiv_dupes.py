"""
Rebuild duplicate flags with transparent reasons.

Duplicate sources:
1. same_recipe: same (source_cell, target_cell, factors), keeping earliest
   research paper in each group.
2. preprint_peer_review: title-similar PMIDs where at least one record is from
   a preprint source (bioRxiv/medRxiv/Research Square) and at least one record
   is not. Keep the non-preprint PMID with the latest year.

This avoids blindly marking all title-similar PMIDs as duplicates.
"""

import re
import shutil
from collections import defaultdict
from difflib import SequenceMatcher

import pandas as pd

FILE = "recipes_master_v2.csv"
JOURNALS = "journals.csv"
KEY = ["source_cell", "target_cell", "factors"]
SIMILARITY_THRESHOLD = 0.92


def normalize_title(title: str) -> str:
    title = title.lower().strip()
    title = re.sub(r"[^a-z0-9 ]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return re.sub(r"^(a|an|the) ", "", title)


def is_preprint_journal(journal: str) -> bool:
    return any(term in journal.lower() for term in ["biorxiv", "medrxiv", "research square", "preprint"])


def add_reason(df: pd.DataFrame, mask, reason: str, group_id: str, preferred_pmid: str = ""):
    idx = df.index[mask]
    if len(idx) == 0:
        return
    df.loc[idx, "is_duplicate"] = True
    for i in idx:
        existing_reason = df.at[i, "duplicate_reason"]
        existing_group = df.at[i, "duplicate_group_id"]
        df.at[i, "duplicate_reason"] = reason if not existing_reason else f"{existing_reason};{reason}"
        df.at[i, "duplicate_group_id"] = group_id if not existing_group else f"{existing_group};{group_id}"
        if preferred_pmid:
            df.at[i, "preferred_pmid"] = preferred_pmid


def find_title_groups(pmid_info: pd.DataFrame) -> list[list[str]]:
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
        key = record["title_norm"][:1]
        blocks[key].append(record)

    for block in blocks.values():
        for i, a in enumerate(block):
            for b in block[i + 1:]:
                if not a["title_norm"] or not b["title_norm"]:
                    continue
                length_ratio = min(len(a["title_norm"]), len(b["title_norm"])) / max(len(a["title_norm"]), len(b["title_norm"]))
                if length_ratio < 0.75:
                    continue
                if SequenceMatcher(None, a["title_norm"], b["title_norm"]).ratio() >= SIMILARITY_THRESHOLD:
                    union(a["pmid"], b["pmid"])

    grouped = defaultdict(list)
    for pmid in parent:
        grouped[find(pmid)].append(pmid)
    return [sorted(pmids, key=int) for pmids in grouped.values() if len(pmids) > 1]


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")
    journals = pd.read_csv(JOURNALS, dtype=str).fillna("") if pd.io.common.file_exists(JOURNALS) else pd.DataFrame(columns=["pmid", "journal"])
    journal_map = journals.set_index("pmid")["journal"].to_dict()

    pmid_info = df[["pmid", "title", "year"]].drop_duplicates("pmid").copy()
    pmid_info["journal"] = pmid_info["pmid"].map(journal_map).fillna("")
    pmid_info["title_norm"] = pmid_info["title"].apply(normalize_title)
    pmid_info["year_int"] = pd.to_numeric(pmid_info["year"], errors="coerce").fillna(0).astype(int)
    info = pmid_info.set_index("pmid").to_dict("index")

    preprint_decisions = []
    protected_keep_pmids = set()
    for group_no, pmids in enumerate(find_title_groups(pmid_info), 1):
        preprints = [p for p in pmids if is_preprint_journal(info[p]["journal"])]
        reviewed = [p for p in pmids if not is_preprint_journal(info[p]["journal"])]
        if not preprints or not reviewed:
            continue
        keep = max(reviewed, key=lambda p: (info[p]["year_int"], int(p)))
        protected_keep_pmids.add(keep)
        preprint_decisions.append((group_no, preprints, keep))

    df["is_duplicate"] = False
    df["duplicate_reason"] = ""
    df["preferred_pmid"] = ""
    df["duplicate_group_id"] = ""

    # Same recipe duplicates.
    df["year_int"] = pd.to_numeric(df["year"], errors="coerce").fillna(9999).astype(int)
    same_recipe_groups = 0
    same_recipe_marked = 0
    dup_mask = df.duplicated(subset=KEY, keep=False)
    for group_no, (_, group) in enumerate(df[dup_mask].groupby(KEY, sort=False), 1):
        if len(group) < 2:
            continue
        same_recipe_groups += 1
        protected = group[group["pmid"].isin(protected_keep_pmids)]
        if len(protected):
            keep_idx = protected["year_int"].idxmax()
        else:
            research = group[group["paper_type"] == "research"]
            keep_idx = research["year_int"].idxmin() if len(research) else group["year_int"].idxmin()
        keep_pmid = df.at[keep_idx, "pmid"]
        mask = group.index != keep_idx
        drop_idx = group.index[mask]
        add_reason(df, df.index.isin(drop_idx), "same_recipe", f"recipe_{group_no:04d}", keep_pmid)
        same_recipe_marked += len(drop_idx)

    # Preprint -> peer-reviewed duplicates.
    preprint_groups = 0
    preprint_pmids = set()
    for group_no, preprints, keep in preprint_decisions:
        preprint_groups += 1
        for pmid in preprints:
            preprint_pmids.add(pmid)
            add_reason(df, df["pmid"] == pmid, "preprint_peer_review", f"title_{group_no:04d}", keep)

    df = df.drop(columns=["year_int"])

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")

    print(f"same_recipe groups: {same_recipe_groups}")
    print(f"same_recipe duplicate rows: {same_recipe_marked}")
    print(f"preprint_peer_review groups: {preprint_groups}")
    print(f"preprint PMID marked: {len(preprint_pmids)}")
    print(f"total is_duplicate=True: {(df['is_duplicate'].astype(str).str.lower() == 'true').sum()}")
    print(f"保存至 {FILE}")


if __name__ == "__main__":
    main()
