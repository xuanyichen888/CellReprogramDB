"""
从 PubMed 获取期刊名称，补充到 recipes_master_v2.csv
输出: journals.csv (pmid, journal)
"""
import csv, time, json, os
from Bio import Entrez

Entrez.email = "xuanyichen888@gmail.com"
OUTPUT = "journals.csv"
CHECKPOINT = "journals_done.json"

def load_done():
    if os.path.exists(CHECKPOINT):
        return json.load(open(CHECKPOINT))
    return {}

def save_done(done):
    json.dump(done, open(CHECKPOINT, "w"))

def fetch_journals(pmids):
    result = {}
    handle = Entrez.efetch(db="pubmed", id=pmids, rettype="xml", retmode="xml")
    records = Entrez.read(handle)
    handle.close()
    for article in records["PubmedArticle"]:
        try:
            medline = article["MedlineCitation"]
            pmid = str(medline["PMID"])
            journal = str(medline["Article"]["Journal"]["Title"])
            result[pmid] = journal
        except Exception:
            pass
    return result

def main():
    import pandas as pd
    df = pd.read_csv("recipes_master_v2.csv", dtype=str).fillna("")
    pmids = df["pmid"].unique().tolist()
    print(f"总 PMID: {len(pmids)}")

    done = load_done()
    todo = [p for p in pmids if p not in done]
    print(f"已有: {len(done)}，待获取: {len(todo)}")

    BATCH = 100
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i+BATCH]
        try:
            journals = fetch_journals(batch)
            done.update(journals)
            save_done(done)
            print(f"  [{i+len(batch)}/{len(todo)}] 获取 {len(journals)} 条")
        except Exception as e:
            print(f"  批次错误: {e}")
        time.sleep(0.5)

    # 写出 journals.csv
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pmid", "journal"])
        for pmid, journal in done.items():
            w.writerow([pmid, journal])

    print(f"\n完成！共 {len(done)} 条期刊信息 → {OUTPUT}")

if __name__ == "__main__":
    main()
