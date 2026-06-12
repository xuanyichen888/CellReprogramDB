"""
Fill missing factor lists in recipes_master_v2.csv.

Workflow:
1. Copy factors from an already curated row only when the same PMID + same
   standardized source/target has exactly one specific factor list.
2. Use DeepSeek for remaining high/medium research rows with missing factors.
3. Leave vague or uncertain outputs as "not specified".
"""

import argparse
import json
import os
import re
import shutil
import time
from pathlib import Path

import pandas as pd
from openai import OpenAI

from mark_duplicates import split_factors

FILE = Path("recipes_master_v2.csv")
PAPERS_FILE = Path("papers.csv")
FULLTEXT_FILE = Path("fulltext.csv")
CHECKPOINT = Path("qa_outputs/factors_deepseek_checkpoint.json")
UNRESOLVED_OUT = Path("qa_outputs/factors_deepseek_unresolved.csv")

BASE_URL = "https://api.deepseek.com"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
SLEEP_SECONDS = float(os.environ.get("DEEPSEEK_SLEEP_SECONDS", "0.6"))

VALID_TYPES = {"TF", "small_molecule", "miRNA", "knockdown", "cytokine", "other"}
MISSING_VALUES = {"", "not specified", "unknown", "none", "n/a", "not specified in text"}
GENERIC_FACTOR_PATTERNS = [
    r"^defined factors?$",
    r"^(defined|reprogramming|transcription|protein) factors?$",
    r"^factor pairs?$",
    r"^small molecules?$",
    r"^small molecule cocktail",
    r"^chemical cocktail",
    r"^chemical compounds?$",
    r"^cytokines?$",
    r"^growth factors?$",
    r"exact factors? not specified",
    r"not specified",
]

SYSTEM_PROMPT = """\
You are a biomedical curator repairing missing factor lists for a cell reprogramming database.

Task:
For THIS recipe row, identify the concrete factors used to convert the source cell into the target cell.

Return ONLY valid JSON:
{
  "status": "specific|unclear|not_recipe",
  "factors": "...",
  "factor_type": "...",
  "confidence": "high|medium|low",
  "rationale": "..."
}

Rules:
- Use only the provided title, abstract, evidence sentence, source/target cells, and full-text excerpt.
- Do NOT infer factors from outside knowledge if the provided text does not name them.
- Standard cocktail abbreviations may be expanded only if the text explicitly mentions them:
  OSKM/Yamanaka = OCT4, SOX2, KLF4, c-MYC
  OKSM = OCT4, KLF4, SOX2, c-MYC
  BAM = ASCL1, BRN2, MYT1L
  GMT/MGT = GATA4, MEF2C, TBX5
  GHMT = GATA4, HAND2, MEF2C, TBX5
- If the text only says "defined factors", "factor pairs", "transcription factors",
  "small molecules", or "chemical cocktail" without names, return status="unclear".
- If a factor is only a mechanistic regulator/barrier/enhancer and not the recipe used
  for conversion, return status="unclear".
- factors must be comma-separated concrete names.
- factor_type must have one label per factor in the same order:
  TF | small_molecule | miRNA | knockdown | cytokine | other
- Return status="specific" only when the factor list is concrete enough to show to users.
- Keep rationale under 25 words.
"""


