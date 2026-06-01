"""
Mark duplicate recipes in recipes_master_v2.csv.

Duplicate key:
  source_cell_std / target_cell_std / normalized factor set

This catches cases such as "T-cell" vs "T cell" and factor-order variants.
Existing manual duplicate annotations are preserved.
"""

import re
import shutil

import pandas as pd

FILE = "recipes_master_v2.csv"


def append_tag(existing: str, tag: str) -> str:
    existing = str(existing or "").strip()
    parts = [part.strip() for part in existing.split(";") if part.strip()]
    if tag and tag not in parts:
        parts.append(tag)
    return ";".join(parts)


def split_factors(value: str) -> list[str]:
    """Split a comma/semicolon factor list while respecting parentheses."""
    text = str(value or "").strip()
    if not text:
        return []
    parts = []
    buf = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        if ch in {",", ";"} and depth == 0:
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)
    part = "".join(buf).strip()
    if part:
        parts.append(part)
    return parts


def normalize_factor_for_key(value: str) -> str:
    f = str(value or "").strip()
    if not f:
        return ""
    low = f.lower()
    if low in {"not specified", "unknown", "not specified in text"}:
        return "not specified"

    f = (
        f.replace("α", "A")
        .replace("β", "B")
        .replace("γ", "G")
        .replace("δ", "D")
    )
    f = re.sub(
        r"\s*\((?:pou5f1|oct3/4|oct4|oskm|yamanaka'?s? factors?)\)\s*$",
        "",
        f,
        flags=re.I,
    )
    f = re.sub(r"\s+", " ", f).strip()

    compact = re.sub(r"[^A-Za-z0-9]+", "", f).upper()
    aliases = {
        "OCT34": "OCT4",
        "OCT3": "OCT4",
        "OCT4": "OCT4",
        "POU5F1": "OCT4",
        "SOX2": "SOX2",
        "KLF4": "KLF4",
        "CMYC": "C-MYC",
        "MYC": "C-MYC",
        "NMYC": "N-MYC",
        "LMYC": "L-MYC",
        "NANOG": "NANOG",
        "LIN28": "LIN28",
        "LIN28A": "LIN28",
        "ASCL1": "ASCL1",
        "MASH1": "ASCL1",
        "NEUROG2": "NGN2",
        "NEUROGENIN2": "NGN2",
        "NGN2": "NGN2",
        "NEUROD1": "NEUROD1",
        "GATA4": "GATA4",
        "MEF2C": "MEF2C",
        "TBX5": "TBX5",
        "HAND2": "HAND2",
        "PDX1": "PDX1",
        "NGN3": "NGN3",
        "NEUROG3": "NGN3",
        "MAFA": "MAFA",
        "HNF4A": "HNF4A",
        "HNF1A": "HNF1A",
        "FOXA1": "FOXA1",
        "FOXA2": "FOXA2",
        "FOXA3": "FOXA3",
        "CEBPA": "CEBPA",
        "CEBPALPHA": "CEBPA",
        "PU1": "SPI1",
        "SPI1": "SPI1",
        "ETV2": "ETV2",
        "ER71": "ETV2",
        "ATOH1": "ATOH1",
        "MATH1": "ATOH1",
        "DLX2": "DLX2",
    }
    if compact in aliases:
        return aliases[compact]

    return re.sub(r"\s+", " ", f).strip().upper()


def factor_key(value: str) -> str:
    parts = [normalize_factor_for_key(part) for part in split_factors(value)]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    if parts == ["not specified"]:
        return "not specified"
    return " | ".join(sorted(parts))


