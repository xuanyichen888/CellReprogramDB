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
    r"\bPBMC\b":   "peripheral blood mononuclear cell",
    r"\bPBMCs\b":  "peripheral blood mononuclear cell",
    r"\bMEF\b":    "mouse embryonic fibroblast",
    r"\bMEFs\b":   "mouse embryonic fibroblast",
    r"\bMSC\b":    "mesenchymal stem cell",
    r"\bMSCs\b":   "mesenchymal stem cell",
    r"\bHUVEC\b":  "human umbilical vein endothelial cell",
    r"\bHUVECs\b": "human umbilical vein endothelial cell",
    r"\bHSC\b":    "hematopoietic stem cell",
    r"\bHSCs\b":   "hematopoietic stem cell",
    r"\bNSC\b":    "neural stem cell",
    r"\bNSCs\b":   "neural stem cell",
    r"\biPSC\b":   "induced pluripotent stem cell",
    r"\biPSCs\b":  "induced pluripotent stem cell",
    r"\bESC\b":    "embryonic stem cell",
    r"\bESCs\b":   "embryonic stem cell",
    r"\bhESC\b":   "human embryonic stem cell",
    r"\bhESCs\b":  "human embryonic stem cell",
    r"\bmESC\b":   "mouse embryonic stem cell",
    r"\bmESCs\b":  "mouse embryonic stem cell",
    r"\bNPC\b":    "neural progenitor cell",
    r"\bNPCs\b":   "neural progenitor cell",
}

# ── Layer 3: synonym → canonical form ────────────────────────────────────────
# Keys are exact post-cleanup strings; values are the canonical form.
# Only merge when biological equivalence is clear and unambiguous.
SYNONYMS = {
    # ── T cell punctuation ────────────────────────────────────────────────────
    "T-cell":                        "T cell",
    "T-Cell":                        "T cell",
    "T lymphocyte":                  "T cell",

    # ── Regulatory T cell variants ────────────────────────────────────────────
    "regulatory T cell (Treg)":      "regulatory T cell",
    "regulatory T cell (Foxp3+)":    "regulatory T cell",
    "regulatory T cell (Foxp3+ Treg)": "regulatory T cell",
    "regulatory T cells (Treg)":     "regulatory T cell",
    "regulatory T cells (Tregs)":    "regulatory T cell",
    "Foxp3+ regulatory T cell (Treg)": "regulatory T cell",
    "FOXP3 lineage regulatory T cell (Treg)": "regulatory T cell",

    # ── B cell ────────────────────────────────────────────────────────────────
    "B-cell":                        "B cell",
    "B-Cell":                        "B cell",
    "B lymphocyte":                  "B cell",
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

    # Layer 2: abbreviation expansion
    for pattern, replacement in ABBREV.items():
        s = re.sub(pattern, replacement, s, flags=re.IGNORECASE)

    # Layer 3: exact synonym lookup (post-expansion)
    s = SYNONYMS.get(s, s)

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
