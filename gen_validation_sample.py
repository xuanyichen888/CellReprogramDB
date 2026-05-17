"""
生成人工验证样本 verification_sample_v3.xlsx

Sheet 1: validation_sample
  分层抽样策略（共50条，便于计算整数百分比）：
    fulltext / high:   20条
    abstract / high:   15条
    medium (any):      12条
    single_tf (high):   3条  ← 专门验证单TF问题

Sheet 2: known_issues
  博后反馈中点名的 PMID，用于 targeted QA，不纳入随机样本 precision 计算。

Sheet 3: annotation_guide
  人工标注字段说明和推荐取值。
"""
import pandas as pd, random
random.seed(42)

FILE   = "recipes_master_v2.csv"
OUTPUT = "verification_sample_v3.xlsx"

df = pd.read_csv(FILE, dtype=str).fillna("")
papers = pd.read_csv("papers.csv", dtype=str).fillna("")
abstract_map = papers.set_index("pmid")["abstract"].to_dict()
df["abstract"] = df["pmid"].map(abstract_map).fillna("")
df["pubmed_url"] = "https://pubmed.ncbi.nlm.nih.gov/" + df["pmid"].astype(str)

# 基础过滤：research 论文，非 bioRxiv 重复，非 not specified
base = df[
    (df["is_duplicate"].astype(str).str.lower() != "true") &
    (df["factors"] != "not specified") &
    (df["paper_type"] == "research")
].copy()

# 各层定义
ft_high   = base[(base["source"] == "fulltext") & (base["confidence"] == "high") &
                  (base["single_tf_flag"].astype(str).str.lower() != "true")]
ab_high   = base[(base["source"] == "abstract") & (base["confidence"] == "high") &
                  (base["single_tf_flag"].astype(str).str.lower() != "true")]
medium    = base[(base["confidence"] == "medium") &
                  (base["single_tf_flag"].astype(str).str.lower() != "true")]
single_tf = base[(base["single_tf_flag"].astype(str).str.lower() == "true") &
                  (base["confidence"] == "high")]

print(f"Pool sizes:")
print(f"  fulltext/high (non-single-TF): {len(ft_high)}")
print(f"  abstract/high (non-single-TF): {len(ab_high)}")
print(f"  medium:                        {len(medium)}")
print(f"  single_TF/high:                {len(single_tf)}")

def take(pool: pd.DataFrame, n: int, stratum: str, seed: int) -> pd.DataFrame:
    """Sample a stratum and label it before any shuffle."""
    out = pool.sample(min(n, len(pool)), random_state=seed).copy()
    out["stratum"] = stratum
    return out

# 抽样：先贴 stratum，再合并和 shuffle，避免标签错位
s_ft_high   = take(ft_high,   20, "fulltext/high", 42)
s_ab_high   = take(ab_high,   15, "abstract/high", 43)
s_medium    = take(medium,    12, "medium",        44)
s_single_tf = take(single_tf,  3, "single_TF",     45)

sample = pd.concat([s_ft_high, s_ab_high, s_medium, s_single_tf], ignore_index=True)
sample = sample.sample(frac=1, random_state=99).reset_index(drop=True)
sample.insert(0, "no", range(1, len(sample)+1))

# 人工标注列
ANNOTATION_COLS = [
    "recipe_valid",          # yes / partial / no / unclear
    "source_cell_valid",     # yes / partial / no / not_applicable
    "target_cell_valid",     # yes / partial / no / not_applicable
    "factors_valid",         # yes / partial / no / not_applicable
    "standalone_recipe",     # yes / no_member_of_larger_recipe / unclear
    "duplicate_status",      # no_duplicate / duplicate_preprint / duplicate_other / unsure
    "preferred_pmid",        # 如果是 preprint/peer-review 重复，填 peer-reviewed PMID
    "error_category",        # 用分号分隔多个标签
    "action",                # keep / fix / hide_single_tf / mark_duplicate / remove / needs_fulltext
    "corrected_source_cell",
    "corrected_target_cell",
    "corrected_factors",
    "annotation_notes",
]
for col in ANNOTATION_COLS:
    sample[col] = ""

