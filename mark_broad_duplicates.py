"""
Add broad cell-type duplicate annotations to recipes_master_v2.csv.

This is intentionally more aggressive than mark_duplicates.py:
  - fibroblast subtypes -> fibroblast
  - iPSC/pluripotent variants -> induced pluripotent stem cell
  - generic neuron-like targets -> neuron, with major neuronal subtypes preserved
  - beta-cell punctuation/wording variants -> insulin-producing cell

It does not delete rows. It adds:
  source_cell_broad, target_cell_broad,
  is_broad_duplicate, broad_duplicate_reason,
  broad_preferred_pmid, broad_duplicate_group_id
"""

import re
import shutil

import pandas as pd

from mark_duplicates import factor_key

FILE = "recipes_master_v2.csv"


def clean_text(value: str) -> str:
    text = str(value or "").strip().lower()
    text = (
        text.replace("β", "beta")
        .replace("α", "alpha")
        .replace("γ", "gamma")
        .replace("δ", "delta")
    )
    text = re.sub(r"[-_/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def broad_cell(name: str, role: str) -> str:
    original = str(name or "").strip()
    low = clean_text(original)
    if not low:
        return ""

    if (
        has(low, r"\b(induced pluripotent stem cell|ips cell|ipsc|ips like|pluripotent stem cell|pluripotent cell|pluripotent state)\b")
        and not has(low, r"naive|2 cell|totipotent|embryonic germ")
    ):
        return "induced pluripotent stem cell"

    if has(low, r"\b(human embryonic stem cell|mouse embryonic stem cell|embryonic stem cell|esc)\b"):
        return "embryonic stem cell"

    if has(low, r"fibroblast|mef\b|mouse embryonic fibroblast"):
        return "fibroblast"

    if has(low, r"mesenchymal (stem|stromal) cell|msc\b|adipose.*stem cell|dental pulp.*stem cell|bone marrow.*stem cell"):
        return "mesenchymal stem cell"

    if has(low, r"astrocyte|astroglia"):
        return "astrocyte"

    if has(low, r"muller glia|muller glial|müller glia|müller glial"):
        return "Muller glia"

    if has(low, r"pancreatic (acinar|exocrine)"):
        return "pancreatic exocrine cell"

    if has(low, r"pancreatic.*duct|ductal"):
        return "pancreatic ductal cell"

    if has(low, r"hepatic stellate"):
        return "hepatic stellate cell"

    if has(low, r"hepatocyte|hepatic cell") and "like" not in low:
        return "hepatocyte"

    if has(low, r"peripheral blood mononuclear|pbmc"):
        return "peripheral blood mononuclear cell"

    if has(low, r"\bb cell\b|b lymphocyte|pre b cell"):
        return "B cell"

    if has(low, r"\bt cell\b|t lymphocyte|treg|regulatory t"):
        return "T cell"

    if has(low, r"endothelial|huvec"):
        return "endothelial cell"

    if has(low, r"macrophage|monocyte"):
        return "macrophage"

    if has(low, r"keratinocyte"):
        return "keratinocyte"

    if has(low, r"neural stem|neural progenitor|neural precursor|nsc\b|npc\b"):
        return "neural stem/progenitor cell"

    if role == "target":
        if has(low, r"cardiomyocyte|cardiac myocyte|myocardiocyte|cardiac like"):
            return "cardiomyocyte"
        if has(low, r"hepatocyte like|induced hepatocyte|ihep|hepatic like"):
            return "hepatocyte-like cell"
        if has(low, r"beta cell|beta like|insulin producing|insulin secreting|insulin expressing"):
            return "insulin-producing cell"
        if has(low, r"endothelial progenitor|endothelial like"):
            return "endothelial cell"
        if has(low, r"macrophage|m1 macrophage|m2 macrophage"):
            return "macrophage"
        if has(low, r"hair cell|inner hair|outer hair|vestibular hair"):
            return "hair cell"
        if has(low, r"dopaminergic|dopamine neuron"):
            return "dopaminergic neuron"
        if has(low, r"motor neuron"):
            return "motor neuron"
        if has(low, r"gabaergic|interneuron"):
            return "GABAergic neuron"
        if has(low, r"glutamatergic|excitatory"):
            return "glutamatergic neuron"
        if has(low, r"retinal ganglion"):
            return "retinal ganglion cell"
        if has(low, r"neuron|neuronal|neuroblast|neural cell"):
            return "neuron"
        if has(low, r"osteoblast|osteogenic"):
            return "osteoblast"
        if has(low, r"myofibroblast"):
            return "myofibroblast"

    return original


def choose_keep(group: pd.DataFrame) -> int:
    ranked = group.copy()
    hidden_actions = {"remove", "hide_incomplete_recipe", "hide_single_tf", "mark_duplicate"}
    ranked["_manual_hide_rank"] = (
        ranked["validation_action"].astype(str).str.lower().isin(hidden_actions)
        | (ranked["validation_recipe_valid"].astype(str).str.lower() == "no")
    ).astype(int)
    ranked["_paper_rank"] = ranked["paper_type"].map({"research": 0, "review": 1, "other": 2}).fillna(3)
    ranked["_needs_review_rank"] = (
        ranked["validation_needs_review"].astype(str).str.lower().isin({"true", "1", "yes"})
    ).astype(int)
    ranked["_year_rank"] = pd.to_numeric(ranked["year"], errors="coerce").fillna(9999).astype(int)
    ranked["_source_rank"] = ranked["source"].map({"fulltext": 0, "manual": 0, "abstract": 1}).fillna(2)
    ranked["_conf_rank"] = ranked["confidence"].map({"high": 0, "medium": 1, "low": 2}).fillna(3)
    ranked["_has_evidence_rank"] = (ranked["evidence_sentence"].astype(str).str.strip() == "").astype(int)
    ranked = ranked.sort_values(
        [
            "_manual_hide_rank",
            "_paper_rank",
            "_needs_review_rank",
            "_year_rank",
            "_source_rank",
            "_conf_rank",
            "_has_evidence_rank",
            "pmid",
        ],
        kind="mergesort",
    )
    return ranked.index[0]


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")
    for col, default in {
        "source_cell_broad": "",
        "target_cell_broad": "",
        "is_broad_duplicate": "False",
        "broad_duplicate_reason": "",
        "broad_preferred_pmid": "",
        "broad_duplicate_group_id": "",
    }.items():
        if col not in df.columns:
            df[col] = default

    df["source_cell_broad"] = df["source_cell_std"].apply(lambda s: broad_cell(s, "source"))
    df["target_cell_broad"] = df["target_cell_std"].apply(lambda s: broad_cell(s, "target"))
    df["_factor_key"] = df["factors"].apply(factor_key)

    # The auto-marker owns exactly one reason tag; everything else in
    # broad_duplicate_reason is a manual / model adjudication note that must
    # survive re-runs. Snapshot the non-auto remainder before recomputing.
    auto_tag = "same_broad_cell_recipe"

    def manual_note(reason: str) -> str:
        parts = [p.strip() for p in re.split(r"[;|]", str(reason or "")) if p.strip()]
        return " | ".join(p for p in parts if p != auto_tag)

    def with_auto_tag(manual: str) -> str:
        manual = str(manual or "").strip()
        return f"{auto_tag} | {manual}" if manual else auto_tag

    preserved_note = df["broad_duplicate_reason"].apply(manual_note)

    manual_duplicate = (
        (df["validation_action"].astype(str).str.lower() == "mark_duplicate")
        | df["duplicate_reason"].astype(str).str.contains("superseded|preprint|manual", case=False, na=False)
    )
    df["is_broad_duplicate"] = manual_duplicate.astype(bool)
    # Keep manual notes; only the auto-generated grouping is rebuilt each run.
    df["broad_duplicate_reason"] = preserved_note
    df["broad_preferred_pmid"] = ""
    df["broad_duplicate_group_id"] = ""

    key = ["source_cell_broad", "target_cell_broad", "_factor_key"]
    dup_mask = df.duplicated(subset=key, keep=False)
    dup_df = df[dup_mask].copy()

    n_groups = 0
    n_marked = 0
    for group_no, (_, group) in enumerate(dup_df.groupby(key, sort=False), 1):
        if len(group) < 2:
            continue
        n_groups += 1
        keep_idx = choose_keep(group)
        df.loc[keep_idx, "is_broad_duplicate"] = False

        dup_idx = group.index[group.index != keep_idx]
        newly_marked = dup_idx[~df.loc[dup_idx, "is_broad_duplicate"].astype(bool)]
        df.loc[dup_idx, "is_broad_duplicate"] = True
        n_marked += len(newly_marked)

        group_id = f"broad_recipe_{group_no:04d}"
        preferred = str(df.at[keep_idx, "pmid"])
        for idx in dup_idx:
            df.at[idx, "broad_duplicate_reason"] = with_auto_tag(df.at[idx, "broad_duplicate_reason"])
            df.at[idx, "broad_duplicate_group_id"] = group_id
            df.at[idx, "broad_preferred_pmid"] = preferred

    df = df.drop(columns=["_factor_key"])

    # Manual-adjudication duplicates that fall outside any computed group still
    # need complete metadata (preferred / group / reason) instead of blanks.
    is_dup = df["is_broad_duplicate"].astype(str).str.lower() == "true"
    orphan = is_dup & (df["broad_duplicate_group_id"].astype(str).str.strip() == "")
    for idx in df.index[orphan]:
        note = str(df.at[idx, "broad_duplicate_reason"]).strip()
        why = str(df.at[idx, "duplicate_reason"]).strip() or "manual_adjudication"
        df.at[idx, "broad_duplicate_reason"] = note if note else why
        # These are same-paper supersessions (abstract -> fulltext) or other
        # manual merges: the kept recipe is the better extraction of the same
        # PMID, so fall back to the row's own pmid when no peer preferred exists.
        pref = str(df.at[idx, "preferred_pmid"]).strip() or str(df.at[idx, "pmid"]).strip()
        df.at[idx, "broad_preferred_pmid"] = pref
        grp = str(df.at[idx, "duplicate_group_id"]).strip()
        df.at[idx, "broad_duplicate_group_id"] = grp if grp else (f"broad_manual_{pref}" if pref else "broad_manual")

    # For non-duplicates, drop only the auto grouping; keep any manual note.
    not_duplicate = ~is_dup
    df.loc[not_duplicate, ["broad_preferred_pmid", "broad_duplicate_group_id"]] = ""

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")

    is_dup = df["is_broad_duplicate"].astype(str).str.lower() == "true"
    print(f"broad duplicate groups: {n_groups}")
    print(f"newly broad-marked duplicates: {n_marked}")
    print(f"total is_broad_duplicate=True: {is_dup.sum()}")
    print(f"broad unique recipes: {len(df) - is_dup.sum()}")
    print(f"saved {FILE}")


if __name__ == "__main__":
    main()
