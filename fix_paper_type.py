"""
用 PubMed 官方 PublicationType 字段覆盖 LLM 判断的 paper_type。

映射规则：
  含 Review / Systematic Review / Meta-Analysis  → "review"
  含 Comment / Editorial / Letter / Retraction   → "other"
  其余（Journal Article / Clinical Trial 等）    → "research"
"""

import csv, json, os, time
import pandas as pd
from Bio import Entrez

Entrez.email = "xuanyichen888@gmail.com"

FILE       = "recipes_master_v2.csv"
CHECKPOINT = "pubtype_done.json"
BATCH      = 100


def load_done():
    if os.path.exists(CHECKPOINT):
        return json.load(open(CHECKPOINT))
    return {}


def save_done(done):
    json.dump(done, open(CHECKPOINT, "w"))


def classify(pub_types: list[str]) -> str:
    types_lower = [t.lower() for t in pub_types]
    if any(t in types_lower for t in ["review", "systematic review", "meta-analysis"]):
        return "review"
    if any(t in types_lower for t in ["comment", "editorial", "letter",
                                       "retraction of publication",
                                       "published erratum", "news"]):
        return "other"
    return "research"


def fetch_pubtypes(pmids: list[str]) -> dict[str, str]:
    result = {}
    handle = Entrez.efetch(db="pubmed", id=pmids, rettype="xml", retmode="xml")
    records = Entrez.read(handle)
    handle.close()
    for art in records["PubmedArticle"]:
        try:
            pmid = str(art["MedlineCitation"]["PMID"])
            pub_types = [str(pt) for pt in
                         art["MedlineCitation"]["Article"].get("PublicationTypeList", [])]
            result[pmid] = classify(pub_types)
        except Exception:
            pass
    return result


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")
    pmids = df["pmid"].unique().tolist()
    print(f"总 PMID: {len(pmids)}")

    done = load_done()
    todo = [p for p in pmids if p not in done]
    print(f"已有: {len(done)}，待获取: {len(todo)}")

    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        try:
            result = fetch_pubtypes(batch)
            done.update(result)
            save_done(done)
            print(f"  [{i + len(batch)}/{len(todo)}] 获取 {len(result)} 条")
        except Exception as e:
            print(f"  批次错误: {e}")
        time.sleep(0.4)

    # 覆盖 paper_type 列
    before = df["paper_type"].value_counts().to_dict()
    df["paper_type"] = df["pmid"].map(done).fillna(df["paper_type"])
    after  = df["paper_type"].value_counts().to_dict()

    import shutil
    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")

    print("\n=== paper_type 变化 ===")
    for k in ["research", "review", "other"]:
        b = before.get(k, 0)
        a = after.get(k, 0)
        print(f"  {k:10s}: {b} → {a}  (Δ {a-b:+d})")
    print(f"\n保存至 {FILE}（备份：{FILE}.bak）")


if __name__ == "__main__":
    main()
