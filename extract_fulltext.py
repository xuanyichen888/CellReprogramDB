"""
用全文（methods + results）重新提取recipe，补充abstract提取的不足
输入: fulltext.csv + recipes_v2_final.csv（已有abstract结果）
输出: recipes_fulltext.csv（新发现的或补充的条目）
"""

import csv, json, os, time, re
from openai import OpenAI

API_KEY    = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL   = "https://api.deepseek.com"
MODEL      = "deepseek-v4-flash"
OUTPUT     = "recipes_fulltext.csv"
CHECKPOINT = "checkpoint_fulltext.json"
SLEEP      = 1.5   # 全文更长，给多一点间隔

OUTPUT_FIELDS = [
    "pmid", "source_cell", "target_cell",
    "factors", "factor_type",
    "species", "culture_condition",
    "confidence", "paper_type", "notes",
]

SYSTEM_PROMPT = """\
You are a biomedical expert in cell reprogramming. Extract reprogramming recipes from the Methods and Results sections of research papers.

Respond ONLY with valid JSON — no markdown — using this schema:
{
  "has_recipe": true/false,
  "paper_type": "research|review|other",
  "entries": [
    {
      "source_cell": "...",
      "target_cell": "...",
      "factors": "...",
      "factor_type": "...",
      "species": "...",
      "culture_condition": "...",
      "confidence": "high|medium|low",
      "notes": "..."
    }
  ]
}

Rules:
- has_recipe=true if any specific cell-type conversion is described using identifiable factors
- Extract ALL factors exhaustively — read the entire text, do not stop at the first list
- factors: all factors named, comma-separated, exactly as written
- factor_type: one label per factor in the same order — TF | small_molecule | miRNA | knockdown | cytokine | other
- source_cell and target_cell: as specific as possible (e.g. "dermal fibroblast" not "fibroblast")
- species: "human", "mouse", "rat", or other; list all tested
- culture_condition: specific medium or culture system if named (e.g. "N2B27", "feeder-free")
- confidence: "high" = source, target, and factors all explicitly stated in experimental context
- notes: efficiency, in vivo/vitro, key result — max 20 words
- If a paper tests multiple source→target pairs, create one entry per pair
- Focus on experimentally validated conversions; ignore predictions or computational results
"""


def load_checkpoint():
    if os.path.exists(CHECKPOINT):
        return set(json.load(open(CHECKPOINT)))
    return set()

def save_checkpoint(done):
    json.dump(list(done), open(CHECKPOINT, "w"))

def append_rows(rows):
    exists = os.path.exists(OUTPUT)
    with open(OUTPUT, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        if not exists:
            w.writeheader()
        w.writerows(rows)

def call_api(client, pmid, methods, results):
    text = f"PMID: {pmid}\n\n[METHODS SECTION]\n{methods}\n\n[RESULTS SECTION]\n{results}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": text},
        ],
        temperature=0.0,
        max_tokens=2048,
    )
    return resp.choices[0].message.content.strip()

def parse(raw):
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def main():
    if not API_KEY:
        raise SystemExit("请先运行: export DEEPSEEK_API_KEY=sk-...")

    if not os.path.exists("fulltext.csv"):
        raise SystemExit("请先运行 fetch_fulltext.py 生成 fulltext.csv")

    papers    = list(csv.DictReader(open("fulltext.csv", encoding="utf-8")))
    # 过滤掉没有内容的
    papers    = [p for p in papers if p["methods_text"].strip() or p["results_text"].strip()]
    done      = load_checkpoint()
    todo      = [p for p in papers if p["pmid"] not in done]

    print(f"全文可用: {len(papers)} 篇，待处理: {len(todo)} 篇\n")

    client    = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    extracted = 0
    skipped   = 0
    errors    = 0

    for i, paper in enumerate(todo, 1):
        pmid    = paper["pmid"]
        methods = paper.get("methods_text", "")
        results = paper.get("results_text", "")

        print(f"[{i}/{len(todo)}] PMID {pmid} ... ", end="", flush=True)

        try:
            raw    = call_api(client, pmid, methods, results)
            result = parse(raw)
        except json.JSONDecodeError as e:
            print(f"JSON错误: {e}")
            errors += 1
            done.add(pmid)
            save_checkpoint(done)
            time.sleep(SLEEP)
            continue
        except Exception as e:
            print(f"API错误: {e}")
            errors += 1
            time.sleep(5)
            continue

        if not result.get("has_recipe") or not result.get("entries"):
            print("no recipe")
            skipped += 1
        else:
            paper_type = result.get("paper_type", "")
            rows = [{
                "pmid":              pmid,
                "source_cell":       e.get("source_cell", ""),
                "target_cell":       e.get("target_cell", ""),
                "factors":           e.get("factors", ""),
                "factor_type":       e.get("factor_type", ""),
                "species":           e.get("species", ""),
                "culture_condition": e.get("culture_condition", ""),
                "confidence":        e.get("confidence", ""),
                "paper_type":        paper_type,
                "notes":             e.get("notes", ""),
            } for e in result["entries"]]
            append_rows(rows)
            extracted += len(rows)
            print(f"提取 {len(rows)} 条")

        done.add(pmid)
        save_checkpoint(done)
        time.sleep(SLEEP)

    print(f"\n完成。提取: {extracted} 条 | 跳过: {skipped} | 错误: {errors}")
    print(f"结果保存至 {OUTPUT}")


if __name__ == "__main__":
    main()
