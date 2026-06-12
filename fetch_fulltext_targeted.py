"""
针对指定 PMID 列表补抓全文，追加写入 fulltext.csv
用法: python3 fetch_fulltext_targeted.py pmids_need_fulltext.txt
"""

import csv, sys, time, re, os
import requests

FULLTEXT_CSV = "fulltext.csv"
PMC_LIST     = "pmc_available.csv"
CHECKPOINT   = "fulltext_done.txt"
SEARCH_URL   = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MAX_CHARS    = 10000
BATCH        = 50


def load_done():
    if os.path.exists(CHECKPOINT):
        return set(open(CHECKPOINT).read().splitlines())
    return set()

def mark_done(pmid):
    with open(CHECKPOINT, "a") as f:
        f.write(pmid + "\n")

def extract_sections(xml):
    tag  = re.compile(r'<[^>]+>')
    sec  = re.compile(r'<sec\b[^>]*>.*?</sec>', re.DOTALL | re.I)
    titl = re.compile(r'<title[^>]*>(.*?)</title>', re.DOTALL | re.I)
    para = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL | re.I)
    methods, results = [], []
    for s in sec.finditer(xml):
        s_text = s.group()
        tm = titl.search(s_text)
        if not tm: continue
        title = tag.sub('', tm.group(1)).strip().lower()
        content = " ".join(tag.sub('', p.group(1)) for p in para.finditer(s_text)).strip()
        if not content: continue
        if any(k in title for k in ["method","material","experimental","procedure","protocol"]):
            methods.append(content)
        elif any(k in title for k in ["result","finding","outcome","data"]):
            results.append(content)
    return " ".join(methods)[:MAX_CHARS], " ".join(results)[:MAX_CHARS]

def find_pmc_for_pmids(pmids):
    """查询哪些 PMID 在 Europe PMC 有开放全文"""
    # 先从已有 pmc_available.csv 中找
    known = {}
    if os.path.exists(PMC_LIST):
        for row in csv.DictReader(open(PMC_LIST, encoding="utf-8")):
            known[row["pmid"]] = row["pmcid"]

    still_unknown = [p for p in pmids if p not in known]
    print(f"已缓存 PMCID: {len(pmids)-len(still_unknown)} 篇，需查询: {len(still_unknown)} 篇")

    if still_unknown:
        new_found = []
        for i in range(0, len(still_unknown), BATCH):
            batch = still_unknown[i:i+BATCH]
            query = "(" + " OR ".join(f"EXT_ID:{p}" for p in batch) + ") AND SRC:MED"
            for attempt in range(3):
                try:
                    r = requests.get(SEARCH_URL,
                                     params={"query": query, "resultType": "core",
                                             "format": "json", "pageSize": BATCH},
                                     timeout=20)
                    data = r.json().get("resultList", {}).get("result", [])
                    for item in data:
                        pmid  = item.get("pmid", "")
                        pmcid = item.get("pmcid", "")
                        if pmid and pmcid and item.get("inEPMC") == "Y":
                            known[pmid] = pmcid
                            new_found.append({"pmid": pmid, "pmcid": pmcid})
                    break
                except Exception as e:
                    if attempt == 2: print(f"  查询失败: {e}")
                    else: time.sleep(3)
            time.sleep(0.5)
        # 追加写入 pmc_available.csv
        if new_found:
            file_exists = os.path.exists(PMC_LIST)
            with open(PMC_LIST, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["pmid","pmcid"])
                if not file_exists: w.writeheader()
                w.writerows(new_found)
            print(f"新找到 {len(new_found)} 篇有全文")

    return {p: known[p] for p in pmids if p in known}

def download_fulltext(pmcid):
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 200:
                return r.text
            return None
        except requests.exceptions.Timeout:
            if attempt < 2: time.sleep(5)
            else: return None
        except Exception:
            return None


def main():
    target_file = sys.argv[1] if len(sys.argv) > 1 else "pmids_need_fulltext.txt"
    pmids = open(target_file).read().splitlines()
    pmids = [p.strip() for p in pmids if p.strip()]
    print(f"目标 PMID: {len(pmids)} 篇")

    # 已经在 fulltext.csv 里的跳过
    existing = set()
    if os.path.exists(FULLTEXT_CSV):
        for row in csv.DictReader(open(FULLTEXT_CSV, encoding="utf-8")):
            existing.add(row["pmid"])
    pmids = [p for p in pmids if p not in existing]
    print(f"需要补抓: {len(pmids)} 篇 (已有 {len(existing)} 篇跳过)\n")

    if not pmids:
        print("全部已存在，无需补抓。")
        return

    # 查询 PMCID
    print("Step 1: 查询 Europe PMC 全文可用性...")
    pmc_map = find_pmc_for_pmids(pmids)
    print(f"找到全文: {len(pmc_map)}/{len(pmids)} 篇\n")

    # 下载全文
    done = load_done()
    todo = [(pmid, pmcid) for pmid, pmcid in pmc_map.items() if pmid not in done]
    print(f"Step 2: 下载全文，待处理 {len(todo)} 篇...")

    with open(FULLTEXT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["pmid","pmcid","methods_text","results_text"])
        success = 0
        for i, (pmid, pmcid) in enumerate(todo, 1):
            print(f"[{i}/{len(todo)}] PMID {pmid} (PMC{pmcid}) ... ", end="", flush=True)
            xml = download_fulltext(pmcid)
            if xml:
                methods, results = extract_sections(xml)
                if methods or results:
                    writer.writerow({"pmid": pmid, "pmcid": pmcid,
                                     "methods_text": methods, "results_text": results})
                    f.flush()
                    success += 1
                    print(f"OK (methods:{len(methods)}, results:{len(results)})")
                else:
                    print("无法解析章节")
            else:
                print("下载失败/无开放全文")
            mark_done(pmid)
            time.sleep(0.8)

    print(f"\n完成！成功提取 {success}/{len(todo)} 篇 → {FULLTEXT_CSV}")
    print(f"没有开放全文: {len(pmids) - len(pmc_map)} 篇（闭源期刊）")


if __name__ == "__main__":
    main()
