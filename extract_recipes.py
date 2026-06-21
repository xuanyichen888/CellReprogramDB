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
import argparse
from pathlib import Path
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
    "confidence", "paper_type",
    "recipe_status", "notes",
]

# recipe_status values that should NOT be written to the database at all
SKIP_RECIPE_STATUS = {"failed", "prior_work", "method_only", "enhancer_only"}

# recipe_status values that are written but flagged low-confidence + needs_review
UNCERTAIN_RECIPE_STATUS = {"unclear"}

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
    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint_path.open("w") as f:
        json.dump(list(processed), f)

def load_papers(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def load_requested_pmids(value):
    if not value:
        return None
    path = Path(value)
    if path.exists():
        if path.suffix.lower() == ".csv":
            with path.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if "pmid" not in (reader.fieldnames or []):
                    raise SystemExit(f"ERROR: {path} has no 'pmid' column")
                return [row["pmid"].strip() for row in reader if row.get("pmid", "").strip()]
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [p.strip() for p in value.split(",") if p.strip()]

def select_papers(papers, pmids=None, limit=None, offset=0):
    selected = papers
    if pmids is not None:
        wanted = list(dict.fromkeys(str(p).strip() for p in pmids if str(p).strip()))
        wanted_set = set(wanted)
        order = {pmid: i for i, pmid in enumerate(wanted)}
        selected = [p for p in selected if str(p.get("pmid", "")).strip() in wanted_set]
        selected.sort(key=lambda p: order.get(str(p.get("pmid", "")).strip(), len(order)))
    if offset:
        selected = selected[offset:]
    if limit:
        selected = selected[:limit]
    return selected

def call_api(client, pmid, title, abstract):
    user_msg = f"PMID: {pmid}\nTitle: {title}\n\nAbstract:\n{abstract}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        temperature=0.0,
        max_tokens=8192,
    )
    return (resp.choices[0].message.content or "").strip()

def parse_response(raw):
    text = raw
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

def append_rows(rows, output_csv):
    file_exists = os.path.exists(output_csv)
    if file_exists:
        with open(output_csv, newline="", encoding="utf-8") as fh:
            existing_fields = csv.DictReader(fh).fieldnames or []
        missing = [f for f in OUTPUT_FIELDS if f not in existing_fields]
        if missing:
            raise SystemExit(
                f"ERROR: {output_csv} is missing fields {missing}.\n"
                "Delete the output file and checkpoint to start fresh."
            )
    out_path = Path(output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract cell-reprogramming recipes from PubMed abstract CSVs."
    )
    parser.add_argument("--input", default=INPUT_CSV, help=f"Input paper CSV. Default: {INPUT_CSV}")
    parser.add_argument("--output", default=OUTPUT_CSV, help=f"Output recipe CSV. Default: {OUTPUT_CSV}")
    parser.add_argument(
        "--checkpoint",
        default=CHECKPOINT,
        help=f"Processed PMID checkpoint JSON. Default: {CHECKPOINT}",
    )
    parser.add_argument(
        "--pmids",
        default="",
        help="Comma-separated PMIDs, a text file with one PMID per line, or a CSV with a pmid column.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Process at most N selected papers.")
    parser.add_argument("--offset", type=int, default=0, help="Skip N selected papers before processing.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show selected papers and output/checkpoint paths; do not call the API.",
    )
    args = parser.parse_args()

    requested_pmids = load_requested_pmids(args.pmids)
    papers = select_papers(
        load_papers(args.input),
        pmids=requested_pmids,
        limit=args.limit or None,
        offset=args.offset,
    )

    if args.dry_run:
        print(f"Input:      {args.input}")
        print(f"Output:     {args.output}")
        print(f"Checkpoint: {args.checkpoint}")
        print(f"Selected papers: {len(papers)}")
        for paper in papers[:20]:
            title = (paper.get("title", "") or "").replace("\n", " ")
            print(f"  PMID {paper.get('pmid', '')} | {paper.get('year', '')} | {title[:120]}")
        if len(papers) > 20:
            print(f"  ... (+{len(papers) - 20} more)")
        return

    if not API_KEY:
        raise SystemExit(
            "ERROR: Set your DeepSeek API key:\n"
            "  export DEEPSEEK_API_KEY=sk-..."
        )

    client    = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    processed = load_checkpoint(args.checkpoint)

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
            save_checkpoint(processed, args.checkpoint)
            skipped += 1
            continue

        try:
            raw    = call_api(client, pmid, title, abstract)
            result = parse_response(raw)
        except json.JSONDecodeError as e:
            print(f"JSON error: {e} | raw: {raw[:120]}")
            errors += 1
            processed.add(pmid)
            save_checkpoint(processed, args.checkpoint)
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
            skipped_status = 0
            for entry in result["entries"]:
                recipe_status = entry.get("recipe_status", "successful").lower().strip()
                # Skip entries the model classified as non-successful
                if recipe_status in SKIP_RECIPE_STATUS:
                    skipped_status += 1
                    print(f"  ↳ skipped [{recipe_status}]: {entry.get('source_cell','')} → {entry.get('target_cell','')}")
                    continue
                # 'unclear' entries: downgrade confidence and flag for review
                conf = entry.get("confidence", "")
                if recipe_status in UNCERTAIN_RECIPE_STATUS:
                    conf = "low"   # hidden by default; QA can promote if valid

                rows.append({
                    "pmid":              pmid,
                    "source_cell":       entry.get("source_cell", ""),
                    "target_cell":       entry.get("target_cell", ""),
                    "factors":           entry.get("factors", ""),
                    "factor_type":       entry.get("factor_type", ""),
                    "species":           entry.get("species", ""),
                    "culture_condition": entry.get("culture_condition", ""),
                    "confidence":        conf,
                    "paper_type":        paper_type,
                    "recipe_status":     recipe_status,
                    "notes":             entry.get("notes", ""),
                })
            if rows:
                append_rows(rows, args.output)
                extracted += len(rows)
            suffix = f" (skipped {skipped_status} non-successful)" if skipped_status else ""
            print(f"extracted {len(rows)} recipe(s)  [{paper_type}]{suffix}")

        processed.add(pmid)
        save_checkpoint(processed, args.checkpoint)
        time.sleep(SLEEP_BETWEEN)

    print(f"\n完成。提取: {extracted} 条 | 跳过: {skipped} 篇 | 错误: {errors} 篇")
    print(f"结果保存至 {args.output}")


if __name__ == "__main__":
    main()