# 输出列
COLS = ["no", "pmid", "title", "source_cell", "target_cell",
        "factors", "factor_type", "confidence", "stratum",
        "source", "single_tf_flag", "pubmed_url", "evidence_sentence",
        "abstract"] + ANNOTATION_COLS

out = sample[[c for c in COLS if c in sample.columns]].copy()
out["no"] = range(1, len(out) + 1)

# 博后点名的例子，用于 targeted QA，不混入随机样本统计
KNOWN_ISSUES = {
    "32016422": "single TF likely member of Yamanaka recipe, not standalone SOX2 recipe",
    "36993577": "bioRxiv/preprint duplicate; should prefer peer-reviewed PMID 37421991",
    "37421991": "peer-reviewed version of PMID 36993577",
    "31089332": "abstract entry missing source/small molecules; fulltext entry may be more accurate",
}
known = df[df["pmid"].isin(KNOWN_ISSUES)].copy()
known.insert(0, "known_issue_note", known["pmid"].map(KNOWN_ISSUES))
known = known[[
    "known_issue_note", "pmid", "title", "source_cell", "target_cell",
    "factors", "factor_type", "confidence", "source", "single_tf_flag",
    "is_duplicate", "pubmed_url", "evidence_sentence", "abstract",
]]

guide = pd.DataFrame([
    ("confidence", "high", "Original extraction label: source cell, target cell, and named factor/cocktail are explicitly supported."),
    ("confidence", "medium", "Original extraction label: conversion is likely, but source/target/factors are vague, partial, or need full-text confirmation."),
    ("confidence", "low", "Original extraction label: conversion is inferred or weakly supported; usually not included in default website view."),
    ("recipe_valid", "yes | partial | no | unclear", "Overall recipe-level correctness."),
    ("source_cell_valid", "yes | partial | no | not_applicable", "Whether source cell is correct and specific enough."),
    ("target_cell_valid", "yes | partial | no | not_applicable", "Whether target cell is correct and specific enough."),
    ("factors_valid", "yes | partial | no | not_applicable", "Whether the factor list is correct and complete."),
    ("standalone_recipe", "yes | no_member_of_larger_recipe | unclear", "Use no_member_of_larger_recipe for cases like SOX2-only Yamanaka-member papers."),
    ("duplicate_status", "no_duplicate | duplicate_preprint | duplicate_other | unsure", "Mark PMID-level duplicates, especially bioRxiv to peer-reviewed pairs."),
    ("preferred_pmid", "PMID or blank", "For duplicates, enter the PMID that should be kept."),
    ("error_category", "single_tf_incomplete; missing_source; factors_unspecified; wrong_factor; wrong_cell; duplicate_preprint; target_synonym; evidence_weak; other", "Use semicolon-separated tags."),
    ("action", "keep | fix | hide_single_tf | mark_duplicate | remove | needs_fulltext", "Suggested database action after annotation."),
    ("corrected_source_cell", "free text", "Fill only if source_cell needs correction."),
    ("corrected_target_cell", "free text", "Fill only if target_cell needs correction."),
    ("corrected_factors", "free text", "Fill only if factors need correction."),
    ("annotation_notes", "free text", "Short explanation or literature note."),
], columns=["column", "allowed_values", "how_to_use"])

with pd.ExcelWriter(OUTPUT) as writer:
    out.to_excel(writer, sheet_name="validation_sample", index=False)
    known.to_excel(writer, sheet_name="known_issues", index=False)
    guide.to_excel(writer, sheet_name="annotation_guide", index=False)

print(f"\n生成 {OUTPUT}，共 {len(out)} 条")
print("分层分布:")
print(out["stratum"].value_counts().to_string())
print("\nKnown issues:")
print(known["pmid"].value_counts().to_string())