def clean(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def is_missing_factor(value: str) -> bool:
    return clean(value).lower() in MISSING_VALUES


def split_csvish(value: str) -> list[str]:
    # Paren/slash-aware: keep "Oct3/4 (Pou5f1)" and "miR (a, b)" intact.
    return split_factors(clean(value))


def is_specific_factors(value: str) -> bool:
    text = clean(value)
    lower = text.lower()
    if is_missing_factor(text):
        return False
    for pattern in GENERIC_FACTOR_PATTERNS:
        if re.search(pattern, lower):
            return False
    parts = split_csvish(text)
    if not parts:
        return False
    # Keep named abbreviations and gene-like/proper compound names; reject pure prose.
    named = 0
    for part in parts:
        p = part.strip()
        if re.search(r"\b(OSKM|OKSM|BAM|GMT|MGT|GHMT)\b", p, re.IGNORECASE):
            named += 1
        elif re.search(r"\bmiR[- ]?\d+|let-7|shRNA|siRNA|CRISPR|knockdown\b", p, re.IGNORECASE):
            named += 1
        elif re.search(r"[A-Z0-9][A-Za-z0-9/-]{1,}", p):
            named += 1
    return named > 0


def normalize_factor_type(value: str, factors: str = "") -> str:
    labels = []
    for raw in split_csvish(clean(value).replace("|", ",")):
        label = raw.strip()
        if label == "TF (knockdown)":
            label = "knockdown"
        elif label in {"unknown", "not specified", "culture_medium"}:
            label = "other"
        if label not in VALID_TYPES:
            label = "other"
        labels.append(label)

    factor_count = len(split_csvish(factors))
    if factor_count and len(labels) == 1 and factor_count > 1:
        labels = labels * factor_count
    if factor_count and len(labels) != factor_count:
        return ""
    return ", ".join(labels)


def parse_json(raw: str) -> dict:
    text = clean(raw)
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def truncate(text: str, limit: int) -> str:
    text = clean(text)
    return text if len(text) <= limit else text[:limit] + " ..."


def load_papers() -> dict[str, str]:
    if not PAPERS_FILE.exists():
        return {}
    papers = pd.read_csv(PAPERS_FILE, dtype=str).fillna("")
    return {
        clean(row.get("pmid", "")): "\n".join(
            [
                f"Title: {clean(row.get('title', ''))}",
                f"Abstract: {truncate(row.get('abstract', ''), 2600)}",
            ]
        )
        for _, row in papers.iterrows()
    }


def load_fulltexts() -> dict[str, str]:
    if not FULLTEXT_FILE.exists():
        return {}
    fulltexts = pd.read_csv(FULLTEXT_FILE, dtype=str).fillna("")
    return {
        clean(row.get("pmid", "")): " ".join(
            [clean(row.get("methods_text", "")), clean(row.get("results_text", ""))]
        )
        for _, row in fulltexts.iterrows()
    }


def keywords_from_cell(value: str) -> list[str]:
    stop = {
        "cell",
        "cells",
        "induced",
        "like",
        "derived",
        "human",
        "mouse",
        "rat",
        "stem",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}", clean(value).lower())
    return [word for word in words if word not in stop][:8]


def fulltext_excerpt(row: pd.Series, text: str) -> str:
    text = clean(text)
    if not text:
        return ""
    keywords = [
        "factor",
        "transcription",
        "overexpress",
        "transduc",
        "infect",
        "lentiv",
        "retrovir",
        "doxycycline",
        "cocktail",
        "small molecule",
        "chemical",
        "treat",
        "protocol",
        "convert",
        "reprogram",
        "transdifferentiat",
        "mir",
        "microrna",
        "shrna",
        "sirna",
        "knockdown",
        "crispr",
    ]
    keywords.extend(keywords_from_cell(row.get("source_cell", "")))
    keywords.extend(keywords_from_cell(row.get("target_cell", "")))

    sentences = re.split(r"(?<=[.!?])\s+", text)
    scored = []
    for pos, sent in enumerate(sentences):
        low = sent.lower()
        score = sum(1 for keyword in keywords if keyword in low)
        if score:
            scored.append((score, pos, sent))
    if not scored:
        return truncate(text, 4200)

    scored.sort(key=lambda item: (-item[0], item[1]))
    selected_positions = sorted(pos for _, pos, _ in scored[:30])
    selected = [sentences[pos] for pos in selected_positions]
    return truncate(" ".join(selected), 5200)


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
            clean(row.get("title", ""))[:80],
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
        f"Species: {clean(row.get('species', ''))}",
        f"Current factors: {clean(row.get('factors', ''))}",
        f"Current factor_type: {clean(row.get('factor_type', ''))}",
        f"Evidence sentence: {truncate(row.get('evidence_sentence', ''), 1200)}",
        f"Notes: {truncate(row.get('notes', ''), 700)}",
    ]
    if paper_text:
        parts.append("Paper title/abstract:\n" + paper_text)
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
        max_tokens=220,
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

    factors = clean(parsed.get("factors", ""))
    factor_type = normalize_factor_type(parsed.get("factor_type", ""), factors)
    status = clean(parsed.get("status", "unclear")).lower()
    confidence = clean(parsed.get("confidence", "low")).lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    if status not in {"specific", "unclear", "not_recipe"}:
        status = "unclear"

    if not is_specific_factors(factors) or not factor_type:
        status = "unclear"
    return {
        "status": status,
        "factors": factors,
        "factor_type": factor_type,
        "confidence": confidence,
        "rationale": truncate(parsed.get("rationale", ""), 180),
        "raw": raw,
    }


