"""
为 recipes_master.csv 每条记录提取 evidence sentence
- abstract来源的条目：从 papers.csv 的摘要里找
- fulltext来源的条目：从 fulltext.csv 的全文里找
输出: recipes_master_v2.csv（新增 evidence_sentence 列）
"""

import csv, json, os, time
from openai import OpenAI

API_KEY    = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL   = "https://api.deepseek.com"
MODEL      = "deepseek-v4-flash"
INPUT      = "recipes_master.csv"
OUTPUT     = "recipes_master_v2.csv"
CHECKPOINT = "checkpoint_evidence.json"
SLEEP      = 0.8

OUTPUT_FIELDS = [
    "pmid", "source_cell", "target_cell",
    "factors", "factor_type",
    "species", "culture_condition",
    "confidence", "paper_type", "notes",
    "source", "evidence_sentence", "evidence_quality",
    "validation_needs_review", "validation_resolution",
]

SYSTEM_PROMPT = """\
You are a biomedical text mining expert specializing in cell reprogramming literature.

Given a reprogramming recipe (source cell → target cell, using specific factors) and the original text,
find the single best sentence that directly demonstrates the conversion was SUCCESSFULLY achieved in THIS paper.

Respond ONLY with valid JSON:
{
  "evidence_sentence": "...",
  "quality": "good|weak|none"
}

REQUIRED — the chosen sentence MUST describe a RESULT or OUTCOME:
  ✓ Generated / produced / obtained / derived target cell type from source
  ✓ Source cell was converted / reprogrammed / transdifferentiated into target
  ✓ iPSC / target-cell colonies appeared, were observed, were confirmed
  ✓ Reprogramming efficiency of X% was achieved
  ✓ Cells acquired / expressed markers of the target cell type
  ✓ "Here we report/describe the generation of X from Y" — result announcements ARE acceptable

FORBIDDEN — do NOT select sentences that:
  ✗ Cite prior or previously published work:
      "We previously showed...", "We have previously demonstrated...",
      "Recently, we identified...", "As reported before...", "It has been shown that..."
  ✗ Describe only the experimental setup without stating an outcome:
      "Cells were transduced with...", "We used X to investigate whether...",
      "Our objective was to determine...", "We treated/cultured/maintained cells with...",
      "To test whether...", "We aimed to explore..."
  ✗ Describe failed / negative results:
      "X failed to induce Y", "No colonies were observed", "Did not produce"

Rules:
- Copy the sentence EXACTLY word-for-word from the text; do NOT paraphrase
- Choose the sentence most explicitly naming source cell, target cell, AND factors together
- If no single sentence covers all three, prefer one naming cell types + outcome
- quality="good"  — clearly states the conversion outcome with factors or cell types
- quality="weak"  — partially supports the recipe (indirect, or missing one element)
- quality="none"  — no suitable result sentence exists; return evidence_sentence=""
"""


def load_checkpoint():
    if os.path.exists(CHECKPOINT):
        return json.load(open(CHECKPOINT))
    return {}

def save_checkpoint(done):
    json.dump(done, open(CHECKPOINT, "w"))


def main():
    if not API_KEY:
        raise SystemExit("请先运行: export DEEPSEEK_API_KEY=sk-...")

    # 读取recipes
    recipes = list(csv.DictReader(open(INPUT, encoding="utf-8")))
    print(f"总条目: {len(recipes)}")

    # 读取原文
    abstracts = {p["pmid"]: p["abstract"]
                 for p in csv.DictReader(open("papers.csv", encoding="utf-8"))}

    fulltext_map = {}
    if os.path.exists("fulltext.csv"):
        for row in csv.DictReader(open("fulltext.csv", encoding="utf-8")):
            fulltext_map[row["pmid"]] = row["methods_text"] + " " + row["results_text"]

    done = load_checkpoint()  # {index_str: evidence_sentence}

    # Guard: if output already exists but has a different header (e.g. old schema without
    # evidence_quality / validation columns), abort rather than silently corrupt the file.
    file_exists = os.path.exists(OUTPUT)
    if file_exists:
        with open(OUTPUT, newline="", encoding="utf-8") as fh:
            existing_fields = csv.DictReader(fh).fieldnames or []
        missing = [f for f in OUTPUT_FIELDS if f not in existing_fields]
        extra   = [f for f in existing_fields if f not in OUTPUT_FIELDS]
        if missing or extra:
            raise SystemExit(
                f"ERROR: {OUTPUT} has incompatible header.\n"
                f"  Missing fields: {missing}\n"
                f"  Unexpected fields: {extra}\n"
                "Delete or rename the existing output file, clear the checkpoint, and re-run."
            )

    out_f  = open(OUTPUT, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out_f, fieldnames=OUTPUT_FIELDS)
    if not file_exists:
        writer.writeheader()

    client    = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    processed = 0
    skipped   = 0

    for i, recipe in enumerate(recipes):
        idx = str(i)

        # 已有结果直接写
        if idx in done:
            row = dict(recipe)
            row["evidence_sentence"] = done[idx]
            writer.writerow(row)
            skipped += 1
            continue

        pmid   = recipe["pmid"]
        source = recipe.get("source", "abstract")

        # 获取原文
        if source == "fulltext" and pmid in fulltext_map:
            text = fulltext_map[pmid][:12000]
        else:
            text = abstracts.get(pmid, "")

        if not text.strip():
            done[idx] = ""
            row = dict(recipe); row["evidence_sentence"] = ""
            writer.writerow(row)
            save_checkpoint(done)
            continue

        print(f"[{i+1}/{len(recipes)}] PMID {pmid} "
              f"{recipe['source_cell'][:20]} → {recipe['target_cell'][:20]} ... ",
              end="", flush=True)

        user_msg = (
            f"Recipe:\n"
            f"  source_cell: {recipe['source_cell']}\n"
            f"  target_cell: {recipe['target_cell']}\n"
            f"  factors: {recipe['factors']}\n\n"
            f"Original text:\n{text}"
        )

        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.0,
                max_tokens=4096,
            )
            raw = (resp.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"): raw = raw[4:]
            result   = json.loads(raw.strip())
            evidence = result.get("evidence_sentence", "")
            quality  = result.get("quality", "good").lower().strip()
        except Exception as e:
            print(f"错误: {e}")
            evidence = ""
            quality  = "none"

        # Auto-flag weak or missing evidence so downstream QA can review
        # FIX: do NOT use `or` on string "False" — Python treats any non-empty string as truthy
        needs_review = quality in ("weak", "none")

        done[idx] = evidence
        row = dict(recipe)
        row["evidence_sentence"]      = evidence
        row["evidence_quality"]       = quality
        row["validation_needs_review"] = "True" if needs_review else ""
        row["validation_resolution"]  = ("evidence_quality_" + quality) if needs_review else ""
        # Ensure all OUTPUT_FIELDS have a value (avoid DictWriter 'extra fields' error)
        for field in OUTPUT_FIELDS:
            row.setdefault(field, "")
        writer.writerow({k: row[k] for k in OUTPUT_FIELDS})  # only write declared fields
        save_checkpoint(done)
        processed += 1

        # 预览（截断显示）
        preview = evidence[:60] + "..." if len(evidence) > 60 else evidence
        print(f"✓  \"{preview}\"")
        time.sleep(SLEEP)

    out_f.close()
    print(f"\n完成！处理 {processed} 条，跳过(已有) {skipped} 条")
    print(f"结果保存至 {OUTPUT}")


if __name__ == "__main__":
    main()
