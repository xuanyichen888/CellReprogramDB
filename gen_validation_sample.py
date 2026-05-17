"""
生成人工验证样本 verification_sample_v3.xlsx
分层抽样策略（共50条，便于计算整数百分比）：
  fulltext / high:   20条
  abstract / high:   15条
  medium (any):      12条
  single_tf (high):   3条  ← 新增，专门验证单TF问题
"""
import pandas as pd, random
random.seed(42)

FILE   = "recipes_master_v2.csv"
OUTPUT = "verification_sample_v3.xlsx"

df = pd.read_csv(FILE, dtype=str).fillna("")

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

# 抽样
s_ft_high   = ft_high.sample(min(20, len(ft_high)),   random_state=42)
s_ab_high   = ab_high.sample(min(15, len(ab_high)),   random_state=42)
s_medium    = medium.sample(min(12, len(medium)),     random_state=42)
s_single_tf = single_tf.sample(min(3,  len(single_tf)), random_state=42)

sample = pd.concat([s_ft_high, s_ab_high, s_medium, s_single_tf], ignore_index=True)
sample = sample.sample(frac=1, random_state=99).reset_index(drop=True)  # shuffle
sample.insert(0, "no", range(1, len(sample)+1))
sample["stratum"] = (
    ["fulltext/high"] * len(s_ft_high) +
    ["abstract/high"] * len(s_ab_high) +
    ["medium"]        * len(s_medium)  +
    ["single_TF"]     * len(s_single_tf)
)

# 输出列
COLS = ["no", "pmid", "title", "source_cell", "target_cell",
        "factors", "factor_type", "confidence", "stratum",
        "source", "evidence_sentence",
        "correct", "comment"]

# 加空白标注列
sample["correct"] = ""
sample["comment"] = ""

out = sample[[c for c in COLS if c in sample.columns]]
# shuffle stratum after adding column
out = out.sample(frac=1, random_state=99).reset_index(drop=True)
out["no"] = range(1, len(out)+1)

out.to_excel(OUTPUT, index=False)
print(f"\n生成 {OUTPUT}，共 {len(out)} 条")
print("分层分布:")
print(out["stratum"].value_counts().to_string())
