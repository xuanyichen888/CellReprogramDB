"""
Normalize factor_type values to the six labels supported by the UI:
TF, small_molecule, miRNA, knockdown, cytokine, other.

Also collapses vague factor values such as "unknown" and "not specified in text"
to "not specified" so the existing hide-no-factors filter can catch them.
"""

import pandas as pd
import shutil
import re

FILE = "recipes_master_v2.csv"
VALID = {"TF", "small_molecule", "miRNA", "knockdown", "cytokine", "other"}
KNOWN_TF = {
    "ASCL1", "ATOH1", "BATF3", "BCL6", "BRN2", "BRN3B", "CEBPA", "CEBPB",
    "CRX", "DLX2", "DLX5", "ETV2", "FOXA2", "FOXA3", "FOXD3", "FOXG1",
    "GATA1", "GATA4", "GFI1", "HAND2", "HES1", "HES3", "HNF1A", "HNF4A",
    "IKZF1", "IRF8", "ISL1", "KLF4", "LHX2", "LHX3", "LMX1A", "MEF2C",
    "MITF", "MYC", "MYOD", "MYOD1", "MYT1L", "NANOG", "NEUROD1", "NFE2",
    "NFIA", "NFIB", "NGN2", "NGN3", "NR4A2", "NURR1", "OCT4", "OTX2",
    "PAX4", "PAX6", "PDX1", "POU5F1", "PTF1A", "RAX", "RFX4", "SOX2",
    "SOX9", "SOX10", "SOX11", "TBX3", "TBX5", "TFAP2A", "ZIC1",
}
CYTOKINE_PATTERNS = [
    r"^activin a$", r"^bdnf$", r"^bmp[- ]?\d+$", r"^dkk1$", r"^egf$",
    r"^fgf[- ]?\d+$", r"^bfgf$", r"^gdnf$", r"^hgf$", r"^igf1$",
    r"^ngf$", r"^nodal$", r"^noggin$", r"^nt[- ]?3$", r"^osm$",
    r"^oncostatin m$", r"^vegf$", r"^wnt3a$",
]
SMALL_MOLECULE_PATTERNS = [
    r"^5[- ]?azacytidine$", r"^8[- ]?br[- ]?camp$", r"^a83[- ]?01$",
    r"^atra$", r"^bix01294$", r"^chir99021$", r"^dbcamp$",
    r"^dexamethasone$", r"^dapt$", r"^dibutyryl camp$", r"^dmso$",
    r"^forskolin$", r"^i[- ]?bet151$", r"^isx[- ]?9$", r"^ldn[- ]?193189$",
    r"^nicotinamide$", r"^parnate$", r"^pd0325901$", r"^pifithrin",
    r"^repsox$", r"^rg108$", r"^ro4949097$", r"^rosiglitazone$",
    r"^sb[- ]?431542$", r"^su5402$", r"^ttnpb$", r"^vpa$",
    r"^vitamin c$", r"^y[- ]?27632$",
]


def normalize_factors(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    lower = text.lower()
    if lower in {"unknown", "not specified", "not specified in text"}:
        return "not specified"
    return text


def split_parts(value: str) -> list[str]:
    # Paren-aware: split on , ; | at depth 0 only, so parenthesized commas
    # (e.g. "miR-302 (a, b, c)") and slashes ("Oct3/4") stay intact.
    text = str(value or "").replace("|", ",")
    parts, buf, depth = [], [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        if ch in {",", ";"} and depth == 0:
            p = "".join(buf).strip()
            if p:
                parts.append(p)
            buf = []
        else:
            buf.append(ch)
    p = "".join(buf).strip()
    if p:
        parts.append(p)
    return parts


def canonical_gene_name(name: str) -> str:
    text = name.strip()
    text = re.sub(r"\([^)]*\)", "", text).strip()
    text = text.replace("α", "A").replace("β", "B")
    text = re.sub(r"^[hm](?=[A-Z])", "", text)
    text = text.replace("c-MYC", "MYC").replace("cMYC", "MYC").replace("C-MYC", "MYC")
    text = text.replace("OCT3/4", "OCT4").replace("POU5F1", "OCT4")
    return re.sub(r"[^A-Za-z0-9]", "", text).upper()


def infer_type_from_factor(name: str) -> str:
    text = name.strip()
    low = text.lower()
    if not text:
        return "other"
    if re.search(r"\b(mir|mirna|microrna|let[- ]?7)\b|^mir[- ]?\d+", low):
        return "miRNA"
    if re.search(r"\b(shrna|sirna|knockdown|knockout|crispri|loss[- ]of[- ]function)\b", low):
        return "knockdown"
    if any(re.search(pattern, low) for pattern in CYTOKINE_PATTERNS):
        return "cytokine"
    if any(re.search(pattern, low) for pattern in SMALL_MOLECULE_PATTERNS):
        return "small_molecule"
    if canonical_gene_name(text) in KNOWN_TF:
        return "TF"
    return "other"


def clean_label(label: str) -> str:
    label = label.strip()
    if label == "TF (knockdown)":
        return "knockdown"
    if label in {"unknown", "not specified", "culture_medium"}:
        return "other"
    return label if label in VALID else "other"


def normalize_factor_type(value: str, factors: str = "") -> str:
    text = value.strip()
    factor_parts = split_parts(factors)
    raw_labels = [clean_label(x) for x in split_parts(text)]

    if not factor_parts:
        return ", ".join(raw_labels)

    if len(raw_labels) == 1 and len(factor_parts) > 1:
        raw_labels = raw_labels * len(factor_parts)

    labels = []
    for i, factor in enumerate(factor_parts):
        existing = raw_labels[i] if i < len(raw_labels) else ""
        inferred = infer_type_from_factor(factor)
        if existing in {"", "other"} and inferred != "other":
            labels.append(inferred)
        else:
            labels.append(existing or inferred)
    return ", ".join(labels)


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")
    before_bad = 0
    for value in df["factor_type"]:
        for label in [x.strip() for x in value.replace("|", ",").split(",") if x.strip()]:
            if label not in VALID:
                before_bad += 1

    df["factors"] = df["factors"].apply(normalize_factors)
    df["factor_type"] = df.apply(lambda row: normalize_factor_type(row["factor_type"], row["factors"]), axis=1)

    after_bad = 0
    for value in df["factor_type"]:
        for label in [x.strip() for x in value.split(",") if x.strip()]:
            if label not in VALID:
                after_bad += 1

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")

    print(f"非法 factor_type 标签: {before_bad} -> {after_bad}")
    print(f"factors == 'not specified': {(df['factors'] == 'not specified').sum()}")
    print(f"保存至 {FILE}")


if __name__ == "__main__":
    main()
