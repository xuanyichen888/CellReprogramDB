"""
Extract cell reprogramming recipes from patents_raw.csv using Claude Haiku.

Usage:
  ANTHROPIC_API_KEY=<key> python3 extract_patent_recipes.py

Reads:   patents_raw.csv
Writes:  patents_recipes.csv   — extracted recipes (source → target, factors)
         patents_not_recipe.csv — patents with no extractable recipe
"""

import json, os, re, time, csv
from pathlib import Path
import anthropic

INPUT  = Path("patents_raw.csv")
OUTPUT = Path("patents_recipes.csv")
SKIP   = Path("patents_not_recipe.csv")
CHKPT  = Path("patents_extraction_checkpoint.json")

SYSTEM = """\
You are a biomedical curator extracting cell reprogramming recipes from patent text.

A recipe = a specific method to convert one cell type into another using named factors
(transcription factors, small molecules, miRNAs, cytokines, knockdowns, etc.).

Return JSON only:
{
  "is_recipe": true/false,
  "source_cell": "cell type being converted (or 'not specified')",
  "target_cell": "desired cell type produced",
  "factors": "FACTOR1, FACTOR2, ... (comma-separated, or 'not specified')",
  "factor_type": "TF|small_molecule|miRNA|knockdown|cytokine|mixed|other",
  "species": "human|mouse|both|other|not specified",
  "confidence": "high|medium|low",
  "notes": "one sentence explanation"
}

Rules:
- is_recipe=false if the patent is about tools/methods for studying reprogramming, not performing it
- is_recipe=false if only generic phrases like "reprogramming factors" without specific names
- confidence=high: specific named factors, clear source→target stated
- confidence=medium: partially specified or inferred
- confidence=low: very vague or indirect
- species: infer from context (human cell lines → human; MEF/mouse embryonic → mouse)
"""


def load_checkpoint():
    if CHKPT.exists():
        return set(json.loads(CHKPT.read_text()))
    return set()

def save_checkpoint(done):
    CHKPT.write_text(json.dumps(sorted(done)))

def parse_json(text):
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text)

def run():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: Set ANTHROPIC_API_KEY"); return

    client = anthropic.Anthropic(api_key=api_key)
    done = load_checkpoint()

    # Load patents
    with open(INPUT, encoding="utf-8", newline="") as f:
        patents = list(csv.DictReader(f))
    print(f"Patents to process: {len(patents)} total, {len(done)} already done")

    recipe_fields = ["patent_number","title","inventor","assignee",
                     "priority_date","grant_date","url",
                     "source_cell","target_cell","factors","factor_type",
                     "species","confidence","notes"]
    skip_fields   = ["patent_number","title","url","notes"]

    recipe_mode = not Path(OUTPUT).exists()
    skip_mode   = not Path(SKIP).exists()

    recipes_written = 0
    skipped_written = 0

    with open(OUTPUT, "a", encoding="utf-8", newline="") as rf, \
         open(SKIP,   "a", encoding="utf-8", newline="") as sf:
        rw = csv.DictWriter(rf, fieldnames=recipe_fields, extrasaction="ignore")
        sw = csv.DictWriter(sf, fieldnames=skip_fields,   extrasaction="ignore")
        if recipe_mode: rw.writeheader()
        if skip_mode:   sw.writeheader()

        for pat in patents:
            num = pat.get("patent_number", "").strip()
            if not num or num in done:
                continue

            text = f"Title: {pat.get('title','')}\n\n{pat.get('snippet','')}"
            try:
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=350,
                    system=SYSTEM,
                    messages=[{"role": "user", "content": text}],
                )
                parsed = parse_json(resp.content[0].text)
            except Exception as e:
                print(f"  ERROR {num}: {e}")
                done.add(num)
                save_checkpoint(done)
                time.sleep(0.5)
                continue

            done.add(num)
            save_checkpoint(done)

            if parsed.get("is_recipe"):
                row = {**pat, **parsed}
                row["patent_number"] = num
                rw.writerow(row)
                recipes_written += 1
                print(f"  [RECIPE] {num}: {parsed.get('source_cell','')} → {parsed.get('target_cell','')} | {parsed.get('factors','')[:60]}")
            else:
                sw.writerow({"patent_number": num, "title": pat.get("title",""),
                             "url": pat.get("url",""), "notes": parsed.get("notes","")})
                skipped_written += 1

            time.sleep(0.35)

    print(f"\nDone. Recipes: {recipes_written}  |  Not-recipe: {skipped_written}")
    print(f"See: {OUTPUT}")


if __name__ == "__main__":
    run()
