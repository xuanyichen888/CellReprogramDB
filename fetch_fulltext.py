"""
Step 2b: 用Europe PMC API查找并下载开放全文
输出: fulltext.csv (pmid, pmcid, methods_text, results_text)
"""

import csv, time, re, os, warnings
import requests
warnings.filterwarnings("ignore")

SEARCH_URL   = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
FULLTEXT_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
OUTPUT       = "fulltext.csv"
PMC_LIST     = "pmc_available.csv"   # 先查可用列表
CHECKPOINT   = "fulltext_done.txt"
MAX_CHARS    = 10000                  # 每个章节最多字符数
BATCH        = 50                    # 每批查询篇数


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def load_done():
    if os.path.exists(CHECKPOINT):
        return set(open(CHECKPOINT).read().splitlines())
    return set()

def mark_done(pmid):
    with open(CHECKPOINT, "a") as f:
        f.write(pmid + "\n")

def extract_sections(xml):
    """从PMC XML提取methods和results章节文字"""
    tag  = re.compile(r'<[^>]+>')
    sec  = re.compile(r'<sec\b[^>]*>.*?</sec>', re.DOTALL | re.I)
    titl = re.compile(r'<title[^>]*>(.*?)</title>', re.DOTALL | re.I)
    para = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL | re.I)

    methods, results = [], []
    for s in sec.finditer(xml):
        s_text  = s.group()
        tm      = titl.search(s_text)
        if not tm: continue
        title   = tag.sub('', tm.group(1)).strip().lower()
        content = " ".join(tag.sub('', p.group(1)) for p in para.finditer(s_text)).strip()
        if not content: continue
        if any(k in title for k in ["method","material","experimental","procedure","protocol"]):
            methods.append(content)
        elif any(k in title for k in ["result","finding","outcome","data"]):
            results.append(content)

    return " ".join(methods)[:MAX_CHARS], " ".join(results)[:MAX_CHARS]


# ── Step 1: 查哪些论文有PMC全文 ───────────────────────────────────────────────

def find_pmc_papers(pmids):
    available = []
    for i in range(0, len(pmids), BATCH):
        batch     = pmids[i:i+BATCH]
        query     = "(" + " OR ".join(f"EXT_ID:{p}" for p in batch) + ") AND SRC:MED"
        for attempt in range(3):
            try:
                r    = requests.get(SEARCH_URL,
                                    params={"query": query, "resultType":"core",
                                            "format":"json", "pageSize": BATCH},
                                    timeout=20)
                data = r.json().get("resultList",{}).get("result",[])
                for item in data:
                    pmid  = item.get("pmid","")
                    pmcid = item.get("pmcid","")
                    if pmid and pmcid and item.get("inEPMC") == "Y":
                        available.append({"pmid": pmid, "pmcid": pmcid})
                break
            except Exception as e:
                if attempt == 2: print(f"  查询失败: {e}")
                else: time.sleep(3)
        time.sleep(0.5)
        if (i // BATCH) % 10 == 0:
            print(f"  已查 {min(i+BATCH, len(pmids))}/{len(pmids)}，找到全文: {len(available)}")
    return available


# ── Step 2: 下载并解析全文 ────────────────────────────────────────────────────

def download_fulltext(pmcid):
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 200:
                return r.text
            return None
        except requests.exceptions.Timeout:
            if attempt < 2:
                time.sleep(5)
            else:
                return None
        except Exception:
            return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    papers = list(csv.DictReader(open("papers.csv", encoding="utf-8")))
    pmids  = [p["pmid"] for p in papers]
    print(f"总论文数: {len(pmids)}")

    # Step 1: 找有全文的论文（增量：只查新增的pmid）
    if os.path.exists(PMC_LIST):
        pmc_papers = list(csv.DictReader(open(PMC_LIST, encoding="utf-8")))
        existing_pmc_pmids = {p["pmid"] for p in pmc_papers}
        print(f"已有PMC列表: {len(pmc_papers)} 篇")
        # 只查新增的pmid
        new_pmids = [p for p in pmids if p not in existing_pmc_pmids]
        if new_pmids:
            print(f"查询新增 {len(new_pmids)} 篇的全文可用性...")
            new_pmc = find_pmc_papers(new_pmids)
            pmc_papers.extend(new_pmc)
            # 追加写入 pmc_available.csv
            with open(PMC_LIST, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["pmid","pmcid"])
                w.writerows(new_pmc)
            print(f"新找到 {len(new_pmc)} 篇有全文，总计 {len(pmc_papers)} 篇")
        else:
            print("没有新论文需要查询")
    else:
        print("\nStep 1: 查询PMC全文可用性...")
        pmc_papers = find_pmc_papers(pmids)
        with open(PMC_LIST, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["pmid","pmcid"])
            w.writeheader(); w.writerows(pmc_papers)
        print(f"找到 {len(pmc_papers)} 篇有全文，保存至 {PMC_LIST}")

    # Step 2: 下载全文
    done = load_done()
    todo = [p for p in pmc_papers if p["pmid"] not in done]
    print(f"\nStep 2: 下载全文，待处理 {len(todo)} 篇...")

    file_exists = os.path.exists(OUTPUT)
    out_f  = open(OUTPUT, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_f, fieldnames=["pmid","pmcid","methods_text","results_text"])
    if not file_exists: writer.writeheader()

    success = 0
    for i, p in enumerate(todo, 1):
        pmid  = p["pmid"]
        pmcid = p["pmcid"]
        print(f"[{i}/{len(todo)}] PMID {pmid} (PMC{pmcid}) ... ", end="", flush=True)

        xml = download_fulltext(pmcid)
        if xml:
            methods, results = extract_sections(xml)
            if methods or results:
                writer.writerow({"pmid": pmid, "pmcid": pmcid,
                                 "methods_text": methods, "results_text": results})
                success += 1
                print(f"OK (methods:{len(methods)}chars, results:{len(results)}chars)")
            else:
                print("无法解析章节")
        else:
            print("下载失败")

        mark_done(pmid)
        time.sleep(0.8)

    out_f.close()
    print(f"\n完成！成功提取 {success}/{len(todo)} 篇全文 → {OUTPUT}")


if __name__ == "__main__":
    main()
