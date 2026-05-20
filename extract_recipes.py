"""
Extract cell reprogramming recipes from PubMed abstracts using SiliconFlow API.

Output columns:
  pmid | source_cell | target_cell | factors | factor_type |
  species | culture_condition | confidence | paper_type | notes
"""

import csv
import json
import os
import time
from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY       = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL      = "https://api.deepseek.com"
MODEL         = "deepseek-v4-flash"
INPUT_CSV     = "papers.csv"
OUTPUT_CSV    = "recipes_v2.csv"
CHECKPOINT    = "checkpoint_v2.json"
SLEEP_BETWEEN = 1.0

OUTPUT_FIELDS = [
    "pmid", "source_cell", "target_cell",
    "factors", "factor_type",
    "species", "culture_condition",
    "confidence", "paper_type", "notes",
]

# ── Prompt ────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are a biomedical expert in cell reprogramming. Extract structured recipe data from abstracts.

Respond ONLY with valid JSON — no markdown, no explanation — using this exact schema:
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
      "recipe_status": "successful|failed|prior_work|method_only|enhancer_only|unclear",
      "notes": "..."
    }
  ]
}

═══ CRITICAL EXTRACTION RULES ═══

ONLY extract recipes that are DEMONSTRATED AS SUCCESSFUL RESULTS in THIS paper.

DO NOT extract:
  ✗ Recipes cited as prior/previously published work:
      "We previously showed...", "As we reported...", "It was demonstrated that..."
      → These belong to another paper; set recipe_status="prior_work"
  ✗ Failed or negative conversions:
      "X failed to induce Y", "did not generate", "was insufficient to convert"
      → set recipe_status="failed", confidence="low"
  ✗ Methods/setup without result:
      "X was used to investigate whether Y can be converted" (aim, no outcome)
      → set recipe_status="method_only"
  ✗ Enhancers tested in addition to a base recipe (without including the full base cocktail):
      "GLIS1 was added to OSKM and improved efficiency" → if OSKM is not listed in factors, skip
      → Include BOTH the enhancer AND the base cocktail in the factors field, or set recipe_status="enhancer_only"

has_recipe:
- true if this paper reports a SUCCESSFUL cell conversion as a NEW finding
- false if no conversion result is newly reported in this paper

paper_type:
- "research" = original experimental paper reporting new results
- "review" = review/survey paper
- "other" = methods, protocol, commentary, etc.

source_cell:
- The starting cell type, as specific as possible
- e.g. "mouse embryonic fibroblast" not "somatic cell"; "peripheral blood mononuclear cell" not "blood cell"
- Do NOT use: "somatic cell", "differentiated cell" — always name the specific type
- If the abstract covers multiple source cells, create one entry per source→target pair

target_cell:
- The destination cell type, as specific as possible
- e.g. "dopaminergic neuron" not "neuron"; "ventricular cardiomyocyte" not "cardiomyocyte"
- Do NOT use abbreviations alone — write the full name, e.g. "induced pluripotent stem cell" not "iPSC"

factors:
- List ALL factors in this paper's successful recipe
- Comma-separated, use standard gene/compound names (not abbreviations-only)
- If a single transcription factor is described as studying its MECHANISM within a known cocktail
  (e.g., "the role of SOX2 in Yamanaka reprogramming"), set confidence="low" and note it
- If no specific factors are named, write "not specified"

factor_type:
- Comma-separated in same order as factors
- Labels: TF | small_molecule | miRNA | knockdown | cytokine | other
- If factors is "not specified", write ""

species:
- "human", "mouse", "rat", or other; list all tested: "human, mouse"
- If not mentioned, write ""

culture_condition:
- Specific medium, supplement, or culture system that is part of the protocol
- Brief. Write "" if not relevant.

confidence:
- "high": source cell, target cell, AND named factor(s) are all explicitly stated as a SUCCESSFUL result
- "medium": conversion likely but source/target/factors are partial, vague, or inferred
- "low": conversion inferred, not directly stated; or single-TF studying its role in a larger cocktail

