"""
Fill remaining blank species annotations with DeepSeek.

This script is intentionally narrow: it only touches rows whose species is blank,
stores every model response in a checkpoint, and leaves uncertain cases blank.
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
CHECKPOINT = Path("qa_outputs/species_deepseek_checkpoint.json")
UNRESOLVED_OUT = Path("qa_outputs/species_deepseek_unresolved.csv")

BASE_URL = "https://api.deepseek.com"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
SLEEP_SECONDS = float(os.environ.get("DEEPSEEK_SLEEP_SECONDS", "0.6"))

ALLOWED_SPECIES = {
    "human",
    "mouse",
    "human, mouse",
    "rat",
    "porcine",
    "bovine",
    "zebrafish",
    "other",
    "unclear",
    "",
}

SYSTEM_PROMPT = """\
You are a biomedical curator annotating species for cell reprogramming recipes.

Task:
Determine the organism whose cells were experimentally used for THIS recipe row.

Return ONLY valid JSON:
{"species": "...", "confidence": "high|medium|low", "rationale": "..."}

Allowed species values:
human | mouse | human, mouse | rat | porcine | bovine | zebrafish | other | unclear

Rules:
- Use the source/target cells, evidence sentence, title, abstract, and full-text excerpt.
- Species means the organism of the source/target cells used in the experiment.
- Do NOT infer human from disease, therapy, patient need, clinical application, or translational language alone.
- Do NOT infer mouse from "mouse model" alone if the recipe cells are clearly human xenograft/transplant cells.
- If both human and mouse cells were tested for the same recipe, return "human, mouse".
- If the species is not explicitly recoverable, return "unclear".
- Keep rationale under 25 words.
"""


def clean(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def normalize_species(value: str) -> str:
    text = clean(value).lower().replace(";", ",")
    text = " ".join(text.split())
    if not text or text in {"unknown", "not specified", "na", "n/a", "unclear"}:
        return "unclear"
    aliases = {
        "homo sapiens": "human",
        "humans": "human",
        "mus musculus": "mouse",
        "mice": "mouse",
        "murine": "mouse",
        "human and mouse": "human, mouse",
        "mouse and human": "human, mouse",
        "human/mouse": "human, mouse",
        "mouse/human": "human, mouse",
        "rats": "rat",
        "pig": "porcine",
        "swine": "porcine",
        "cow": "bovine",
    }
    text = aliases.get(text, text)
    if "," in text:
        parts = [aliases.get(p.strip(), p.strip()) for p in text.split(",") if p.strip()]
        parts = [p for p in parts if p and p != "unclear"]
        order = ["human", "mouse", "rat", "porcine", "bovine", "zebrafish", "other"]
        parts = sorted(set(parts), key=lambda p: order.index(p) if p in order else len(order))
        text = ", ".join(parts)
    return text if text in ALLOWED_SPECIES else "other"


def parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def truncate(text: str, limit: int) -> str:
    text = clean(text)
    return text if len(text) <= limit else text[:limit] + " ..."


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
            clean(row.get("factors", "")),
        ]
    )


def build_user_message(row: pd.Series, paper_text: str, fulltext: str) -> str:
    parts = [
        f"PMID: {clean(row.get('pmid', ''))}",
        f"Year: {clean(row.get('year', ''))}",
        f"Paper type: {clean(row.get('paper_type', ''))}",
        f"Recipe confidence: {clean(row.get('confidence', ''))}",
        f"Source cell: {clean(row.get('source_cell', ''))}",
        f"Source cell raw/std: {clean(row.get('source_cell_raw', ''))} | {clean(row.get('source_cell_std', ''))}",
        f"Target cell: {clean(row.get('target_cell', ''))}",
        f"Target cell raw/std: {clean(row.get('target_cell_raw', ''))} | {clean(row.get('target_cell_std', ''))}",
        f"Factors: {clean(row.get('factors', ''))}",
        f"Factor type: {clean(row.get('factor_type', ''))}",
        f"Evidence sentence: {truncate(row.get('evidence_sentence', ''), 1200)}",
        f"Notes: {truncate(row.get('notes', ''), 600)}",
    ]
    if paper_text:
        parts.append("Paper text:\n" + truncate(paper_text, 2600))
    if fulltext:
        parts.append("Full-text excerpt:\n" + truncate(fulltext, 3200))
    return "\n".join(parts)


def call_deepseek(client: OpenAI, row: pd.Series, paper_text: str, fulltext: str) -> dict:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(row, paper_text, fulltext)},
        ],
        temperature=0,
        max_tokens=120,
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
    species = normalize_species(parsed.get("species", "unclear"))
    confidence = clean(parsed.get("confidence", "low")).lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    return {
        "species": species,
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
    if "species_inference_method" not in df.columns:
        df["species_inference_method"] = ""

    paper_lookup = load_lookup(PAPERS_FILE, ["title", "abstract"])
    fulltext_lookup = load_lookup(FULLTEXT_FILE, ["methods_text", "results_text"])
    cache = load_checkpoint()
    client = OpenAI(api_key=api_key, base_url=BASE_URL)

    blank_indices = [idx for idx, row in df.iterrows() if not clean(row.get("species", ""))]
    todo = []
    for idx in blank_indices:
        key = row_key(idx, df.loc[idx])
        if key not in cache:
            todo.append(idx)
    if args.limit > 0:
        todo = todo[: args.limit]

    print(f"Blank species rows: {len(blank_indices)}")
    print(f"Cached rows: {len(cache)}")
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
                fulltext_lookup.get(pmid, ""),
            )
            cache[key] = result
            save_checkpoint(cache)
            print(f"{result['species']} ({result['confidence']})")
        except Exception as exc:
            print(f"ERROR: {exc}")
            time.sleep(4)
            continue
        time.sleep(SLEEP_SECONDS)

    filled = 0
    unresolved_rows = []
    for idx in blank_indices:
        row = df.loc[idx]
        key = row_key(idx, row)
        result = cache.get(key)
        if not result:
            unresolved_rows.append(idx)
            continue
        species = normalize_species(result.get("species", "unclear"))
        confidence = clean(result.get("confidence", "low")).lower()
        if species and species != "unclear" and confidence in {"high", "medium"}:
            df.at[idx, "species"] = species
            df.at[idx, "species_inference_method"] = f"deepseek_{confidence}"
            filled += 1
        else:
            unresolved_rows.append(idx)

    unresolved = df.loc[unresolved_rows].copy()
    unresolved.to_csv(UNRESOLVED_OUT, index=False, encoding="utf-8")

    if not args.no_write:
        backup = Path(str(FILE) + ".pre_deepseek_species.bak")
        if not backup.exists():
            shutil.copy(FILE, backup)
        df.to_csv(FILE, index=False, encoding="utf-8")

    remaining_blank = (df["species"].astype(str).str.strip() == "").sum()
    high_medium_research_blank = (
        (df["species"].astype(str).str.strip() == "")
        & (df["paper_type"] == "research")
        & (df["confidence"].isin(["high", "medium"]))
    ).sum()
    print()
    print(f"Filled from DeepSeek checkpoint: {filled}")
    print(f"Remaining blank species: {remaining_blank}")
    print(f"Remaining blank high/medium research rows: {high_medium_research_blank}")
    print(f"Unresolved rows exported to: {UNRESOLVED_OUT}")
    if args.no_write:
        print("No-write mode: recipes_master_v2.csv was not changed.")


if __name__ == "__main__":
    main()
