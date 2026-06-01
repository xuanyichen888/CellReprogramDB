"""
Add source_cell_std and target_cell_std columns to recipes_master_v2.csv.

Three-layer normalization (non-destructive — original columns are unchanged):
  1. String cleanup: strip whitespace, hyphen normalization in compound cell names
  2. Abbreviation expansion: PBMC, MEF, MSC, HUVEC, HSC, NSC, iPSC ...
  3. Synonym merging: curated whitelist of clearly equivalent name variants

The _std columns are used by the Streamlit app for filter dropdowns, so that
e.g. "T-cell" and "T cell" appear as a single filter option.
"""

import re
import shutil
import pandas as pd

FILE = "recipes_master_v2.csv"

# ── Layer 2: abbreviation expansion ──────────────────────────────────────────
# Matched as whole-word, case-insensitive; applied before synonym merging.
ABBREV = {
    r"(?<![-A-Za-z0-9])PBMCs?\b":  "peripheral blood mononuclear cell",
    r"(?<![-A-Za-z0-9])MEFs?\b":   "mouse embryonic fibroblast",
    r"(?<![-A-Za-z0-9])MSCs?\b":   "mesenchymal stem cell",
    r"(?<![-A-Za-z0-9])HUVECs?\b": "human umbilical vein endothelial cell",
    r"(?<![-A-Za-z0-9])HSCs?\b":   "hematopoietic stem cell",
    r"(?<![-A-Za-z0-9])NSCs?\b":   "neural stem cell",
    r"(?<![-A-Za-z0-9])iPSCs?\b":  "induced pluripotent stem cell",
    r"(?<![-A-Za-z0-9])ESCs?\b":   "embryonic stem cell",
    r"(?<![-A-Za-z0-9])hESCs?\b":  "human embryonic stem cell",
    r"(?<![-A-Za-z0-9])mESCs?\b":  "mouse embryonic stem cell",
    r"(?<![-A-Za-z0-9])NPCs?\b":   "neural progenitor cell",
}

# ── Layer 3: synonym → canonical form ────────────────────────────────────────
# Keys are exact post-cleanup strings; values are the canonical form.
# Only merge when biological equivalence is clear and unambiguous.
SYNONYMS = {
    # ── T cell punctuation ────────────────────────────────────────────────────
    "T-cell":                        "T cell",
    "T-Cell":                        "T cell",
    "T-cells":                       "T cell",
    "T cells":                       "T cell",
    "T lymphocyte":                  "T cell",
    "T lymphocytes":                 "T cell",

    # ── Regulatory T cell variants ────────────────────────────────────────────
    "CD4+ Treg":                     "regulatory T cell",
    "Treg":                          "regulatory T cell",
    "Tregs":                         "regulatory T cell",
    "Treg cell":                     "regulatory T cell",
    "Treg cells":                    "regulatory T cell",
    "regulatory T cell (Treg)":      "regulatory T cell",
    "regulatory T cell (Foxp3+)":    "regulatory T cell",
    "regulatory T cell (Foxp3+ Treg)": "regulatory T cell",
    "regulatory T cells (Treg)":     "regulatory T cell",
    "regulatory T cells (Tregs)":    "regulatory T cell",
    "Foxp3+ regulatory T cell (Treg)": "regulatory T cell",
    "FOXP3 lineage regulatory T cell (Treg)": "regulatory T cell",
    "alloantigen-specific CD4+ Foxp3+ regulatory T cells (ag-Tregs)": "regulatory T cell",
    "functional iTreg cell":         "regulatory T cell",
    "induced regulatory T cell (Treg, FOXP3+CD25+CD4+)": "regulatory T cell",

    # ── B cell ────────────────────────────────────────────────────────────────
    "B-cell":                        "B cell",
    "B-Cell":                        "B cell",
    "B-cells":                       "B cell",
    "B cells":                       "B cell",
    "B lymphocyte":                  "B cell",
    "B lymphocytes":                 "B cell",
    "resting B-lymphocyte":          "B cell",

    # ── NK cell ──────────────────────────────────────────────────────────────
    "NK-cell":                       "NK cell",
    "natural killer cell":           "NK cell",

    # ── Fibroblast species normalization ─────────────────────────────────────
    "murine embryonic fibroblast":   "mouse embryonic fibroblast",
    "mouse embryo fibroblast":       "mouse embryonic fibroblast",
    "murine fibroblast":             "mouse fibroblast",

    # ── iPSC synonyms ────────────────────────────────────────────────────────
    "induced pluripotent stem cells": "induced pluripotent stem cell",
    "iPS cell":                      "induced pluripotent stem cell",
    "iPS cells":                     "induced pluripotent stem cell",

    # ── Cardiomyocyte ─────────────────────────────────────────────────────────
    "cardiomyocytes":                "cardiomyocyte",
    "cardiac myocyte":               "cardiomyocyte",

    # ── Pancreatic beta / insulin-producing cell punctuation ─────────────────
    "insulin-producing cell (β cell)": "insulin-producing cell (β-cell)",
    "insulin-producing cell (beta cell)": "insulin-producing cell (β-cell)",
    "pancreatic β cell":             "insulin-producing cell (β-cell)",
    "pancreatic beta cell":          "insulin-producing cell (β-cell)",
    "islet beta cell":               "insulin-producing cell (β-cell)",
    "islet β cell":                  "insulin-producing cell (β-cell)",

    # ── Hepatocyte variants (conservative — only clear equivalents) ───────────
    "hepatocyte (hepatic spheroid)": "hepatocyte",
    "mature functional hepatocyte":  "hepatocyte",
    "neohepatocytes":                "hepatocyte-like cell",

    # ── Neural stem cell ──────────────────────────────────────────────────────
    "neural stem/progenitor cell":   "neural stem cell",
    "neural stem/precursor cell":    "neural stem cell",

    # ── Macrophage ────────────────────────────────────────────────────────────
    "macrophage-like cell":          "macrophage",

    # ── Endothelial ───────────────────────────────────────────────────────────
    "endothelial cells":             "endothelial cell",
    "vascular endothelial cell":     "endothelial cell",

    # ── Remaining hyphen variants ─────────────────────────────────────────────
    "GABAergic induced-neuron":                  "GABAergic induced neuron",
    "induced-oligodendrocyte progenitor cell":   "induced oligodendrocyte progenitor cell",
    "type-1 conventional dendritic cell":        "type 1 conventional dendritic cell",
}


