"""
用3篇gold-standard论文测试新prompt效果
只读不写，不影响任何现有文件
"""

import os, json, time, re
from Bio import Entrez
from openai import OpenAI

Entrez.email = "xuanyichen888@gmail.com"
API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com"
MODEL    = "deepseek-v4-flash"

if not API_KEY:
    raise SystemExit("请先运行: export DEEPSEEK_API_KEY=sk-...")

# 从 extract_recipes.py 读取最新prompt
src    = open("extract_recipes.py").read()
PROMPT = re.search(r'SYSTEM_PROMPT = """\\\n(.*?)"""', src, re.DOTALL).group(1).strip()

# 3篇验证论文
VALIDATION_PMIDS = ["16904174", "34761218", "40210438"]
LABELS = {
    "16904174": "Yamanaka original (2006)",
    "34761218": "Taiji-reprogram",
    "40210438": "TFcomb",
}

# 抓摘要
print("正在从PubMed获取摘要...")
handle  = Entrez.efetch(db="pubmed", id=VALIDATION_PMIDS, rettype="xml", retmode="xml")
records = Entrez.read(handle)
handle.close()

papers = []
for article in records["PubmedArticle"]:
    m        = article["MedlineCitation"]
    pmid     = str(m["PMID"])
    title    = str(m["Article"]["ArticleTitle"])
    abstract = ""
    if "Abstract" in m["Article"]:
        t = m["Article"]["Abstract"]["AbstractText"]
        abstract = " ".join(str(x) for x in t) if isinstance(t, list) else str(t)
    papers.append({"pmid": pmid, "title": title, "abstract": abstract})

# 跑提取
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

for p in papers:
    label = LABELS.get(p["pmid"], "")
    print(f"\n{'='*65}")
    print(f"【{label}】 PMID: {p['pmid']}")
    print(f"Title: {p['title']}")

    msg  = f"PMID: {p['pmid']}\nTitle: {p['title']}\n\nAbstract:\n{p['abstract']}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user",   "content": msg},
        ],
        temperature=0.0,
        max_tokens=2048,
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    result = json.loads(raw.strip())

    print(f"has_recipe = {result['has_recipe']}  |  paper_type = {result.get('paper_type', '')}")

    entries = result.get("entries", [])
    if not entries:
        print("  → 没有提取到任何条目")
    for i, e in enumerate(entries, 1):
        print(f"\n  条目 {i}:")
        print(f"    source_cell    : {e.get('source_cell', '')}")
        print(f"    target_cell    : {e.get('target_cell', '')}")
        print(f"    factors        : {e.get('factors', '')}")
        print(f"    factor_type    : {e.get('factor_type', '')}")
        print(f"    species        : {e.get('species', '')}")
        print(f"    culture_cond   : {e.get('culture_condition', '')}")
        print(f"    confidence     : {e.get('confidence', '')}")
        print(f"    notes          : {e.get('notes', '')}")

    time.sleep(1)

print(f"\n{'='*65}")
print("测试完成，以上结果未写入任何文件。")