recipe_status:
- "successful": conversion demonstrated as positive result in this paper
- "failed": conversion attempted but failed (negative result)
- "prior_work": recipe belongs to a previously published paper
- "method_only": abstract describes the experimental approach but no outcome
- "enhancer_only": only an enhancer was studied without the full base cocktail
- "unclear": cannot determine

notes:
- One sentence (≤20 words): efficiency %, in vivo vs in vitro, key finding
- Write "" if nothing to add

If has_recipe=false, entries must be [].
One abstract can produce multiple entries if it reports multiple source→target conversions.
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_checkpoint(path):
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f))
    return set()

def save_checkpoint(processed, path):
    with open(path, "w") as f:
        json.dump(list(processed), f)

def load_papers(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def call_api(client, pmid, title, abstract):
    user_msg = f"PMID: {pmid}\nTitle: {title}\n\nAbstract:\n{abstract}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        temperature=0.0,
        max_tokens=2048,
    )
    return resp.choices[0].message.content.strip()

def parse_response(raw):
    text = raw
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

def append_rows(rows):
    file_exists = os.path.exists(OUTPUT_CSV)
    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not API_KEY:
        raise SystemExit(
            "ERROR: Set your DeepSeek API key:\n"
            "  export DEEPSEEK_API_KEY=sk-..."
        )

    client    = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    papers    = load_papers(INPUT_CSV)
    processed = load_checkpoint(CHECKPOINT)

    total     = len(papers)
    skipped   = 0
    extracted = 0
    errors    = 0

    print(f"共 {total} 篇论文，已处理 {len(processed)} 篇，待处理 {total - len(processed)} 篇\n")

    for i, paper in enumerate(papers, 1):
        pmid     = paper["pmid"]
        title    = paper.get("title", "")
        abstract = paper.get("abstract", "")

        if pmid in processed:
            continue

        print(f"[{i}/{total}] PMID {pmid} ... ", end="", flush=True)

        if not abstract or len(abstract) < 50:
            print("skipped (no abstract)")
            processed.add(pmid)
            save_checkpoint(processed, CHECKPOINT)
            skipped += 1
            continue

        try:
            raw    = call_api(client, pmid, title, abstract)
            result = parse_response(raw)
        except json.JSONDecodeError as e:
            print(f"JSON error: {e} | raw: {raw[:120]}")
            errors += 1
            processed.add(pmid)
            save_checkpoint(processed, CHECKPOINT)
            time.sleep(SLEEP_BETWEEN)
            continue
        except Exception as e:
            print(f"API error: {e}")
            errors += 1
            time.sleep(5)
            continue

        if not result.get("has_recipe") or not result.get("entries"):
            print("no recipe")
            skipped += 1
        else:
            paper_type = result.get("paper_type", "")
            rows = []
            for entry in result["entries"]:
                rows.append({
                    "pmid":              pmid,
                    "source_cell":       entry.get("source_cell", ""),
                    "target_cell":       entry.get("target_cell", ""),
                    "factors":           entry.get("factors", ""),
                    "factor_type":       entry.get("factor_type", ""),
                    "species":           entry.get("species", ""),
                    "culture_condition": entry.get("culture_condition", ""),
                    "confidence":        entry.get("confidence", ""),
                    "paper_type":        paper_type,
                    "notes":             entry.get("notes", ""),
                })
            append_rows(rows)
            extracted += len(rows)
            print(f"extracted {len(rows)} recipe(s)  [{paper_type}]")

        processed.add(pmid)
        save_checkpoint(processed, CHECKPOINT)
        time.sleep(SLEEP_BETWEEN)

    print(f"\n完成。提取: {extracted} 条 | 跳过: {skipped} 篇 | 错误: {errors} 篇")
    print(f"结果保存至 {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
