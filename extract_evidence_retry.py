"""
对 evidence_sentence 为空的条目用更宽松的 prompt 重新提取。

输入: recipes_master_v2.csv
默认: dry-run —— 只把提议的 evidence 写到 qa_outputs/evidence_retry_preview.csv，
      不动主表。
--apply: 才把结果写回 recipes_master_v2.csv（先 .bak 备份）。
"""

import argparse, csv, json, os, time, shutil
from pathlib import Path
from openai import OpenAI

API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com"
MODEL    = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
FILE     = "recipes_master_v2.csv"
PREVIEW  = "qa_outputs/evidence_retry_preview.csv"
SLEEP    = 0.8

SYSTEM_PROMPT = """\
You are a biomedical text mining expert.

Given a reprogramming recipe and the original text, find the most relevant sentence or phrase that provides evidence for this conversion.

Respond ONLY with valid JSON:
{"evidence_sentence": "..."}

Rules:
- Pick the sentence most relevant to this specific cell conversion
- It does NOT need to mention all three elements (source, target, factors) — pick the best available
- Prefer sentences that mention at least one of: source cell, target cell, or key factors
- Copy the sentence exactly from the text, do not paraphrase
- If truly nothing is relevant, return ""
- One sentence only
"""

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write results back to recipes_master_v2.csv. Default is dry-run (preview only).")
    args = parser.parse_args()

    if not API_KEY:
        raise SystemExit("请先 export DEEPSEEK_API_KEY=sk-...")

    rows      = list(csv.DictReader(open(FILE, encoding="utf-8")))
    abstracts = {p["pmid"]: p["abstract"]
                 for p in csv.DictReader(open("papers.csv", encoding="utf-8"))}
    fulltext_map = {}
    if os.path.exists("fulltext.csv"):
        for r in csv.DictReader(open("fulltext.csv", encoding="utf-8")):
            fulltext_map[r["pmid"]] = r["methods_text"] + " " + r["results_text"]

    empty_idx = [i for i, r in enumerate(rows) if not r["evidence_sentence"].strip()]
    print(f"需要重试: {len(empty_idx)} 条")

    client  = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    updated = 0
    proposed = []

    for n, i in enumerate(empty_idx, 1):
        r    = rows[i]
        pmid = r["pmid"]

        text = (fulltext_map.get(pmid,"") if r.get("source")=="fulltext"
                else abstracts.get(pmid,""))[:12000]

        if not text.strip():
            continue

        print(f"[{n}/{len(empty_idx)}] PMID {pmid} {r['source_cell'][:18]}→{r['target_cell'][:18]} ... ",
              end="", flush=True)

        user_msg = (
            f"Recipe:\n"
            f"  source_cell: {r['source_cell']}\n"
            f"  target_cell: {r['target_cell']}\n"
            f"  factors: {r['factors'][:100]}\n\n"
            f"Original text:\n{text}"
        )

        ev = ""
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role":"system","content":SYSTEM_PROMPT},
                              {"role":"user","content":user_msg}],
                    temperature=0.0, max_tokens=8192,
                )
                content = resp.choices[0].message.content
                if not content:
                    if attempt < 2:
                        time.sleep(2)
                        continue
                    raise ValueError("API连续返回空内容")
                raw = content.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"): raw = raw[4:]
                ev = json.loads(raw.strip()).get("evidence_sentence","")
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(2)
                else:
                    print(f"错误: {e}")
                    ev = ""

        if not ev.strip():
            continue
        rows[i]["evidence_sentence"] = ev
        proposed.append({"pmid": pmid, "source_cell": r["source_cell"],
                         "target_cell": r["target_cell"], "factors": r["factors"],
                         "proposed_evidence_sentence": ev})
        updated += 1
        preview = ev[:70]+"..." if len(ev)>70 else ev
        print(f"✓  \"{preview}\"")
        time.sleep(SLEEP)

    # 默认 dry-run: 只写 preview，不动主表
    Path("qa_outputs").mkdir(exist_ok=True)
    with open(PREVIEW, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["pmid", "source_cell", "target_cell",
                                          "factors", "proposed_evidence_sentence"])
        w.writeheader()
        w.writerows(proposed)
    print(f"\n提议补 evidence: {updated} 条 -> {PREVIEW}")

    if not args.apply:
        print(f"Dry-run（默认）；主表未改。确认后加 --apply 写回 {FILE}。")
        return

    shutil.copy(FILE, FILE + ".bak")
    fieldnames = list(rows[0].keys())
    tmp = FILE + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    shutil.move(tmp, FILE)
    filled = sum(1 for r in rows if r["evidence_sentence"].strip())
    print(f"已写回 {updated} 条 -> {FILE}（备份 {FILE}.bak）")
    print(f"最终有evidence: {filled}/{len(rows)} ({filled/len(rows)*100:.1f}%)")

if __name__ == "__main__":
    main()
