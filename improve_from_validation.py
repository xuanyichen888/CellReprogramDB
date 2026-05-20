"""
根据 verification_sample_v3 抽样结果，批量改进整体数据库。

发现的4类系统性问题：
1. evidence_sentence 引用前人工作 → 标 needs_review
2. evidence_sentence 是方法句（不含结果）→ 标 needs_review
3. evidence_sentence 含阴性结果 → 标 remove
4. medium confidence + abstract + factors 模糊 → 降 confidence / 标 needs_review
"""

import re, shutil, pandas as pd

FILE = "recipes_master_v2.csv"

# ── 模式定义 ──────────────────────────────────────────────────────────────

# 1. 引用前人工作
PRIOR_WORK_PATTERNS = [
    r'\bpreviously (reported|showed|demonstrated|described|published|shown)\b',
    r'\brecently[,\s]+(we|our group)\b',
    r'\bwe (previously|earlier|recently) (showed|reported|demonstrated|found|described)\b',
    r'\bprior (work|study|studies|publication)\b',
    r'\bin (our|a) previous (study|work|publication|report)\b',
    r'\bhas been (reported|described|shown|demonstrated) (previously|before)\b',
    r'\bwas (previously|recently) (reported|described|shown)\b',
]

# 2. 纯方法句（不含结果的描述）
METHODS_PATTERNS = [
    r'^(cells?|fibroblasts?|stem cells?|iPSCs?|MEFs?)\s+were\s+(isolated|cultured|maintained|seeded|collected|derived|established)',
    r'\bwere used to (investigate|examine|test|study|determine|evaluate|assess)\b',
    r'\bwere (transduced|transfected|infected|treated)\s+with\b(?!.*(?:result|showed|demonstrated|induced|generated|produced|converted|reprogrammed|gave rise))',
    r'\bto (investigate|examine|test|study|determine|explore)\s+whether\b',
    r'^(here|in this study)[,\s]+we (investigate|examine|present) the (generation|development|establishment|protocol)',
    r'^(here|in this study)[,\s]+we (describe|report) the (development|establishment|protocol)',
]

# 3. 阴性结果
NEGATIVE_PATTERNS = [
    r'\bdoes? not\b.*\b(reprogramm|iPSC|colony|colonies|convert|induce|generate)\b',
    r'\b(fail(ed)?|unable|could not|cannot|did not)\b.*\b(reprogramm|convert|induce|generate|produce)\b',
    r'\b(no|lack of)\b.*\b(iPSC|colony|colonies|reprogramming|conversion)\b',
    r'\b(inefficient|ineffective)\b.*\b(reprogramm|convert)\b',
    r'\bnot elicit\b',
    r'\bnot (generate|produce|form|yield)\b.*\b(iPSC|colony|neuron|cardiomyocyte)\b',
]

# 4. 模糊因子（medium + abstract）
VAGUE_FACTOR_PATTERNS = [
    r'^defined factors?$',
    r'^(defined|classical|standard|conventional|canonical) (reprogramming )?factors?$',
    r'^(reprogramming|pluripotency) factors?$',
    r'^(small molecules?|chemical(s)?)$',
    r'^(growth factors?|cytokines?)$',
    r'^(mirna|microrna) mimics?$',
    r'^(guide rnas?|grnas?)$',
    r'^transcription factors?$',
    r'^(yamanaka|thomson|bam|gmt) factors?$',
    r'^not specified( in text)?$',
    r'^unknown$',
]

def matches_any(text, patterns, flags=re.IGNORECASE):
    text = str(text).strip()
    return any(re.search(p, text, flags) for p in patterns)

