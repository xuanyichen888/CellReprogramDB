"""
Fill missing species annotations in recipes_master_v2.csv.

Conservative workflow:
1. Normalize existing species values.
2. Infer from explicit source/target cell wording.
3. Fill remaining blanks by same-PMID vote.
4. Infer from local title/abstract/evidence/fulltext only when the species cue is clear.
5. Export a residual blank-species review CSV.

This avoids LLM/API cost for the easy cases and leaves ambiguous rows untouched.
"""

import re
import shutil
from collections import Counter
from pathlib import Path

import pandas as pd

FILE = Path("recipes_master_v2.csv")
PAPERS_FILE = Path("papers.csv")
FULLTEXT_FILE = Path("fulltext.csv")
REVIEW_OUT = Path("qa_outputs/species_missing_review.csv")
GENERATED_METHODS = {
    "cell_fields",
    "same_pmid_vote",
    "title_abstract_evidence",
    "fulltext",
}


def clean(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def normalize_species(value: str) -> str:
    text = clean(value).lower().replace(";", ",")
    if not text:
        return ""
    parts = [p.strip() for p in re.split(r",|/", text) if p.strip()]
    aliases = {
        "homo sapiens": "human",
        "human": "human",
        "humans": "human",
        "mus musculus": "mouse",
        "mouse": "mouse",
        "mice": "mouse",
        "murine": "mouse",
        "rat": "rat",
        "rats": "rat",
        "porcine": "porcine",
        "pig": "porcine",
        "swine": "porcine",
        "bovine": "bovine",
        "cow": "bovine",
        "zebrafish": "zebrafish",
    }
    normalized = []
    for part in parts:
        mapped = aliases.get(part, part)
        if mapped and mapped not in normalized:
            normalized.append(mapped)
    order = ["human", "mouse", "rat", "porcine", "bovine", "zebrafish"]
    normalized = sorted(normalized, key=lambda x: order.index(x) if x in order else len(order))
    return ", ".join(normalized)


SPECIES_PATTERNS = {
    "human": [
        r"\bhuman\b",
        r"\bhescs?\b",
        r"\bhipscs?\b",
        r"\bhpscs?\b",
        r"\bhdfs?\b",
        r"\bhuvecs?\b",
        r"\bhuman\s+umbilical cord\b",
        r"\bhuman\s+cord blood\b",
    ],
    "mouse": [
        r"\bmouse\b",
        r"\bmice\b",
        r"\bmurine\b",
        r"\bmefs?\b",
        r"\bmescs?\b",
    ],
    "rat": [r"\brat\b", r"\brats\b"],
    "porcine": [r"\bporcine\b", r"\bpig\b", r"\bswine\b"],
    "bovine": [r"\bbovine\b", r"\bcow\b"],
    "zebrafish": [r"\bzebrafish\b"],
}

COMPILED = {
    species: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for species, patterns in SPECIES_PATTERNS.items()
}

CELL_CONTEXT = (
    r"cells?|cellular|fibroblasts?|astrocytes?|neurons?|cardiomyocytes?|"
    r"hepatocytes?|keratinocytes?|adipocytes?|myoblasts?|macrophages?|"
    r"monocytes?|dendritic cells?|lymphocytes?|b cells?|t cells?|glia|"
    r"stem cells?|ipscs?|escs?|organoids?|tissues?|islets?|beta cells?|"
    r"pancreatic|cardiac|neural|retinal|cochlear|schwann|muller|müller|"
    r"pericytes?|mesenchymal"
)

TEXT_SPECIES_PATTERNS = {
    "human": [
        r"\bhescs?\b",
        r"\bhipscs?\b",
        r"\bhpscs?\b",
        r"\bhdfs?\b",
        r"\bhuvecs?\b",
        rf"\bhuman\b(?:\W+\w+){{0,6}}\W+(?:{CELL_CONTEXT})\b",
        rf"\b(?:{CELL_CONTEXT})\b(?:\W+\w+){{0,6}}\W+\bhuman\b",
        r"\bhuman\s+umbilical cord\b",
        r"\bhuman\s+cord blood\b",
    ],
    "mouse": [
        r"\bmefs?\b",
        r"\bmescs?\b",
        rf"\b(?:mouse|mice|murine)\b(?:\W+\w+){{0,6}}\W+(?:{CELL_CONTEXT})\b",
        rf"\b(?:{CELL_CONTEXT})\b(?:\W+\w+){{0,6}}\W+\b(?:mouse|mice|murine)\b",
        r"\b(mouse|murine)\s+model\b",
    ],
    "rat": [
        rf"\brats?\b(?:\W+\w+){{0,6}}\W+(?:{CELL_CONTEXT})\b",
        rf"\b(?:{CELL_CONTEXT})\b(?:\W+\w+){{0,6}}\W+\brats?\b",
    ],
    "porcine": [
        rf"\b(?:porcine|pig|swine)\b(?:\W+\w+){{0,6}}\W+(?:{CELL_CONTEXT})\b",
        rf"\b(?:{CELL_CONTEXT})\b(?:\W+\w+){{0,6}}\W+\b(?:porcine|pig|swine)\b",
    ],
    "bovine": [
        rf"\b(?:bovine|cow)\b(?:\W+\w+){{0,6}}\W+(?:{CELL_CONTEXT})\b",
        rf"\b(?:{CELL_CONTEXT})\b(?:\W+\w+){{0,6}}\W+\b(?:bovine|cow)\b",
    ],
    "zebrafish": [r"\bzebrafish\b"],
}

TEXT_COMPILED = {
    species: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for species, patterns in TEXT_SPECIES_PATTERNS.items()
}

COMBINED_HUMAN_MOUSE = re.compile(
    r"\b(human\s+(and|/)\s+(mouse|murine)|"
    r"(mouse|murine)\s+(and|/)\s+human|"
    r"human\s+and\s+mouse\s+(cells?|fibroblasts?|somatic cells?|systems?)|"
    r"both\s+human\s+and\s+(mouse|murine))\b",
    re.IGNORECASE,
)

HUMAN_DERIVED = re.compile(
    r"\b(patient|donor)[ -]derived\b|"
    r"\bderived from (healthy )?(human )?(patients?|donors?)\b|"
    r"\bfrom (healthy )?(human )?(patients?|donors?)\b",
    re.IGNORECASE,
)


def species_hits(text: str, *, include_human_derived: bool = False) -> set[str]:
    hits = set()
    for species, patterns in COMPILED.items():
        if any(pattern.search(text) for pattern in patterns):
            hits.add(species)
    if include_human_derived and HUMAN_DERIVED.search(text):
        hits.add("human")
    return hits


def text_species_hits(text: str) -> set[str]:
    hits = set()
    for species, patterns in TEXT_COMPILED.items():
        if any(pattern.search(text) for pattern in patterns):
            hits.add(species)
    if HUMAN_DERIVED.search(text):
        hits.add("human")
    return hits


def infer_from_cell_fields(row: pd.Series) -> str:
    fields = [
        "source_cell",
        "target_cell",
        "source_cell_raw",
        "target_cell_raw",
        "source_cell_std",
        "target_cell_std",
        "source_cell_broad",
        "target_cell_broad",
    ]
    text = " ".join(clean(row.get(field, "")) for field in fields)
    hits = text_species_hits(text)
    if not hits:
        return ""
    if hits == {"human", "mouse"}:
        return "human, mouse"
    if len(hits) == 1:
        return next(iter(hits))
    return ""


def infer_from_text(text: str) -> str:
    text = clean(text)
    if not text:
        return ""
    hits = species_hits(text, include_human_derived=True)
    if not hits:
        return ""
    if hits == {"human", "mouse"}:
        if COMBINED_HUMAN_MOUSE.search(text):
            return "human, mouse"
        # Common xenograft/transplant phrasing mentions mice but the recipe is human-cell derived.
        if re.search(r"\b(human|patient|donor|hiPSC|hESC|HUVEC)\b", text, re.IGNORECASE) and re.search(
            r"\b(transplant|graft|injected|immunodeficient|nude mice|scid mice|mouse model|mice model)\b",
            text,
            re.IGNORECASE,
        ):
            return "human"
        return ""
    if len(hits) == 1:
        return next(iter(hits))
    return ""


def load_text_maps():
    paper_text = {}
    if PAPERS_FILE.exists():
        papers = pd.read_csv(PAPERS_FILE, dtype=str).fillna("")
        paper_text = {
            clean(row["pmid"]): " ".join([clean(row.get("title", "")), clean(row.get("abstract", ""))])
            for _, row in papers.iterrows()
        }

    fulltext = {}
    if FULLTEXT_FILE.exists():
        ft = pd.read_csv(FULLTEXT_FILE, dtype=str).fillna("")
        fulltext = {
            clean(row["pmid"]): " ".join([clean(row.get("methods_text", "")), clean(row.get("results_text", ""))])
            for _, row in ft.iterrows()
        }
    return paper_text, fulltext


def same_pmid_vote(df: pd.DataFrame, method_col: str, method_name: str) -> int:
    changed = 0
    for pmid, idx in df.groupby("pmid").groups.items():
        species_values = [
            normalize_species(v)
            for v in df.loc[idx, "species"].tolist()
            if normalize_species(v)
        ]
        if not species_values:
            continue
        counts = Counter(species_values)
        if len(counts) > 1 and counts.most_common(1)[0][1] == counts.most_common(2)[1][1]:
            continue
        majority = counts.most_common(1)[0][0]
        blank_idx = [i for i in idx if not clean(df.at[i, "species"])]
        if not blank_idx:
            continue
        df.loc[blank_idx, "species"] = majority
        df.loc[blank_idx, method_col] = method_name
        changed += len(blank_idx)
    return changed


def fill_with_inference(df: pd.DataFrame, method_col: str, method_name: str, fn) -> int:
    mask = df["species"].astype(str).str.strip() == ""
    changed = 0
    for idx, row in df[mask].iterrows():
        inferred = normalize_species(fn(row))
        if inferred:
            df.at[idx, "species"] = inferred
            df.at[idx, method_col] = method_name
            changed += 1
    return changed


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")
    REVIEW_OUT.parent.mkdir(parents=True, exist_ok=True)

    if "species_inference_method" not in df.columns:
        df["species_inference_method"] = ""
    generated = df["species_inference_method"].isin(GENERATED_METHODS)
    df.loc[generated, ["species", "species_inference_method"]] = ""

    original_blank = (df["species"].astype(str).str.strip() == "").sum()
    df["species"] = df["species"].apply(normalize_species)
    existing = df["species"].astype(str).str.strip() != ""
    df.loc[existing & (df["species_inference_method"].astype(str).str.strip() == ""), "species_inference_method"] = "existing"

    paper_text, fulltext = load_text_maps()

    counts = {}
    counts["cell_fields"] = fill_with_inference(df, "species_inference_method", "cell_fields", infer_from_cell_fields)
    counts["same_pmid_vote_1"] = same_pmid_vote(df, "species_inference_method", "same_pmid_vote")

    def paper_text_fn(row):
        local_text = " ".join(
            [
                clean(row.get("title", "")),
                clean(row.get("evidence_sentence", "")),
                clean(row.get("notes", "")),
                paper_text.get(clean(row.get("pmid", "")), ""),
            ]
        )
        return infer_from_text(local_text)

    counts["title_abstract_evidence"] = fill_with_inference(
        df,
        "species_inference_method",
        "title_abstract_evidence",
        paper_text_fn,
    )
    counts["same_pmid_vote_2"] = same_pmid_vote(df, "species_inference_method", "same_pmid_vote")

    def fulltext_fn(row):
        return infer_from_text(fulltext.get(clean(row.get("pmid", "")), ""))

    counts["fulltext"] = fill_with_inference(df, "species_inference_method", "fulltext", fulltext_fn)
    counts["same_pmid_vote_3"] = same_pmid_vote(df, "species_inference_method", "same_pmid_vote")

    remaining_blank = (df["species"].astype(str).str.strip() == "").sum()
    remaining = df[df["species"].astype(str).str.strip() == ""].copy()
    review_cols = [
        "pmid",
        "year",
        "paper_type",
        "confidence",
        "source_cell",
        "target_cell",
        "factors",
        "factor_type",
        "title",
        "evidence_sentence",
        "notes",
    ]
    remaining[[c for c in review_cols if c in remaining.columns]].to_csv(REVIEW_OUT, index=False, encoding="utf-8")

    backup = Path(str(FILE) + ".bak")
    if not backup.exists():
        shutil.copy(FILE, backup)
    df.to_csv(FILE, index=False, encoding="utf-8")

    print(f"original blank species: {original_blank}")
    for method, count in counts.items():
        print(f"{method}: {count}")
    print(f"remaining blank species: {remaining_blank}")
    print()
    print("species distribution:")
    print(df["species"].replace("", "(blank)").value_counts().to_string())
    print()
    high_medium_research_blank = remaining[
        (remaining["paper_type"] == "research") & (remaining["confidence"].isin(["high", "medium"]))
    ]
    print(f"remaining blank high/medium research rows: {len(high_medium_research_blank)}")
    print(f"residual review list: {REVIEW_OUT}")
    print(f"saved {FILE}")


if __name__ == "__main__":
    main()
