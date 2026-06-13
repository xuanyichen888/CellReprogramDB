"""
[DEPRECATED — DO NOT RUN against the current database.]

This was the original "rebuild recipes_master_v2.csv from scratch" merge. It
REBUILDS the master from recipes_v2.csv + recipes_fulltext.csv and writes only
the 12 base columns — so it DROPS every curation column (single_tf_status,
conversion_scope, is_duplicate / is_broad_duplicate and their metadata, all
validation_*, *_std / *_broad, *_inference_method, manual T-cell review, etc.).
Running it on the curated master would silently destroy that work.

Use the safe incremental flow instead: append only new recipes, mark them
validation_needs_review=True, and run the idempotent curation scripts
(normalize_celltypes_std, flag_single_tf, fix_factor_types, mark_duplicates,
mark_broad_duplicates) — never this whole-table rebuild.

Kept in the repo for historical reference only. It refuses to run unless you
pass the explicit override flag, which says exactly what it does.
"""

import sys

import pandas as pd

OVERRIDE_FLAG = "--i-understand-this-rebuilds-master-and-drops-curation"
if OVERRIDE_FLAG not in sys.argv:
    raise SystemExit(
        "merge_master.py is DEPRECATED and refuses to run.\n"
        "It rebuilds recipes_master_v2.csv from scratch and DROPS all curation "
        "columns.\nUse the safe incremental append flow instead.\n"
        f"If you really mean to rebuild, re-run with: {OVERRIDE_FLAG}"
    )

KEEP_SPECIES = {'human', 'mouse', 'human, mouse', 'mouse, human', ''}

# ── 1. 加载 abstract recipes ────────────────────────────────────────────────
print("加载 recipes_v2.csv ...")
abstract = pd.read_csv("recipes_v2.csv", dtype=str).fillna("")
abstract["source"] = "abstract"
before = len(abstract)
abstract = abstract[abstract["species"].isin(KEEP_SPECIES)].copy()
print(f"  {before} → {len(abstract)} 条（过滤后，仅保留 human/mouse/未知）")

# ── 2. 加载 fulltext recipes ────────────────────────────────────────────────
import os
fulltext_rows = []
for fname in ["recipes_fulltext.csv"]:
    if os.path.exists(fname):
        ft = pd.read_csv(fname, dtype=str).fillna("")
        ft["source"] = "fulltext"
        ft_filtered = ft[ft["species"].isin(KEEP_SPECIES)].copy()
        fulltext_rows.append(ft_filtered)
        print(f"加载 {fname}: {len(ft)} → {len(ft_filtered)} 条")

# ── 3. 合并 ──────────────────────────────────────────────────────────────────
combined_parts = [abstract] + fulltext_rows
combined = pd.concat(combined_parts, ignore_index=True)

# 确保列齐全
for col in ["pmid", "source_cell", "target_cell", "factors", "factor_type",
            "species", "culture_condition", "confidence", "paper_type",
            "notes", "source"]:
    if col not in combined.columns:
        combined[col] = ""

combined["evidence_sentence"] = ""

# ── 4. 去重（同一 pmid + source_cell + target_cell + factors 只保留一条）───
key_cols = ["pmid", "source_cell", "target_cell", "factors"]
before_dedup = len(combined)
combined.drop_duplicates(subset=key_cols, keep="first", inplace=True)
print(f"\n去重前: {before_dedup} → 去重后: {len(combined)} 条")

# ── 5. 从旧 recipes_master_v2.csv 恢复 evidence_sentence ───────────────────
if os.path.exists("recipes_master_v2.csv"):
    old = pd.read_csv("recipes_master_v2.csv", dtype=str).fillna("")
    # 用 (pmid, source_cell, target_cell, factors) 做 key 匹配
    old_ev = old.set_index(key_cols)["evidence_sentence"].to_dict()

    def get_ev(row):
        k = (row["pmid"], row["source_cell"], row["target_cell"], row["factors"])
        return old_ev.get(k, "")

    combined["evidence_sentence"] = combined.apply(get_ev, axis=1)
    recovered = (combined["evidence_sentence"] != "").sum()
    print(f"从旧数据库恢复 evidence_sentence: {recovered} 条")

# ── 6. 输出 ──────────────────────────────────────────────────────────────────
out_cols = [
    "pmid", "source_cell", "target_cell",
    "factors", "factor_type",
    "species", "culture_condition",
    "confidence", "paper_type", "notes",
    "source", "evidence_sentence",
]
combined = combined[out_cols]
combined.to_csv("recipes_master_v2.csv", index=False, encoding="utf-8")

total = len(combined)
with_ev = (combined["evidence_sentence"] != "").sum()
no_ev   = total - with_ev

print(f"\n✅ 完成！")
print(f"   总条数:            {total}")
print(f"   有 evidence:       {with_ev} ({with_ev/total*100:.1f}%)")
print(f"   需补 evidence:     {no_ev}")
print(f"   → 保存至 recipes_master_v2.csv")
print(f"\n下一步: python extract_evidence.py  (补充 {no_ev} 条 evidence)")
