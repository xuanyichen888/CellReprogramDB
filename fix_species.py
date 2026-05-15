"""
两步填补 species 空值：
  Step 1 — 规则推断：从 source_cell / target_cell 名称推断物种
  Step 2 — 同 PMID 投票：用同篇论文其他条目中 species 的众数填补剩余空值
"""
import pandas as pd, shutil, re

FILE = "recipes_master_v2.csv"

# (正则, 物种)  — 匹配 source_cell 或 target_cell（忽略大小写）
SPECIES_RULES = [
    (r'\bmouse\b|\bmurine\b|\bmef\b',          'mouse'),
    (r'\bhuman\b|\bhesc\b|\bhipsc\b|\bhdf\b',    'human'),
    (r'\brat\b',                                'rat'),
    (r'\bbovine\b|\bcow\b',                     'bovine'),
    (r'\bporcine\b|\bpig\b|\bswine\b',          'porcine'),
    (r'\bzebrafish\b',                          'zebrafish'),
]
COMPILED = [(re.compile(p, re.IGNORECASE), s) for p, s in SPECIES_RULES]


def infer_species(source: str, target: str) -> str:
    for text in [source, target]:
        for pat, sp in COMPILED:
            if pat.search(text):
                return sp
    return ""


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")
    before = (df["species"] == "").sum()

    # Step 1: rule-based
    mask = df["species"] == ""
    df.loc[mask, "species"] = df[mask].apply(
        lambda r: infer_species(r["source_cell"], r["target_cell"]), axis=1
    )
    after_step1 = (df["species"] == "").sum()
    print(f"Step 1 规则推断：{before - after_step1} 条填补")

    # Step 1b: infer from title + evidence_sentence
    mask = df["species"] == ""
    def infer_from_text(r):
        text = " ".join([r.get("title",""), r.get("evidence_sentence","")])
        for pat, sp in COMPILED:
            if pat.search(text):
                return sp
        return ""
    df.loc[mask, "species"] = df[mask].apply(infer_from_text, axis=1)
    after_step1b = (df["species"] == "").sum()
    print(f"Step 1b title推断：{after_step1 - after_step1b} 条填补")
    after_step1 = after_step1b

    # Step 2: same-PMID majority vote
    def pmid_majority(group):
        filled = group[group != ""]
        if len(filled) == 0:
            return group
        majority = filled.mode()[0]
        return group.apply(lambda x: majority if x == "" else x)

    df["species"] = df.groupby("pmid")["species"].transform(pmid_majority)
    after_step2 = (df["species"] == "").sum()
    print(f"Step 2 同PMID投票：{after_step1 - after_step2} 条填补")
    print(f"仍为空：{after_step2} 条")

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")

    print(f"\nspecies 分布（修复后）:")
    print(df["species"].value_counts().to_string())
    print(f"\n保存至 {FILE}")


if __name__ == "__main__":
    main()