def normalized_cell_key(row: pd.Series, std_col: str, raw_col: str) -> str:
    value = row.get(std_col, "") or row.get(raw_col, "")
    value = str(value or "").strip().lower()
    value = value.replace("β", "beta")
    value = re.sub(r"[-_/]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")
    for col, default in {
        "is_duplicate": "False",
        "duplicate_reason": "",
        "preferred_pmid": "",
        "duplicate_group_id": "",
    }.items():
        if col not in df.columns:
            df[col] = default

    df["year_int"] = pd.to_numeric(df["year"], errors="coerce").fillna(9999).astype(int)
    df["_source_key"] = df.apply(lambda r: normalized_cell_key(r, "source_cell_std", "source_cell"), axis=1)
    df["_target_key"] = df.apply(lambda r: normalized_cell_key(r, "target_cell_std", "target_cell"), axis=1)
    df["_factor_key"] = df["factors"].apply(factor_key)
    key = ["_source_key", "_target_key", "_factor_key"]

    manual_duplicate = (
        (df["validation_action"].astype(str).str.lower() == "mark_duplicate")
        | df["duplicate_reason"].astype(str).str.contains("superseded|preprint|manual", case=False, na=False)
    )
    df["is_duplicate"] = manual_duplicate.astype(bool)

    dup_mask = df.duplicated(subset=key, keep=False)
    dup_df = df[dup_mask].copy()

    n_groups = 0
    n_marked = 0

    for group_no, (_, group) in enumerate(dup_df.groupby(key, sort=False), 1):
        if len(group) < 2:
            continue
        n_groups += 1

        ranked = group.copy()
        hidden_actions = {"remove", "hide_incomplete_recipe", "hide_single_tf", "mark_duplicate"}
        ranked["_manual_hide_rank"] = (
            ranked["validation_action"].astype(str).str.lower().isin(hidden_actions)
            | (ranked["validation_recipe_valid"].astype(str).str.lower() == "no")
        ).astype(int)
        ranked["_paper_rank"] = ranked["paper_type"].map({"research": 0, "review": 1, "other": 2}).fillna(3)
        ranked["_source_rank"] = ranked["source"].map({"fulltext": 0, "manual": 0, "abstract": 1}).fillna(2)
        ranked["_conf_rank"] = ranked["confidence"].map({"high": 0, "medium": 1, "low": 2}).fillna(3)
        ranked["_has_evidence_rank"] = (ranked["evidence_sentence"].astype(str).str.strip() == "").astype(int)
        ranked = ranked.sort_values(
            [
                "_manual_hide_rank",
                "_paper_rank",
                "year_int",
                "_source_rank",
                "_conf_rank",
                "_has_evidence_rank",
                "pmid",
            ],
            kind="mergesort",
        )
        keep_idx = ranked.index[0]
        keep_is_manually_hidden = bool(ranked.loc[keep_idx, "_manual_hide_rank"])
        if not keep_is_manually_hidden:
            df.loc[keep_idx, "is_duplicate"] = False

        dup_idx = group.index[group.index != keep_idx]
        newly_marked = dup_idx[~df.loc[dup_idx, "is_duplicate"].astype(bool)]
        df.loc[dup_idx, "is_duplicate"] = True
        n_marked += len(newly_marked)

        group_id = f"recipe_{group_no:04d}"
        preferred = str(df.at[keep_idx, "pmid"])
        for idx in dup_idx:
            df.at[idx, "duplicate_reason"] = append_tag(
                df.at[idx, "duplicate_reason"], "same_standardized_recipe"
            )
            df.at[idx, "duplicate_group_id"] = append_tag(df.at[idx, "duplicate_group_id"], group_id)
            if not str(df.at[idx, "preferred_pmid"]).strip():
                df.at[idx, "preferred_pmid"] = preferred

    df = df.drop(columns=["year_int", "_source_key", "_target_key", "_factor_key"])

    not_duplicate = df["is_duplicate"].astype(str).str.lower() != "true"
    not_manual_duplicate = df["validation_action"].astype(str).str.lower() != "mark_duplicate"
    df.loc[not_duplicate & not_manual_duplicate, ["duplicate_reason", "preferred_pmid", "duplicate_group_id"]] = ""

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")

    is_dup = df["is_duplicate"].astype(str).str.lower() == "true"
    total_dupes = is_dup.sum()
    print(f"重复组数: {n_groups}")
    print(f"新增标记 is_duplicate=True: {n_marked} 条")
    print(f"总计 is_duplicate=True: {total_dupes} 条")
    print(f"保留的唯一 recipe: {len(df) - total_dupes} 条")
    print("\n重复条目的 paper_type 分布:")
    print(df[is_dup]["paper_type"].value_counts().to_string())
    print(f"\n保存至 {FILE}")


if __name__ == "__main__":
    main()
