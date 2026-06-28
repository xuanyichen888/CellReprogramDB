"""
Infer missing source_cell for 9 needs_review entries using Europe PMC abstracts + Claude Haiku.

Usage:
  ANTHROPIC_API_KEY=<key> python3 fix_source_cells.py [--dry-run]

Reads/writes: recipes_master_v2.csv
"""

import json, os, re, time, argparse
import requests
import pandas as pd
import anthropic

TARGET_PMIDS = [
    "40885192",  # cDC2B / pDC-like dendritic cell (PU.1, IRF4 ...)
    "35810486",  # induced neural stem cell
    "37357983",  # induced neuron (ASCL1)
    "39279468",  # dopaminergic neuron (Arid4b)
    "31535361",  # induced neuron (ASCL1, BRN2, miR124 ...)
    "37371832",  # iPSC (OCT4, SOX2, KLF4, c-MYC, miR-17-92)
    "36691621",  # iPSC (OCT4, SOX2, KLF4)
    "33720298",  # iPSC (ePOU, Sox2, Klf4, c-Myc)
]

SYSTEM = """\
You are a cell biology expert. Given a PubMed abstract, identify the SOURCE CELL TYPE that is being reprogrammed.

Reply with JSON only:
{
  "source_cell": "the cell type being converted FROM (e.g. 'fibroblast', 'astrocyte', 'T cell')",
  "confidence": "high|medium|low",
  "evidence": "key phrase from the abstract that indicates the source cell"
}

Rules:
- Focus on the starting cell type, NOT the end product
- Use the standardized name (fibroblast, not NIH 3T3 unless it's the only info)
- If adult somatic cell is not specified, output "not specified"
- If embryonic/fetal context without specific cell type, output "embryonic cell"
- For iPSC reprogramming papers, the source is usually fibroblast unless stated otherwise
"""


def fetch_abstract(pmid: str) -> str:
    """Fetch abstract from Europe PMC."""
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {"query": f"EXT_ID:{pmid} SRC:MED", "format": "json", "resulttype": "core", "pageSize": 1}
    try:
        r = requests.get(url, params=params, timeout=25)
        r.raise_for_status()
        results = r.json().get("resultList", {}).get("result", [])
        if results:
            abstract = results[0].get("abstractText", "")
            title = results[0].get("title", "")
            return f"Title: {title}\n\nAbstract: {abstract}"
    except Exception as e:
        print(f"    Europe PMC error for {pmid}: {e}")
    return ""


def infer_source_cell(client, pmid: str, abstract_text: str, target_cell: str, factors: str) -> dict:
    """Use Claude Haiku to infer source cell from abstract."""
    prompt = f"""PMID: {pmid}
Target cell: {target_cell}
Reprogramming factors used: {factors}

{abstract_text}"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        return json.loads(text)
    except Exception as e:
        print(f"    LLM error for {pmid}: {e}")
        return {}


def run(dry_run: bool = False):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: Set ANTHROPIC_API_KEY"); return

    client = anthropic.Anthropic(api_key=api_key)

    df = pd.read_csv("recipes_master_v2.csv", dtype=str, low_memory=False)
    df["source_cell"] = df["source_cell"].fillna("")
    df["factors"] = df["factors"].fillna("")

    updates = []

    for pmid in TARGET_PMIDS:
        mask = (df["pmid"].astype(str) == pmid) & (df["source_cell"].str.strip() == "")
        rows = df[mask]
        if rows.empty:
            print(f"PMID {pmid}: no missing-source_cell rows (already filled or not in DB)")
            continue

        print(f"\nPMID {pmid} ({len(rows)} rows to fill)")

        # Fetch abstract once per PMID
        print(f"  Fetching abstract from Europe PMC...")
        abstract = fetch_abstract(pmid)
        if not abstract:
            print(f"  No abstract found, skipping")
            continue
        print(f"  Abstract: {abstract[:120]}...")

        for idx, row in rows.iterrows():
            result = infer_source_cell(
                client, pmid, abstract,
                str(row.get("target_cell", "")),
                str(row.get("factors", ""))
            )
            source = result.get("source_cell", "")
            conf   = result.get("confidence", "")
            ev     = result.get("evidence", "")

            print(f"  Row {idx}: source_cell={source!r}  conf={conf}  | {ev[:80]}")

            if source and source != "not specified" and conf in ("high", "medium"):
                updates.append((idx, source))

        time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"Proposed updates: {len(updates)}")
    for idx, src in updates:
        row = df.loc[idx]
        print(f"  [{idx}] PMID {row.get('pmid','')}: source_cell → {src!r}")

    if dry_run:
        print("\n[DRY RUN] No changes written.")
        return

    if updates:
        for idx, src in updates:
            df.at[idx, "source_cell"] = src
        df.to_csv("recipes_master_v2.csv", index=False)
        print(f"\nWrote {len(updates)} source_cell updates to recipes_master_v2.csv")
    else:
        print("\nNo updates made.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
