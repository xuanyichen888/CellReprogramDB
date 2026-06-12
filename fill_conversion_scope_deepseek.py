"""
Fill unclear conversion_scope annotations with DeepSeek.

The script only touches rows whose conversion_scope is blank/unclear and keeps
low-confidence model calls as "unclear" for manual review.
"""

import argparse
import json
import os
import shutil
import time
from pathlib import Path

import pandas as pd
from openai import OpenAI

FILE = Path("recipes_master_v2.csv")
PAPERS_FILE = Path("papers.csv")
FULLTEXT_FILE = Path("fulltext.csv")
CHECKPOINT = Path("qa_outputs/scope_deepseek_checkpoint.json")
UNRESOLVED_OUT = Path("qa_outputs/scope_deepseek_unresolved.csv")

BASE_URL = "https://api.deepseek.com"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
SLEEP_SECONDS = float(os.environ.get("DEEPSEEK_SLEEP_SECONDS", "0.6"))

SCOPES = {
    "classical_reprogramming",
    "lineage_conversion",
    "directed_differentiation",
    "cell_state_modulation",
    "unclear",
}

SYSTEM_PROMPT = """\
You are a biomedical curator classifying cell reprogramming recipes.

Task:
Assign exactly one conversion_scope for THIS recipe row.

Return ONLY valid JSON:
{"conversion_scope": "...", "confidence": "high|medium|low", "rationale": "..."}

Allowed conversion_scope values:
classical_reprogramming | lineage_conversion | directed_differentiation | cell_state_modulation | unclear

Definitions:
- classical_reprogramming: somatic/non-pluripotent cells are induced into iPSCs, pluripotent, totipotent-like, 2C-like, or pluripotent-like states.
- directed_differentiation: pluripotent, stem, progenitor, organoid, or multipotent cells are differentiated into a more specialized lineage.
- lineage_conversion: direct conversion/transdifferentiation between distinct non-pluripotent cell identities, including metaplasia and mature lineage switches.
- cell_state_modulation: state or phenotype change within the same broad lineage, including EMT/MET, M1/M2 polarization, cancer plasticity, dedifferentiation, terminal differentiation, stemness, repair, activation, senescence, or drug resistance.
- unclear: the text is insufficient, target/source is missing, or it is not a clear recipe.

Rules:
- Use source cell, target cell, factors, title, evidence sentence, notes, abstract, and full-text excerpt.
- Do not use outside knowledge if the row text is ambiguous.
- Prefer lineage_conversion over cell_state_modulation when source and target are distinct named cell identities.
- Prefer cell_state_modulation for cancer-to-cancer/cancer-state changes and same-lineage phenotype shifts.
- Prefer directed_differentiation when the source is iPSC/ESC/PSC/stem/progenitor and the target is more mature/specialized.
- Keep rationale under 25 words.
"""


def clean(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def truncate(text: str, limit: int) -> str:
    text = clean(text)
    return text if len(text) <= limit else text[:limit] + " ..."


def parse_json(raw: str) -> dict:
    text = clean(raw)
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def load_lookup(path: Path, fields: list[str]) -> dict[str, str]:
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype=str).fillna("")
    lookup = {}
    for _, row in df.iterrows():
        pmid = clean(row.get("pmid", ""))
        if not pmid:
            continue
        lookup[pmid] = "\n".join(
            f"{field}: {truncate(row.get(field, ''), 1800)}"
            for field in fields
            if clean(row.get(field, ""))
        )
    return lookup


def fulltext_excerpt(row: pd.Series, fulltext: str) -> str:
    text = clean(fulltext)
    if not text:
        return ""
    keywords = [
        "reprogram",
        "conversion",
        "convert",
        "transdifferentiat",
        "differentiat",
        "dedifferentiat",
        "pluripotent",
        "stemness",
        "metaplasia",
        "lineage",
        "phenotype",
        "state",
        "polarization",
        "emt",
        "met",
        "cancer",
        "terminal",
        "mature",
    ]
    for cell in [row.get("source_cell", ""), row.get("target_cell", "")]:
        for word in clean(cell).lower().replace("-", " ").split():
            if len(word) >= 4 and word not in {"cell", "cells", "like", "induced"}:
                keywords.append(word)

    sentences = []
    for sentence in text.replace("\n", " ").split(". "):
        low = sentence.lower()
        score = sum(1 for keyword in keywords if keyword in low)
        if score:
            sentences.append((score, sentence))
    if not sentences:
        return truncate(text, 3200)
    sentences.sort(key=lambda item: -item[0])
    return truncate(". ".join(sentence for _, sentence in sentences[:24]), 4200)


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        with CHECKPOINT.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_checkpoint(cache: dict) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    with CHECKPOINT.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def row_key(idx: int, row: pd.Series) -> str:
    return "|".join(
        [
            str(idx),
            clean(row.get("pmid", "")),
            clean(row.get("source_cell", "")),
            clean(row.get("target_cell", "")),
            clean(row.get("factors", ""))[:100],
        ]
    )


