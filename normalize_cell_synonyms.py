"""
Normalize target_cell and source_cell synonym variants in recipes_master_v2.csv.

Two-pass approach:
1. Strip pure abbreviation parentheticals at end of string:
   "endothelial cell (EC)"  → "endothelial cell"
   "induced neural stem cell (iNSC)"  → "induced neural stem cell"
   Only stripped when the abbreviation is ALL-CAPS or i+CAPS (≤6 chars).
   Biological descriptions like "(substantia nigra pars compacta)" are kept.

2. Normalize plurals: "... cells" → "... cell" at end of string.
   Done carefully: only when the whole string ends in "s" after a space-separated word.
"""

import re
import shutil
import pandas as pd

FILE = "recipes_master_v2.csv"

# ── Abbreviation stripping ────────────────────────────────────────────────────
# Match trailing "(ABC)" where content is ≤7 chars, all-caps or i/ci/h/r + caps
ABBREV_RE = re.compile(
    r'\s*\(\s*'
    r'(?:'
    r'[A-Z][A-Z0-9-]{0,6}'           # all-caps abbreviation: EC, iPS, BEC
    r'|[a-z]{1,2}[A-Z][A-Z0-9a-z-]{0,5}'  # camelCase abbrev: iNSC, ciCPC, hiVEC
    r')'
    r'\s*\)\s*$'
)

def strip_abbrev(name: str) -> str:
    return ABBREV_RE.sub("", name.strip()).strip()

# ── Plural normalization ───────────────────────────────────────────────────────
# "endothelial cells (ECs)" → first strip abbrev → "endothelial cells" → depluralize
# Only depluralizes trailing "cells" → "cell", "neurons" → "neuron", etc.
PLURAL_SUFFIXES = [
    (r'\bcells\b$',       'cell'),
    (r'\bneurons\b$',     'neuron'),
    (r'\bfibroblasts\b$', 'fibroblast'),
    (r'\bcardiomyocytes\b$', 'cardiomyocyte'),
    (r'\bhepatocytes\b$', 'hepatocyte'),
    (r'\bastrocytes\b$',  'astrocyte'),
    (r'\bmacrophages\b$', 'macrophage'),
    (r'\bprogenitors\b$', 'progenitor'),
    (r'\bprecursors\b$',  'precursor'),
    (r'\bstem cells\b$',  'stem cell'),
]

def normalize_plural(name: str) -> str:
    for pattern, replacement in PLURAL_SUFFIXES:
        name = re.sub(pattern, replacement, name, flags=re.IGNORECASE)
    return name


def normalize_cell(name: str) -> str:
    if not name or not name.strip():
        return name
    n = strip_abbrev(name)
    n = normalize_plural(n)
    return n.strip()


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")

    changes_target = 0
    changes_source = 0

    for col, counter in [("target_cell", "changes_target"), ("source_cell", "changes_source")]:
        normed = df[col].apply(normalize_cell)
        diff   = normed != df[col]
        n      = diff.sum()
        if counter == "changes_target":
            changes_target = n
        else:
            changes_source = n
        if n:
            print(f"\n{col}: {n} entries normalized. Examples:")
            for old, new in zip(df[col][diff].head(15), normed[diff].head(15)):
                print(f'  "{old}"  →  "{new}"')
        df[col] = normed

    print(f"\ntarget_cell changes: {changes_target}")
    print(f"source_cell changes: {changes_source}")
    print(f"Unique target_cell: {df['target_cell'].nunique()} (was higher before normalization)")
    print(f"Unique source_cell: {df['source_cell'].nunique()}")

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"Saved {FILE}")


if __name__ == "__main__":
    main()