def apply_strict_copy(df: pd.DataFrame) -> int:
    copied = 0
    if "factor_inference_method" not in df.columns:
        df["factor_inference_method"] = ""
    if "factor_inference_rationale" not in df.columns:
        df["factor_inference_rationale"] = ""

    missing = df["factors"].apply(is_missing_factor)
    nonmissing = df[~missing].copy()
    key_cols = ["pmid", "source_cell_std", "target_cell_std"]

    for idx, row in df[missing].iterrows():
        same = nonmissing
        for col in key_cols:
            same = same[same[col].astype(str) == clean(row.get(col, ""))]
        values = []
        for _, donor in same.iterrows():
            factors = clean(donor.get("factors", ""))
            factor_type = normalize_factor_type(donor.get("factor_type", ""), factors)
            if is_specific_factors(factors) and factor_type:
                values.append((factors, factor_type))
        values = sorted(set(values))
        if len(values) != 1:
            continue
        factors, factor_type = values[0]
        df.at[idx, "factors"] = factors
        df.at[idx, "factor_type"] = factor_type
        df.at[idx, "factor_inference_method"] = "same_pmid_source_target_copy"
        df.at[idx, "factor_inference_rationale"] = "Copied from same PMID and same standardized source/target row."
        copied += 1
    return copied


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Process at most N model rows; 0 means all.")
    parser.add_argument("--all-missing", action="store_true", help="Also process low/review/other rows.")
    parser.add_argument("--no-write", action="store_true", help="Do not write recipes_master_v2.csv.")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise SystemExit("ERROR: export DEEPSEEK_API_KEY=sk-...")

    df = pd.read_csv(FILE, dtype=str).fillna("")
    if "factor_inference_method" not in df.columns:
        df["factor_inference_method"] = ""
    if "factor_inference_rationale" not in df.columns:
        df["factor_inference_rationale"] = ""

    original_missing = df["factors"].apply(is_missing_factor).sum()
    copied = apply_strict_copy(df)

    missing = df["factors"].apply(is_missing_factor)
    if args.all_missing:
        candidate_mask = missing
    else:
        candidate_mask = missing & (df["paper_type"] == "research") & (df["confidence"].isin(["high", "medium"]))

    paper_lookup = load_papers()
    fulltext_lookup = load_fulltexts()
    cache = load_checkpoint()
    client = OpenAI(api_key=api_key, base_url=BASE_URL)

    candidate_indices = df[candidate_mask].index.tolist()
    todo = []
    for idx in candidate_indices:
        key = row_key(idx, df.loc[idx])
        if key not in cache:
            todo.append(idx)
    if args.limit > 0:
        todo = todo[: args.limit]

    print(f"Original missing factors: {original_missing}")
    print(f"Strict-copy filled: {copied}")
    print(f"Candidate rows after copy: {len(candidate_indices)}")
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
            print(f"{result['status']} | {result['factors'][:80]} ({result['confidence']})")
        except Exception as exc:
            print(f"ERROR: {exc}")
            time.sleep(4)
            continue
        time.sleep(SLEEP_SECONDS)

    model_filled = 0
    unresolved_rows = []
    for idx in candidate_indices:
        if not is_missing_factor(df.at[idx, "factors"]):
            continue
        key = row_key(idx, df.loc[idx])
        result = cache.get(key)
        if not result:
            unresolved_rows.append(idx)
            continue
        if result.get("status") == "specific" and result.get("confidence") in {"high", "medium"}:
            factors = clean(result.get("factors", ""))
            factor_type = normalize_factor_type(result.get("factor_type", ""), factors)
            if is_specific_factors(factors) and factor_type:
                df.at[idx, "factors"] = factors
                df.at[idx, "factor_type"] = factor_type
                df.at[idx, "factor_inference_method"] = f"deepseek_{result.get('confidence')}"
                df.at[idx, "factor_inference_rationale"] = clean(result.get("rationale", ""))
                model_filled += 1
                continue
        unresolved_rows.append(idx)

    unresolved = df.loc[unresolved_rows].copy()
    unresolved.to_csv(UNRESOLVED_OUT, index=False, encoding="utf-8")

    if not args.no_write:
        backup = Path(str(FILE) + ".pre_factor_fill.bak")
        if not backup.exists():
            shutil.copy(FILE, backup)
        df.to_csv(FILE, index=False, encoding="utf-8")

    remaining_missing = df["factors"].apply(is_missing_factor).sum()
    remaining_high_medium_research = (
        df["factors"].apply(is_missing_factor)
        & (df["paper_type"] == "research")
        & (df["confidence"].isin(["high", "medium"]))
    ).sum()
    print()
    print(f"Model-filled rows applied: {model_filled}")
    print(f"Remaining missing factors: {remaining_missing}")
    print(f"Remaining missing high/medium research rows: {remaining_high_medium_research}")
    print(f"Unresolved candidate rows exported to: {UNRESOLVED_OUT}")
    if args.no_write:
        print("No-write mode: recipes_master_v2.csv was not changed.")


if __name__ == "__main__":
    main()