def build_user_message(row: pd.Series, paper_text: str, excerpt: str) -> str:
    parts = [
        f"PMID: {clean(row.get('pmid', ''))}",
        f"Year: {clean(row.get('year', ''))}",
        f"Paper type: {clean(row.get('paper_type', ''))}",
        f"Recipe confidence: {clean(row.get('confidence', ''))}",
        f"Source cell: {clean(row.get('source_cell', ''))}",
        f"Target cell: {clean(row.get('target_cell', ''))}",
        f"Factors: {clean(row.get('factors', ''))}",
        f"Factor type: {clean(row.get('factor_type', ''))}",
        f"Species: {clean(row.get('species', ''))}",
        f"Evidence sentence: {truncate(row.get('evidence_sentence', ''), 1200)}",
        f"Notes: {truncate(row.get('notes', ''), 700)}",
    ]
    if paper_text:
        parts.append("Paper text:\n" + truncate(paper_text, 2800))
    if excerpt:
        parts.append("Full-text excerpt:\n" + excerpt)
    return "\n".join(parts)


def call_deepseek(client: OpenAI, row: pd.Series, paper_text: str, excerpt: str) -> dict:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(row, paper_text, excerpt)},
        ],
        temperature=0,
        max_tokens=140,
    )
    message = resp.choices[0].message
    raw = clean(getattr(message, "content", ""))
    if not raw:
        raw = clean(getattr(message, "reasoning_content", ""))
    if not raw:
        raise ValueError("empty model response content")
    try:
        parsed = parse_json(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse failed; raw={raw[:300]!r}") from exc

    scope = clean(parsed.get("conversion_scope", "unclear"))
    confidence = clean(parsed.get("confidence", "low")).lower()
    if scope not in SCOPES:
        scope = "unclear"
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    return {
        "conversion_scope": scope,
        "confidence": confidence,
        "rationale": truncate(parsed.get("rationale", ""), 180),
        "raw": raw,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Process at most N rows; 0 means all.")
    parser.add_argument("--no-write", action="store_true", help="Do not write recipes_master_v2.csv.")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise SystemExit("ERROR: export DEEPSEEK_API_KEY=sk-...")

    df = pd.read_csv(FILE, dtype=str).fillna("")
    if "scope_inference_method" not in df.columns:
        df["scope_inference_method"] = ""
    if "scope_inference_rationale" not in df.columns:
        df["scope_inference_rationale"] = ""

    paper_lookup = load_lookup(PAPERS_FILE, ["title", "abstract"])
    fulltext_lookup = load_lookup(FULLTEXT_FILE, ["methods_text", "results_text"])
    cache = load_checkpoint()
    client = OpenAI(api_key=api_key, base_url=BASE_URL)

    candidate_mask = df["conversion_scope"].astype(str).str.strip().isin(["", "unclear"])
    candidate_indices = df[candidate_mask].index.tolist()
    todo = []
    for idx in candidate_indices:
        key = row_key(idx, df.loc[idx])
        if key not in cache:
            todo.append(idx)
    if args.limit > 0:
        todo = todo[: args.limit]

    print(f"Unclear scope rows: {len(candidate_indices)}")
    print(f"Cached model rows: {len(cache)}")
    print(f"Rows to process now: {len(todo)}")

    for n, idx in enumerate(todo, 1):
        row = df.loc[idx]
        pmid = clean(row.get("pmid", ""))
        key = row_key(idx, row)
        print(f"[{n}/{len(todo)}] row {idx} PMID {pmid} ... ", end="", flush=True)
        try:
            result = call_deepseek(
                client,
                row,
                paper_lookup.get(pmid, ""),
                fulltext_excerpt(row, fulltext_lookup.get(pmid, "")),
            )
            cache[key] = result
            save_checkpoint(cache)
            print(f"{result['conversion_scope']} ({result['confidence']})")
        except Exception as exc:
            print(f"ERROR: {exc}")
            time.sleep(4)
            continue
        time.sleep(SLEEP_SECONDS)

    filled = 0
    unresolved_rows = []
    for idx in candidate_indices:
        key = row_key(idx, df.loc[idx])
        result = cache.get(key)
        if not result:
            unresolved_rows.append(idx)
            continue
        scope = result.get("conversion_scope", "unclear")
        confidence = result.get("confidence", "low")
        if scope in SCOPES - {"unclear"} and confidence in {"high", "medium"}:
            df.at[idx, "conversion_scope"] = scope
            df.at[idx, "scope_inference_method"] = f"deepseek_{confidence}"
            df.at[idx, "scope_inference_rationale"] = clean(result.get("rationale", ""))
            filled += 1
        else:
            unresolved_rows.append(idx)

    unresolved = df.loc[unresolved_rows].copy()
    unresolved.to_csv(UNRESOLVED_OUT, index=False, encoding="utf-8")

    if not args.no_write:
        backup = Path(str(FILE) + ".pre_scope_fill.bak")
        if not backup.exists():
            shutil.copy(FILE, backup)
        df.to_csv(FILE, index=False, encoding="utf-8")

    remaining_unclear = df["conversion_scope"].astype(str).str.strip().isin(["", "unclear"]).sum()
    print()
    print(f"Filled from DeepSeek checkpoint: {filled}")
    print(f"Remaining unclear scope rows: {remaining_unclear}")
    print(f"Unresolved rows exported to: {UNRESOLVED_OUT}")
    if args.no_write:
        print("No-write mode: recipes_master_v2.csv was not changed.")


if __name__ == "__main__":
    main()
