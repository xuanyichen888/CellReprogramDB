"""
Classify single-TF entries into:
  standalone_valid  — well-established, biologically justified single-factor conversion
  cocktail_member   — known member of a multi-factor cocktail (e.g., SOX2 in OSKM)
  unclear           — cannot determine without reading the full paper

Adds column: single_tf_status
App logic change: only hide 'cocktail_member' by default; show 'standalone_valid' and 'unclear'
"""

import re
import shutil
import pandas as pd

FILE = "recipes_master_v2.csv"

# ── Known standalone valid single-TF reprogramming factors ──────────────────
# These are well-established in the literature as sufficient on their own
# (or with small molecules that are already in the factors field) for conversion.
STANDALONE_VALID = {
    # Neuronal reprogramming
    "NGN2", "NEUROG2", "NEUROGENIN2", "NEUROGENIN-2",
    "ASCL1", "MASH1",
    "NEUROD1", "NEUROD1 (ND1)", "ND1",
    "DLX2",
    "ATOH1", "MATH1",
    # Muscle
    "MYOD", "MYOD1",
    # Endothelial
    "ETV2", "ER71",
    # Hepatic
    "HNF4A", "HNF4ALPHA", "HNF4α",
    # Pancreatic
    "PAX4",
    # Hematopoietic/myeloid
    "GATA1",
    "PU.1", "SPI1",
    "C/EBPA", "CEBPA",
    "C/EBPα",
    # Cardiac
    "GATA4",   # can drive cardiac fate alone in specific contexts
    # Chondrocyte / cartilage
    "SOX9",    # master regulator of chondrogenesis
    # Hair cell
    # (ATOH1 already above)
}

# ── Known cocktail members — single entry without other cocktail partners ────
# These are core iPSC/pluripotency or well-known multi-factor cocktail members;
# a paper studying OCT4 alone is almost certainly investigating its role in OSKM.
COCKTAIL_MEMBERS = {
    # Yamanaka factors
    "OCT4", "OCT-4", "OCT3/4", "POU5F1",
    "SOX2",
    "KLF4",
    "C-MYC", "C/EBP",
    # Thomson factors
    "NANOG",
    "LIN28", "LIN28A",
    # Common pluripotency enhancers studied mechanistically
    "GLIS1",
}

# Normalize a factor string for lookup
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().upper())