def normalize(name: str) -> str:
    s = name.strip()
    if not s:
        return s

    # Layer 1: hyphen normalization in compound cell names
    # "T-cell" → "T cell" (handled in SYNONYMS, but catch generic cases too)
    # Only remove hyphens between word and "cell/cells" to avoid breaking
    # legitimate hyphenated names like "hepatocyte-like"
    s = re.sub(r'\b(\w+)-[Cc]ells?\b',
               lambda m: m.group(1) + " cell", s)

    # Layer 2: abbreviation expansion, but only outside parentheticals.
    # This avoids "human embryonic stem cell (hESC)" becoming
    # "human embryonic stem cell (human embryonic stem cell)".
    parts = re.split(r"(\([^)]*\))", s)
    for i, part in enumerate(parts):
        if part.startswith("(") and part.endswith(")"):
            continue
        for pattern, replacement in ABBREV.items():
            part = re.sub(pattern, replacement, part, flags=re.IGNORECASE)
        parts[i] = part
    s = "".join(parts)

    # Layer 3: exact synonym lookup (post-expansion)
    s = SYNONYMS.get(s, s)

    duplicate_parentheticals = [
        r"induced pluripotent stem cells?",
        r"human embryonic stem cells?",
        r"embryonic stem cells?",
        r"mouse embryonic stem cells?",
        r"peripheral blood mononuclear cells?",
        r"human umbilical vein endothelial cells?",
        r"mesenchymal stem cells?",
        r"neural stem cells?",
        r"neural progenitor cells?",
        r"hematopoietic stem cells?",
        r"mouse embryonic fibroblasts?",
    ]
    for pattern in duplicate_parentheticals:
        s = re.sub(
            rf"\b({pattern})\s*\(\s*{pattern}\s*\)",
            lambda m: m.group(1),
            s,
            flags=re.IGNORECASE,
        )

    return s


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")

    before_src = df["source_cell"].nunique()
    before_tgt = df["target_cell"].nunique()

    df["source_cell_std"] = df["source_cell"].apply(normalize)
    df["target_cell_std"] = df["target_cell"].apply(normalize)

    after_src = df["source_cell_std"].nunique()
    after_tgt = df["target_cell_std"].nunique()

    print(f"source_cell unique: {before_src} → {after_src} (−{before_src - after_src})")
    print(f"target_cell unique: {before_tgt} → {after_tgt} (−{before_tgt - after_tgt})")

    # Show which synonyms actually fired
    changed_src = df[df["source_cell"] != df["source_cell_std"]][
        ["source_cell", "source_cell_std"]
    ].drop_duplicates().sort_values("source_cell_std")
    changed_tgt = df[df["target_cell"] != df["target_cell_std"]][
        ["target_cell", "target_cell_std"]
    ].drop_duplicates().sort_values("target_cell_std")

    print(f"\nSource cell changes ({len(changed_src)}):")
    print(changed_src.to_string(index=False))
    print(f"\nTarget cell changes ({len(changed_tgt)}):")
    print(changed_tgt.to_string(index=False))

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"\nSaved {FILE}")


if __name__ == "__main__":
    main()
