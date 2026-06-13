"""
Model adjudication for immune/T-cell recipe QA rows.

This script does not modify recipes_master_v2.csv. It asks a stronger model to
pre-review immune/T-cell-related rows and writes a checkpoint plus CSV/XLSX QA
outputs so humans only need to inspect uncertain or high-impact cases.
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

import pandas as pd
from openai import OpenAI

FILE = Path("recipes_master_v2.csv")
PAPERS_FILE = Path("papers.csv")
FULLTEXT_FILE = Path("fulltext.csv")
OUTPUT_DIR = Path("qa_outputs")
CHECKPOINT = OUTPUT_DIR / "immune_adjudication_checkpoint.json"
CSV_OUT = OUTPUT_DIR / "immune_recipe_QA_model_adjudicated.csv"
XLSX_OUT = OUTPUT_DIR / "immune_recipe_QA_model_adjudicated.xlsx"

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_ADJUDICATOR_MODEL") or os.environ.get("DEEPSEEK_MODEL", "deepseek-reasoner")
FALLBACK_MODEL = os.environ.get("DEEPSEEK_ADJUDICATOR_FALLBACK_MODEL", "deepseek-chat")
SLEEP_SECONDS = float(os.environ.get("DEEPSEEK_SLEEP_SECONDS", "0.8"))
USE_JSON_MODE = os.environ.get("DEEPSEEK_JSON_MODE", "1").strip().lower() not in {"0", "false", "no"}
API_TIMEOUT_SECONDS = float(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "75"))

DECISIONS = {"auto_accept", "auto_hide", "auto_merge_duplicate", "needs_manual"}
CONFIDENCES = {"high", "medium", "low"}
VALID_TYPES = {"TF", "small_molecule", "miRNA", "knockdown", "cytokine", "other"}
HIDDEN_VALIDATION_ACTIONS = {"remove", "hide_incomplete_recipe", "hide_single_tf", "hide_model_adjudicated"}

SYSTEM_PROMPT = """\
You are a senior biomedical curator adjudicating cell reprogramming database QA.

For one database row, decide whether it is safe to accept automatically, hide
automatically, merge as a duplicate, or send to manual review.

Return ONLY valid JSON:
{
  "decision": "auto_accept|auto_hide|auto_merge_duplicate|needs_manual",
  "confidence": "high|medium|low",
  "recipe_valid": "yes|no|unclear",
  "factor_assessment": "complete|incomplete|missing|not_recipe|unclear",
  "cell_assessment": "ok|needs_standardization|wrong|unclear",
  "duplicate_assessment": "unique|merge_ok|split_needed|unclear",
  "suggested_factors": "",
  "suggested_factor_type": "",
  "suggested_species": "",
  "manual_reason": "",
  "rationale": ""
}

Rules:
- Use only the supplied row, title/abstract, evidence sentence, validation flags,
  duplicate context, and full-text excerpt. Do not use outside memory.
- auto_accept only when source cell, target cell, and recipe factors are concrete
  enough to show to a researcher.
- A single transcription factor is acceptable only if the evidence says it is a
  standalone conversion recipe, not just one member of a cocktail or a mechanism.
- auto_hide when it is not a standalone recipe, factors are only vague, or the
  row is a review/background mention without concrete recipe evidence.
- auto_merge_duplicate only when duplicate context strongly suggests synonym or
  formatting variation of the same source/target/factor recipe.
- needs_manual for ambiguous source/target, incomplete factors, weak evidence,
  conflicting duplicate context, missing full text, or biologically distinct
  subtypes that may have been over-merged.
- If current factors are missing but the supplied evidence names concrete factors,
  put them in suggested_factors and matching labels in suggested_factor_type.
- suggested_factor_type must use comma-separated labels from:
  TF, small_molecule, miRNA, knockdown, cytokine, other.