def classify(factors_str: str, target_cell: str = "", source_cell: str = "") -> str:
    """Return 'standalone_valid', 'cocktail_member', or 'unclear'."""
    f = _norm(factors_str)
    # Remove common gene-name punctuation variants for matching
    f_clean = re.sub(r"[αβ]", "A", f)  # α/β → A for rough matching
    f_clean = re.sub(r"[-/]", "", f_clean)

    # Direct match against standalone list
    for sv in STANDALONE_VALID:
        if _norm(sv) == f or _norm(sv).replace("-","") == f_clean:
            return "standalone_valid"
    # NGN2 variants (neurogenin-2 (NGN2) etc.)
    if re.search(r"\bNGN2\b|\bNEUROGENIN.?2\b|\bNEUROG2\b", f):
        return "standalone_valid"
    if re.search(r"\bASCL1\b|\bMASH1\b", f):
        return "standalone_valid"
    if re.search(r"\bNEUROD1\b", f):
        return "standalone_valid"
    if re.search(r"\bATOH1\b|\bMATH1\b", f):
        return "standalone_valid"
    if re.search(r"\bMYOD\b|\bMYOD1\b", f):
        return "standalone_valid"
    if re.search(r"\bETV2\b|\bER71\b", f):
        return "standalone_valid"
    if re.search(r"\bHNF4A\b|\bHNF4ALPHA\b|\bHNF4", f):
        return "standalone_valid"
    if re.search(r"\bGATA1\b", f):
        return "standalone_valid"
    if re.search(r"\bPU\.?1\b|\bSPI1\b", f):
        return "standalone_valid"
    if re.search(r"\bCEBPA\b|\bC/EBPA\b|\bCEBP.?ALPHA\b", f):
        return "standalone_valid"
    if re.search(r"\bPAX4\b", f):
        return "standalone_valid"
    if re.search(r"\bDLX2\b", f):
        return "standalone_valid"

    # Context-dependent factors: check target/source to decide
    tgt = target_cell.lower()
    if re.search(r"\bGATA4\b", f):
        # Standalone if target is cardiomyocyte or cardiac; otherwise unclear
        return "standalone_valid" if re.search(r"cardio|heart|cardiomyocyte", tgt) else "unclear"
    if re.search(r"\bHNF4A\b|\bHNF4ALPHA\b|\bHNF4α\b", f):
        # Standalone if target is hepatocyte/liver cell
        return "standalone_valid" if re.search(r"hepato|liver|hepatocyte", tgt) else "unclear"
    if re.search(r"\bSOX9\b", f):
        # Standalone if target is chondrocyte/cartilage
        return "standalone_valid" if re.search(r"chondro|cartilage", tgt) else "unclear"
    if re.search(r"\bPTF1A\b", f):
        # Pancreatic lineage
        return "standalone_valid" if re.search(r"pancrea|acinar|ductal|beta.?cell", tgt) else "unclear"
    if re.search(r"\bFOXA2\b", f):
        # Usually a member of hepatocyte cocktail; standalone unclear
        return "unclear"

    # Cocktail member check
    for cm in COCKTAIL_MEMBERS:
        if _norm(cm) == f:
            return "cocktail_member"
    if re.search(r"\bOCT4\b|\bOCT-4\b|\bOCT3/4\b|\bPOU5F1\b", f):
        return "cocktail_member"
    if re.search(r"\bSOX2\b", f):
        return "cocktail_member"
    if re.search(r"\bKLF4\b", f):
        return "cocktail_member"
    if re.search(r"\bC-MYC\b|\bC/MYC\b|\bCMYC\b", f):
        return "cocktail_member"
    if re.search(r"\bNANOG\b", f):
        return "cocktail_member"
    if re.search(r"\bLIN28\b", f):
        return "cocktail_member"

    return "unclear"


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")

    is_single = df["single_tf_flag"].str.lower() == "true"
    print(f"Single-TF entries: {is_single.sum()}")

    if "single_tf_status" not in df.columns:
        df["single_tf_status"] = ""

    # Classify only single-TF entries
    df.loc[is_single, "single_tf_status"] = df.loc[is_single].apply(
        lambda r: classify(r["factors"], r.get("target_cell",""), r.get("source_cell","")), axis=1
    )

    # Clear status for non-single-TF entries
    df.loc[~is_single, "single_tf_status"] = ""

    counts = df.loc[is_single, "single_tf_status"].value_counts()
    print("Single-TF status distribution:")
    print(counts.to_string())
    print()

    # Preview: standalone_valid entries that will now be shown
    sv = df[
        is_single &
        (df["single_tf_status"] == "standalone_valid") &
        (df["confidence"] == "high") &
        (~df["is_duplicate"].str.lower().eq("true")) &
        (df["paper_type"] == "research") &
        (~df["validation_action"].isin(["remove","hide_incomplete_recipe","hide_single_tf"]))
    ]
    print(f"standalone_valid high-confidence entries (will show in default view): {len(sv)}")
    print(sv["factors"].value_counts().head(15).to_string())
    print()

    cm = df[
        is_single &
        (df["single_tf_status"] == "cocktail_member") &
        (df["confidence"] == "high") &
        (~df["is_duplicate"].str.lower().eq("true")) &
        (df["paper_type"] == "research") &
        (~df["validation_action"].isin(["remove","hide_incomplete_recipe","hide_single_tf"]))
    ]
    print(f"cocktail_member (will remain hidden): {len(cm)}")

    unc = df[
        is_single &
        (df["single_tf_status"] == "unclear") &
        (df["confidence"] == "high") &
        (~df["is_duplicate"].str.lower().eq("true")) &
        (df["paper_type"] == "research") &
        (~df["validation_action"].isin(["remove","hide_incomplete_recipe","hide_single_tf"]))
    ]
    print(f"unclear (will show in default view — may be checked later): {len(unc)}")
    print(unc["factors"].value_counts().head(20).to_string())

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"\nSaved {FILE}")


if __name__ == "__main__":
    main()