def is_vague_factors(factors_str):
    parts = [f.strip() for f in str(factors_str).split(',')]
    return len(parts) == 1 and matches_any(parts[0], VAGUE_FACTOR_PATTERNS)


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")

    # 确保必要列存在
    for col in ["validation_needs_review", "validation_action", "validation_resolution", "validation_notes"]:
        if col not in df.columns:
            df[col] = ""

    # 只处理默认显示的条目（排除已标记的）
    already_handled = (
        (df["is_duplicate"].astype(str).str.lower() == "true") |
        (df["validation_action"].isin(["remove", "hide_incomplete_recipe", "hide_single_tf"]))
    )
    active = ~already_handled

    ev = df["evidence_sentence"].fillna("")

    # ── Fix 1: 引用前人工作 ───────────────────────────────────────────────
    mask1 = active & ev.apply(lambda x: matches_any(x, PRIOR_WORK_PATTERNS))
    n1 = mask1.sum()
    df.loc[mask1, "validation_needs_review"] = "True"
    df.loc[mask1, "validation_resolution"] = "evidence_cites_prior_work"
    df.loc[mask1, "validation_notes"] = df.loc[mask1, "validation_notes"].apply(
        lambda x: (x + " | " if x else "") +
        "Evidence sentence cites prior work rather than this paper's own result."
    )
    print(f"Fix 1 - prior work evidence:  {n1} entries flagged needs_review")

    # ── Fix 2: 纯方法句 ──────────────────────────────────────────────────
    mask2 = active & ~mask1 & ev.apply(lambda x: matches_any(x, METHODS_PATTERNS))
    n2 = mask2.sum()
    df.loc[mask2, "validation_needs_review"] = "True"
    df.loc[mask2, "validation_resolution"] = "evidence_is_methods_sentence"
    df.loc[mask2, "validation_notes"] = df.loc[mask2, "validation_notes"].apply(
        lambda x: (x + " | " if x else "") +
        "Evidence sentence describes methods/setup, not a result."
    )
    print(f"Fix 2 - methods-only evidence: {n2} entries flagged needs_review")

    # ── Fix 3: 疑似阴性结果 → 标 needs_review，人工确认后再决定是否 remove ─────
    # NOTE: negative patterns can fire on false positives (e.g., "no gene was targeted
    # more than once" or "failed" referring to the control arm). Mark needs_review,
    # not remove, so each entry is confirmed individually.
    mask3 = active & ev.apply(lambda x: matches_any(x, NEGATIVE_PATTERNS))
    n3 = mask3.sum()
    df.loc[mask3, "validation_needs_review"] = "True"
    df.loc[mask3, "validation_resolution"] = "possible_negative_needs_review"
    df.loc[mask3, "validation_notes"] = df.loc[mask3, "validation_notes"].apply(
        lambda x: (x + " | " if x else "") +
        "Evidence sentence may describe a failed/negative result — requires manual confirmation before removal."
    )
    print(f"Fix 3 - possible negatives:    {n3} entries flagged needs_review")

    # ── Fix 4: medium + abstract + 模糊因子 ─────────────────────────────
    mask4 = (
        active & ~mask1 & ~mask2 & ~mask3 &
        (df["confidence"] == "medium") &
        (df["source"] == "abstract") &
        df["factors"].apply(is_vague_factors)
    )
    n4 = mask4.sum()
    df.loc[mask4, "validation_action"] = "hide_incomplete_recipe"
    df.loc[mask4, "validation_needs_review"] = "False"
    df.loc[mask4, "validation_resolution"] = "medium_abstract_vague_factors"
    df.loc[mask4, "validation_notes"] = df.loc[mask4, "validation_notes"].apply(
        lambda x: (x + " | " if x else "") +
        "Medium-confidence abstract entry with unspecified factors; hidden from default view."
    )
    print(f"Fix 4 - medium+abstract+vague: {n4} entries hidden")

    # ── 统计 ─────────────────────────────────────────────────────────────
    print()
    total_review = (df["validation_needs_review"].astype(str).str.lower() == "true").sum()
    total_remove = (df["validation_action"] == "remove").sum()
    total_hidden = df["validation_action"].isin(["remove","hide_incomplete_recipe","hide_single_tf"]).sum()
    total_dup    = (df["is_duplicate"].astype(str).str.lower() == "true").sum()

    # 默认显示数（模拟 app 默认过滤）
    shown = df[
        (df["is_duplicate"].astype(str).str.lower() != "true") &
        (~df["validation_action"].isin(["remove", "hide_incomplete_recipe", "hide_single_tf"])) &
        (df["factors"] != "not specified") &
        (df["single_tf_flag"].astype(str).str.lower() != "true") &
        (df["paper_type"] == "research") &
        (df["confidence"].isin(["high", "medium"]))
    ]
    print(f"validation_needs_review=True:  {total_review}")
    print(f"validation_action=remove:      {total_remove}")
    print(f"is_duplicate=True:             {total_dup}")
    print(f"默认显示条目数:                {len(shown)}")

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"\n保存至 {FILE}")


if __name__ == "__main__":
    main()