- Keep rationale under 35 words. Keep manual_reason under 20 words.
"""


def clean(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def clean_lower(value) -> str:
    return clean(value).lower()


def is_true(value) -> bool:
    return clean_lower(value) == "true"


def truncate(text: str, limit: int) -> str:
    text = clean(text)
    return text if len(text) <= limit else text[:limit] + " ..."


def factors_missing(value: str) -> bool:
    return clean_lower(value) in {"", "not specified", "unknown", "not specified in text", "none", "n/a"}


def split_factors(value: str) -> list[str]:
    text = clean(value)
    parts = []
    buf = ""
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth > 0:
            depth -= 1
        if (ch == "," or ch == ";") and depth == 0:
            if buf.strip():
                parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    return parts


def normalize_factor(value: str) -> str:
    f = clean(value)
    if not f:
        return ""
    if f.lower() in {"not specified", "unknown", "not specified in text"}:
        return "not specified"
    f = (
        f.replace("\u03b1", "A")
        .replace("\u03b2", "B")
        .replace("\u03b3", "G")
        .replace("\u03b4", "D")
        .strip()
    )
    f = re.sub(r"\s+", " ", f)
    f = re.sub(r"\s*\((?:pou5f1|oct3/4|oct4|oskm|yamanaka'?s? factors?)\)\s*$", "", f, flags=re.I)
    compact = re.sub(r"[^A-Za-z0-9]+", "", f).upper()
    aliases = {
        "OCT34": "OCT4",
        "OCT3": "OCT4",
        "OCT4": "OCT4",
        "POU5F1": "OCT4",
        "SOX2": "SOX2",
        "KLF4": "KLF4",
        "CMYC": "C-MYC",
        "MYC": "C-MYC",
        "LMYC": "L-MYC",
        "NMYC": "N-MYC",
        "NANOG": "NANOG",
        "LIN28": "LIN28",
        "LIN28A": "LIN28",
        "ASCL1": "ASCL1",
        "MASH1": "ASCL1",
        "BRN2": "BRN2",
        "POU3F2": "BRN2",
        "MYT1L": "MYT1L",
        "NEUROG2": "NGN2",
        "NEUROGENIN2": "NGN2",
        "NGN2": "NGN2",
        "NEUROD1": "NEUROD1",
        "GATA4": "GATA4",
        "MEF2C": "MEF2C",
        "TBX5": "TBX5",
        "HAND2": "HAND2",
        "PDX1": "PDX1",
        "NGN3": "NGN3",
        "NEUROG3": "NGN3",
        "MAFA": "MAFA",
        "CEBPA": "CEBPA",
        "CEBPALPHA": "CEBPA",
        "CEBPB": "CEBPB",
        "PU1": "SPI1",
        "SPI1": "SPI1",
        "IRF8": "IRF8",
        "BATF3": "BATF3",
        "IKZF1": "IKZF1",
    }
    return aliases.get(compact, f.upper())


def factor_key(value: str) -> str:
    parts = [normalize_factor(part) for part in split_factors(value)]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    if len(parts) == 1 and parts[0] == "not specified":
        return "not specified"
    return " | ".join(sorted(parts))


def curated_default(row: pd.Series) -> bool:
    return (
        clean_lower(row.get("confidence")) in {"high", "medium"}
        and clean_lower(row.get("paper_type")) == "research"
        and not is_true(row.get("is_broad_duplicate"))
        and not factors_missing(row.get("factors"))
        and clean(row.get("single_tf_status")) not in {"cocktail_member", "unclear"}
        and not is_true(row.get("validation_needs_review"))
        and clean_lower(row.get("validation_action")) not in HIDDEN_VALIDATION_ACTIONS
    )


def immune_related(row: pd.Series) -> bool:
    text = " ".join(
        [
            clean(row.get("source_cell")),
            clean(row.get("target_cell")),
            clean(row.get("source_cell_std")),
            clean(row.get("target_cell_std")),
            clean(row.get("source_cell_broad")),
            clean(row.get("target_cell_broad")),
            clean(row.get("title")),
            clean(row.get("evidence_sentence")),
        ]
    ).lower()
    return bool(
        re.search(
            r"\bt[- ]?cells?\b|\bt lymph|\bb[- ]?cells?\b|\bb lymph|natural killer|\bnk[- ]?cells?\b|macrophage|monocyte|dendritic|\bdc\b|granulocyte|neutrophil|erythro|megakaryo|hematopoietic|haematopoietic|myeloid|lymphoid|thymocyte|t follicular|regulatory t|\btreg\b|immune|immun",
            text,
        )
    )


def tcell_related(row: pd.Series) -> bool:
    # Strict T-cell focus should describe the converted cells themselves.
    # Title/evidence mentions can be false positives from factor names (for
    # example GATA-3 or TCL-1A) or background biology.
    text = " ".join(
        [
            clean(row.get("source_cell")),
            clean(row.get("target_cell")),
            clean(row.get("source_cell_std")),
            clean(row.get("target_cell_std")),
            clean(row.get("source_cell_broad")),
            clean(row.get("target_cell_broad")),
        ]
    ).lower()
    return bool(
        re.search(
            r"\bt[- ]?cells?\b|\bt lymph|treg|regulatory t|cd4\+|cd8\+|th1\b|th2\b|th17\b|tfh\b|t follicular|thymocyte|\bcar[- ]?t\b|t-lineage|t regulatory",
            text,
        )
    )


def row_issue_flags(row: pd.Series) -> list[str]:
    flags = []
    if factors_missing(row.get("factors")):
        flags.append("missing_factors")
    if not clean(row.get("species")):
        flags.append("species_blank")
    if clean(row.get("conversion_scope")) == "unclear":
        flags.append("scope_unclear")
    if clean(row.get("single_tf_status")) in {"cocktail_member", "unclear"}:
        flags.append(f"single_tf_{clean(row.get('single_tf_status'))}")
    if is_true(row.get("validation_needs_review")):
        flags.append("validation_needs_review")
    if clean_lower(row.get("validation_action")) in HIDDEN_VALIDATION_ACTIONS:
        flags.append(f"validation_action_{clean(row.get('validation_action'))}")
    if is_true(row.get("is_broad_duplicate")):
        flags.append("broad_duplicate")
    if clean_lower(row.get("confidence")) == "low":
        flags.append("low_confidence")
    if clean_lower(row.get("paper_type")) != "research":
        flags.append(f"paper_type_{clean(row.get('paper_type')) or 'blank'}")
    return flags


def review_priority(row: pd.Series) -> int:
    flags = row_issue_flags(row)
    score = 0
    if curated_default(row):
        score += 40
    if tcell_related(row):
        score += 20
    if "validation_needs_review" in flags:
        score += 25
    if "missing_factors" in flags:
        score += 18
    if "species_blank" in flags:
        score += 14
    if any(flag.startswith("single_tf_") for flag in flags):
        score += 12
    if is_true(row.get("is_broad_duplicate")):
        score += 8
    if clean_lower(row.get("confidence")) == "high":
        score += 5
    return score


def load_papers() -> dict[str, str]:
    if not PAPERS_FILE.exists():
        return {}
    papers = pd.read_csv(PAPERS_FILE, dtype=str).fillna("")
    return {
        clean(row.get("pmid")): "\n".join(
            [
                f"Title: {truncate(row.get('title'), 350)}",
                f"Abstract: {truncate(row.get('abstract'), 2600)}",
            ]
        )
        for _, row in papers.iterrows()
        if clean(row.get("pmid"))
    }


def load_fulltexts() -> dict[str, str]:
    if not FULLTEXT_FILE.exists():
        return {}
    fulltexts = pd.read_csv(FULLTEXT_FILE, dtype=str).fillna("")
    return {
        clean(row.get("pmid")): " ".join([clean(row.get("methods_text")), clean(row.get("results_text"))])
        for _, row in fulltexts.iterrows()
        if clean(row.get("pmid"))
    }


def keywords_from_cell(value: str) -> list[str]:
    stop = {"cell", "cells", "induced", "like", "derived", "human", "mouse", "rat", "stem"}
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
        "lentiv",
        "retrovir",
        "cocktail",
        "convert",
        "conversion",
        "reprogram",
        "transdifferentiat",
        "differentiation",
        "macrophage",
        "monocyte",
        "lymphocyte",
        "t cell",
        "b cell",
        "hematopoietic",
        "immune",
        "mir",
        "shrna",
        "knockdown",
        "crispr",
    ]
    keywords.extend(keywords_from_cell(row.get("source_cell", "")))
    keywords.extend(keywords_from_cell(row.get("target_cell", "")))
    keywords.extend(split_factors(row.get("factors", ""))[:12])

    sentences = re.split(r"(?<=[.!?])\s+", text)
    scored = []
    for pos, sent in enumerate(sentences):
        low = sent.lower()
        score = sum(1 for keyword in keywords if keyword and keyword.lower() in low)
        if score:
            scored.append((score, pos, sent))
    if not scored:
        return truncate(text, 4500)
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected_positions = sorted(pos for _, pos, _ in scored[:28])
    selected = [sentences[pos] for pos in selected_positions]
    return truncate(" ".join(selected), 5400)


def build_duplicate_groups(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    tmp = df.copy()
    tmp["_group_key"] = tmp.apply(
        lambda r: f"{clean(r.get('source_cell_broad'))}||{clean(r.get('target_cell_broad'))}||{factor_key(r.get('factors'))}",
        axis=1,
    )
    return {key: group for key, group in tmp.groupby("_group_key", dropna=False)}


def duplicate_context(row: pd.Series, group: pd.DataFrame | None) -> str:
    if group is None or len(group) <= 1:
        return "No duplicate group context."
    rows = []
    for _, peer in group.head(10).iterrows():
        rows.append(
            " | ".join(
                [
                    f"pmid={clean(peer.get('pmid'))}",
                    f"year={clean(peer.get('year'))}",
                    f"source={clean(peer.get('source_cell'))}",
                    f"target={clean(peer.get('target_cell'))}",
                    f"source_std={clean(peer.get('source_cell_std'))}",
                    f"target_std={clean(peer.get('target_cell_std'))}",
                    f"broad_dup={clean(peer.get('is_broad_duplicate'))}",
                    f"factors={truncate(peer.get('factors'), 120)}",
                ]
            )
        )
    return "\n".join(
        [
            f"Broad duplicate group size: {len(group)}",
            f"Current broad duplicate flag: {clean(row.get('is_broad_duplicate'))}",
            "Peer rows:",
            *rows,
        ]
    )


def normalize_factor_type(value: str, factors: str = "") -> str:
    labels = []
    for raw in re.split(r",|;", clean(value).replace("|", ",")):
        label = raw.strip()
        if not label:
            continue
        if label == "TF (knockdown)":
            label = "knockdown"
        elif label in {"unknown", "not specified", "culture_medium"}:
            label = "other"
        if label not in VALID_TYPES:
            label = "other"
        labels.append(label)
    factor_count = len(split_factors(factors))
    if factor_count and len(labels) == 1 and factor_count > 1:
        labels *= factor_count
    if factor_count and labels and len(labels) != factor_count:
        return ""
    return ", ".join(labels)


def parse_json(raw: str) -> dict:
    text = clean(raw)
    if text.startswith("```"):
        pieces = text.split("```")
        text = pieces[1] if len(pieces) > 1 else text
        if text.strip().startswith("json"):
            text = text.strip()[4:]
    if "{" in text and "}" in text:
        text = text[text.find("{") : text.rfind("}") + 1]
    return json.loads(text.strip())


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        with CHECKPOINT.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_checkpoint(cache: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with CHECKPOINT.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def row_key(idx: int, row: pd.Series) -> str:
    return "|".join(
        [
            str(idx),
            clean(row.get("pmid")),
            clean(row.get("source_cell")),
            clean(row.get("target_cell")),
            clean(row.get("factors")),
            clean(row.get("title"))[:80],
        ]
    )


def build_user_message(row: pd.Series, paper_text: str, excerpt: str, dup_context: str) -> str:
    flags = row_issue_flags(row)
    parts = [
        f"Original row index: {clean(row.get('_row_index'))}",
        f"PMID: {clean(row.get('pmid'))}",
        f"Year: {clean(row.get('year'))}",
        f"Paper type: {clean(row.get('paper_type'))}",
        f"Recipe confidence: {clean(row.get('confidence'))}",
        f"Default visible by current app rules: {'yes' if curated_default(row) else 'no'}",
        f"T-cell focused: {'yes' if tcell_related(row) else 'no'}",
        f"QA issue flags: {'; '.join(flags) if flags else 'none'}",
        f"Conversion scope: {clean(row.get('conversion_scope'))}",
        f"Species: {clean(row.get('species'))}",
        f"Source cell raw/std/broad: {clean(row.get('source_cell'))} | {clean(row.get('source_cell_std'))} | {clean(row.get('source_cell_broad'))}",
        f"Target cell raw/std/broad: {clean(row.get('target_cell'))} | {clean(row.get('target_cell_std'))} | {clean(row.get('target_cell_broad'))}",
        f"Current factors: {clean(row.get('factors'))}",
        f"Current factor_type: {clean(row.get('factor_type'))}",
        f"Single TF status: {clean(row.get('single_tf_status'))}",
        f"Validation action/resolution: {clean(row.get('validation_action'))} | {clean(row.get('validation_resolution'))}",
        f"Validation notes: {truncate(row.get('validation_notes'), 800)}",
        f"Evidence sentence: {truncate(row.get('evidence_sentence'), 1300)}",
        f"Database notes: {truncate(row.get('notes'), 700)}",
        "Duplicate context:\n" + dup_context,
    ]
    if paper_text:
        parts.append("Paper title/abstract:\n" + paper_text)
    if excerpt:
        parts.append("Full-text excerpt:\n" + excerpt)
    else:
        parts.append("Full-text excerpt: [not available]")
    return "\n".join(parts)


def normalize_model_result(parsed: dict, raw: str, model_name: str) -> dict:
    decision = clean_lower(parsed.get("decision"))
    confidence = clean_lower(parsed.get("confidence"))
    if decision not in DECISIONS:
        decision = "needs_manual"
    if confidence not in CONFIDENCES:
        confidence = "low"

    suggested_factors = clean(parsed.get("suggested_factors"))
    suggested_factor_type = normalize_factor_type(parsed.get("suggested_factor_type"), suggested_factors)
    if suggested_factors and not suggested_factor_type:
        confidence = "low"

    return {
        "decision": decision,
        "confidence": confidence,
        "recipe_valid": clean_lower(parsed.get("recipe_valid")) or "unclear",
        "factor_assessment": clean_lower(parsed.get("factor_assessment")) or "unclear",
        "cell_assessment": clean_lower(parsed.get("cell_assessment")) or "unclear",
        "duplicate_assessment": clean_lower(parsed.get("duplicate_assessment")) or "unclear",
        "suggested_factors": suggested_factors,
        "suggested_factor_type": suggested_factor_type,
        "suggested_species": clean(parsed.get("suggested_species")),
        "manual_reason": truncate(parsed.get("manual_reason"), 160),
        "rationale": truncate(parsed.get("rationale"), 240),
        "raw": raw,
        "model": model_name,
    }


def call_model_once(client: OpenAI, row: pd.Series, paper_text: str, excerpt: str, dup_context: str, model_name: str) -> dict:
    request = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(row, paper_text, excerpt, dup_context)},
        ],
        "temperature": 0,
        "max_tokens": 900,
    }
    if USE_JSON_MODE:
        request["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**request)
    message = response.choices[0].message
    raw = clean(getattr(message, "content", ""))
    if not raw:
        raw = clean(getattr(message, "reasoning_content", ""))
    if not raw:
        raise ValueError("empty model response")
    parsed = parse_json(raw)
    return normalize_model_result(parsed, raw, model_name)


def call_model(client: OpenAI, row: pd.Series, paper_text: str, excerpt: str, dup_context: str) -> dict:
    try:
        return call_model_once(client, row, paper_text, excerpt, dup_context, MODEL)
    except json.JSONDecodeError:
        if FALLBACK_MODEL and FALLBACK_MODEL != MODEL:
            return call_model_once(client, row, paper_text, excerpt, dup_context, FALLBACK_MODEL)
        raise


def manual_recommended(row: pd.Series, result: dict) -> str:
    decision = result.get("decision", "")
    confidence = result.get("confidence", "")
    if decision == "needs_manual":
        return "yes"
    if confidence == "low":
        return "yes"
    if tcell_related(row) and confidence != "high":
        return "yes"
    if result.get("duplicate_assessment") in {"split_needed", "unclear"} and is_true(row.get("is_broad_duplicate")):
        return "yes"
    return "no"


def build_output_rows(df: pd.DataFrame, cache: dict) -> pd.DataFrame:
    out_rows = []
    for idx, row in df.iterrows():
        key = row_key(idx, row)
        result = cache.get(key, {})
        issues = row_issue_flags(row)
        out_rows.append(
            {
                "row_index": idx,
                "model_decision": result.get("decision", "pending"),
                "model_confidence": result.get("confidence", ""),
                "manual_recommended": manual_recommended(row, result) if result else "yes",
                "manual_reason": result.get("manual_reason", "not yet adjudicated"),
                "model_rationale": result.get("rationale", ""),
                "recipe_valid": result.get("recipe_valid", ""),
                "factor_assessment": result.get("factor_assessment", ""),
                "cell_assessment": result.get("cell_assessment", ""),
                "duplicate_assessment": result.get("duplicate_assessment", ""),
                "suggested_factors": result.get("suggested_factors", ""),
                "suggested_factor_type": result.get("suggested_factor_type", ""),
                "suggested_species": result.get("suggested_species", ""),
                "adjudicator_model": result.get("model", MODEL if result else ""),
                "review_priority": review_priority(row),
                "default_visible": "yes" if curated_default(row) else "no",
                "tcell_focused": "yes" if tcell_related(row) else "no",
                "issue_flags": "; ".join(issues),
                "pmid": clean(row.get("pmid")),
                "year": clean(row.get("year")),
                "paper_type": clean(row.get("paper_type")),
                "confidence": clean(row.get("confidence")),
                "source_cell": clean(row.get("source_cell")),
                "target_cell": clean(row.get("target_cell")),
                "source_cell_std": clean(row.get("source_cell_std")),
                "target_cell_std": clean(row.get("target_cell_std")),
                "source_cell_broad": clean(row.get("source_cell_broad")),
                "target_cell_broad": clean(row.get("target_cell_broad")),
                "factors": clean(row.get("factors")),
                "factor_type": clean(row.get("factor_type")),
                "species": clean(row.get("species")),
                "conversion_scope": clean(row.get("conversion_scope")),
                "single_tf_status": clean(row.get("single_tf_status")),
                "is_broad_duplicate": clean(row.get("is_broad_duplicate")),
                "broad_duplicate_reason": clean(row.get("broad_duplicate_reason")),
                "broad_duplicate_group_id": clean(row.get("broad_duplicate_group_id")),
                "validation_needs_review": clean(row.get("validation_needs_review")),
                "validation_action": clean(row.get("validation_action")),
                "validation_notes": truncate(row.get("validation_notes"), 260),
                "validation_resolution": clean(row.get("validation_resolution")),
                "title": truncate(row.get("title"), 260),
                "evidence_sentence": truncate(row.get("evidence_sentence"), 540),
                "notes": truncate(row.get("notes"), 260),
            }
        )
    out = pd.DataFrame(out_rows)
    return out.sort_values(
        by=["manual_recommended", "model_confidence", "review_priority", "tcell_focused"],
        ascending=[False, True, False, False],
    )


def write_outputs(out: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(CSV_OUT, index=False, encoding="utf-8")
    try:
        with pd.ExcelWriter(XLSX_OUT, engine="openpyxl") as writer:
            summary_rows = []
            summary_rows.append(["Total adjudicated rows", len(out)])
            summary_rows.append(["Pending rows", int((out["model_decision"] == "pending").sum())])
            summary_rows.append(["Manual recommended", int((out["manual_recommended"] == "yes").sum())])
            for decision, count in out["model_decision"].value_counts().items():
                summary_rows.append([f"Decision: {decision}", int(count)])
            for confidence, count in out["model_confidence"].replace("", "(blank)").value_counts().items():
                summary_rows.append([f"Model confidence: {confidence}", int(count)])
            summary = pd.DataFrame(summary_rows, columns=["Metric", "Value"])
            summary.to_excel(writer, index=False, sheet_name="Summary")
            out.to_excel(writer, index=False, sheet_name="All_Model_Adjudication")
            out[out["manual_recommended"] == "yes"].to_excel(writer, index=False, sheet_name="Manual_Recommended")
            out[out["model_decision"] == "auto_accept"].to_excel(writer, index=False, sheet_name="Auto_Accept")
            out[out["model_decision"] == "auto_hide"].to_excel(writer, index=False, sheet_name="Auto_Hide")
            out[out["model_decision"] == "auto_merge_duplicate"].to_excel(writer, index=False, sheet_name="Auto_Merge")

            for sheet in writer.book.worksheets:
                sheet.freeze_panes = "A2"
                sheet.auto_filter.ref = sheet.dimensions
                for col_cells in sheet.columns:
                    col_letter = col_cells[0].column_letter
                    max_len = max(len(str(cell.value or "")) for cell in col_cells[:60])
                    sheet.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 48)
    except Exception as exc:
        print(f"WARNING: xlsx export failed; CSV was written. Error: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Process at most N uncached rows; 0 means all.")
    parser.add_argument("--no-model", action="store_true", help="Only rebuild outputs from existing checkpoint.")
    args = parser.parse_args()

    df = pd.read_csv(FILE, dtype=str).fillna("")
    df["_row_index"] = df.index.astype(str)
    immune = df[df.apply(immune_related, axis=1)].copy()
    immune = immune.sort_values(by=["_row_index"]).copy()
    immune = immune.assign(_priority=immune.apply(review_priority, axis=1))
    immune = immune.sort_values(by=["_priority", "_row_index"], ascending=[False, True])

    cache = load_checkpoint()
    if not args.no_model:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise SystemExit("ERROR: export DEEPSEEK_API_KEY=sk-...")
        paper_lookup = load_papers()
        fulltext_lookup = load_fulltexts()
        groups = build_duplicate_groups(df)
        client = OpenAI(api_key=api_key, base_url=BASE_URL, timeout=API_TIMEOUT_SECONDS)

        todo = []
        for idx, row in immune.iterrows():
            key = row_key(idx, row)
            if key not in cache:
                todo.append(idx)
        if args.limit > 0:
            todo = todo[: args.limit]

        print(f"Adjudicator model: {MODEL}")
        print(f"Immune/T-cell QA rows: {len(immune)}")
        print(f"Cached rows: {len(cache)}")
        print(f"Rows to process now: {len(todo)}")

        for n, idx in enumerate(todo, 1):
            row = immune.loc[idx]
            pmid = clean(row.get("pmid"))
            key = row_key(idx, row)
            group_key = f"{clean(row.get('source_cell_broad'))}||{clean(row.get('target_cell_broad'))}||{factor_key(row.get('factors'))}"
            print(f"[{n}/{len(todo)}] row {idx} PMID {pmid} ... ", end="", flush=True)
            try:
                result = call_model(
                    client,
                    row,
                    paper_lookup.get(pmid, ""),
                    fulltext_excerpt(row, fulltext_lookup.get(pmid, "")),
                    duplicate_context(row, groups.get(group_key)),
                )
                cache[key] = result
                save_checkpoint(cache)
                print(f"{result['decision']} ({result['confidence']})")
            except Exception as exc:
                print(f"ERROR: {exc}")
                time.sleep(5)
                continue
            time.sleep(SLEEP_SECONDS)

    out = build_output_rows(immune, cache)
    write_outputs(out)
    print()
    print(f"Wrote CSV: {CSV_OUT}")
    print(f"Wrote XLSX: {XLSX_OUT}")
    print("Decision counts:")
    print(out["model_decision"].value_counts(dropna=False).to_string())
    print("Manual recommended:", int((out["manual_recommended"] == "yes").sum()))


if __name__ == "__main__":
    main()
