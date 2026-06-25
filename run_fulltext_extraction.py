"""
Full-text factor extraction for remaining needs_review entries.

Usage:
    ANTHROPIC_API_KEY=<key> python3 run_fulltext_extraction.py

Reads:  /tmp/fulltext_fetched.json  (41 entries with methods text)
Reads:  recipes_master_v2.csv       (to find which rows need factors)
Writes: outputs/fulltext_extraction_results_YYYYMMDD.csv
        outputs/fulltext_extraction_auto_applied_YYYYMMDD.csv (high-conf only)

Auto-applies only "high" confidence results to master (with backup).
"""

import json, os, re, time, shutil, csv
from datetime import datetime
from pathlib import Path

import pandas as pd
import anthropic

TODAY = datetime.now().strftime("%Y%m%d")
MASTER = Path("recipes_master_v2.csv")
FULLTEXT_JSON = Path("/tmp/fulltext_fetched.json")
OUT_RESULTS = Path(f"outputs/fulltext_extraction_results_{TODAY}.csv")
OUT_APPLIED = Path(f"outputs/fulltext_extraction_auto_applied_{TODAY}.csv")

MISSING = {"", "not specified", "nan", "not specified in text", "unknown", "none", "n/a"}

SYSTEM = """\
You are a biomedical curator for a cell reprogramming database.
Given methods-section text from a paper, identify the SPECIFIC reprogramming factors
(transcription factors, small molecules, miRNAs, etc.) used to convert source to target cell.

Return JSON only, no prose:
{
  "confidence": "high|medium|low",
  "factors": "FACTOR1, FACTOR2, ...",
  "factor_type": "TF|small_molecule|miRNA|mixed|other",
  "reasoning": "one sentence"
}

Rules:
- "high": factors are unambiguously named in the methods text for THIS conversion
- "medium": likely correct but some ambiguity (e.g. multiple protocols mentioned)
- "low": inferred, not explicit, or text doesn't clearly describe this specific conversion
- If no specific factors found: confidence=low, factors="not specified"
- List only actual molecule names (OCT4, Sox2, CHIR99021...), not generic phrases
"""

def parse_json(text):
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text)

def is_missing(s):
    return str(s).strip().lower() in MISSING

def run():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        return

    client = anthropic.Anthropic(api_key=api_key)

    ft_data = json.loads(FULLTEXT_JSON.read_text())
    pmid_to_methods = {r["pmid"]: r["methods_text"] for r in ft_data if r.get("methods_text", "").strip()}
    print(f"Full text available: {len(pmid_to_methods)} PMIDs")

    df = pd.read_csv(MASTER, dtype=str).fillna("")

    def is_true(s): return str(s).strip().lower() in ("true","1","yes")
    needs = df[
        df["validation_needs_review"].apply(is_true) &
        df["factors"].apply(is_missing)
    ].copy()
    targets = needs[needs["pmid"].isin(pmid_to_methods)]
    print(f"Rows to process: {len(targets)} (across {targets['pmid'].nunique()} PMIDs)")

    results = []
    for idx, row in targets.iterrows():
        pmid = row["pmid"]
        src  = row.get("source_cell", "")
        tgt  = row.get("target_cell", "")
        methods = pmid_to_methods[pmid][:6000]  # trim to fit context

        prompt = (
            f"PMID: {pmid}\n"
            f"Source cell: {src}\n"
            f"Target cell: {tgt}\n\n"
            f"Methods text:\n{methods}"
        )
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            parsed = parse_json(resp.content[0].text)
            results.append({
                "df_index": idx,
                "pmid": pmid,
                "source_cell": src,
                "target_cell": tgt,
                "extracted_factors": parsed.get("factors", ""),
                "extracted_factor_type": parsed.get("factor_type", ""),
                "confidence": parsed.get("confidence", "low"),
                "reasoning": parsed.get("reasoning", ""),
            })
        except Exception as e:
            results.append({
                "df_index": idx, "pmid": pmid, "source_cell": src, "target_cell": tgt,
                "extracted_factors": "", "extracted_factor_type": "", "confidence": "low",
                "reasoning": f"ERROR: {e}",
            })
        time.sleep(0.4)

    pd.DataFrame(results).to_csv(OUT_RESULTS, index=False)
    print(f"Wrote results: {OUT_RESULTS}")

    # Auto-apply high-confidence results
    high = [r for r in results if r["confidence"] == "high" and r["extracted_factors"].strip() and r["extracted_factors"].lower() != "not specified"]
    print(f"High-confidence extractable: {len(high)} / {len(results)}")

    if high:
        backup = MASTER.with_suffix(f".bak_{TODAY}.csv")
        shutil.copy(MASTER, backup)
        print(f"Backup: {backup}")

        for r in high:
            i = r["df_index"]
            df.at[i, "factors"] = r["extracted_factors"]
            if r["extracted_factor_type"]:
                df.at[i, "factor_type"] = r["extracted_factor_type"]
            df.at[i, "validation_needs_review"] = ""
            df.at[i, "validation_notes"] = f"fulltext_auto:{r['reasoning'][:80]}"

        df.to_csv(MASTER, index=False)
        pd.DataFrame(high).to_csv(OUT_APPLIED, index=False)
        print(f"Applied {len(high)} high-conf results to master. See {OUT_APPLIED}")
        print("Run post-pipeline scripts after verifying:")
        print("  python3 fix_factor_types.py")
        print("  python3 flag_single_tf.py")
        print("  python3 mark_duplicates.py")
        print("  python3 mark_broad_duplicates.py")

if __name__ == "__main__":
    run()
