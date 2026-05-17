"""
检测 bioRxiv preprint + peer-reviewed 重复：
- 同一篇论文先发 bioRxiv（较小 PMID）后发期刊（较大 PMID），标题高度相似
- 策略：按 title 做归一化后分组，同组保留 year 最大（最晚，即 peer-reviewed）的 PMID，
  其余 PMID 的所有条目标记 is_duplicate=True
"""
import pandas as pd, shutil, re
from difflib import SequenceMatcher

FILE = "recipes_master_v2.csv"


def normalize_title(t: str) -> str:
    t = t.lower().strip()
    t = re.sub(r'[^a-z0-9 ]', '', t)
    t = re.sub(r'\s+', ' ', t)
    return t


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")

    # 每个 PMID 的 (title, year)
    pmid_info = (
        df[["pmid", "title", "year"]]
        .drop_duplicates("pmid")
        .copy()
    )
    pmid_info["title_norm"] = pmid_info["title"].apply(normalize_title)
    pmid_info["year_int"]   = pd.to_numeric(pmid_info["year"], errors="coerce").fillna(0).astype(int)

    records = pmid_info.to_dict("records")
    n = len(records)

    # O(n²) 但 n~2824，可接受
    groups = {}   # pmid → group_id
    group_id = 0
    for i in range(n):
        for j in range(i + 1, n):
            a, b = records[i], records[j]
            if not a["title_norm"] or not b["title_norm"]:
                continue
            if similar(a["title_norm"], b["title_norm"]) >= 0.92:
                # Same paper
                ga = groups.get(a["pmid"])
                gb = groups.get(b["pmid"])
                if ga is None and gb is None:
                    groups[a["pmid"]] = group_id
                    groups[b["pmid"]] = group_id
                    group_id += 1
                elif ga is None:
                    groups[a["pmid"]] = gb
                elif gb is None:
                    groups[b["pmid"]] = ga
                # else already grouped

    if not groups:
        print("未检测到 bioRxiv 重复对")
        return

    # 按 group 找出需要丢弃的 PMID（保留 year 最大的，年份相同保留 PMID 最大的）
    from collections import defaultdict
    gid_to_pmids = defaultdict(list)
    for pmid, gid in groups.items():
        gid_to_pmids[gid].append(pmid)

    discard_pmids = set()
    print(f"检测到 {len(gid_to_pmids)} 个 bioRxiv 重复组：")
    for gid, pmids in gid_to_pmids.items():
        info = [r for r in records if r["pmid"] in pmids]
        info.sort(key=lambda x: (x["year_int"], int(x["pmid"])), reverse=True)
        keep = info[0]["pmid"]
        drop = [x["pmid"] for x in info[1:]]
        discard_pmids.update(drop)
        print(f"  保留 PMID {keep} ({info[0]['year_int']}), 丢弃 {drop}")

    # 标记
    mask = df["pmid"].isin(discard_pmids)
    df.loc[mask, "is_duplicate"] = True
    print(f"\n额外标记 {mask.sum()} 条为 is_duplicate=True（来自 {len(discard_pmids)} 个 preprint PMID）")
    print(f"总 is_duplicate=True: {(df['is_duplicate'].astype(str).str.lower()=='true').sum()} 条")

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"保存至 {FILE}")


if __name__ == "__main__":
    main()
