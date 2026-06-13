"""
Score manually annotated verification_sample_v3.xlsx.

Run only after the validation_sample sheet has been manually annotated.
"""

import argparse
import pandas as pd


VALID_RECIPE = {"yes", "partial", "no", "unclear"}
COMPONENT_COLS = ["source_cell_valid", "target_cell_valid", "factors_valid"]


def pct(num: int, den: int) -> float:
    return round(num / den * 100, 1) if den else 0.0


def summarize(group: pd.DataFrame, label: str) -> dict:
    n = len(group)
    yes = (group["recipe_valid"] == "yes").sum()
    partial = (group["recipe_valid"] == "partial").sum()
    no = (group["recipe_valid"] == "no").sum()
    unclear = (group["recipe_valid"] == "unclear").sum()
    return {
        "group": label,
        "n": n,
        "strict_precision_pct": pct(yes, n),
        "relaxed_precision_pct": pct(yes + partial, n),
        "yes": yes,
        "partial": partial,
        "no": no,
        "unclear": unclear,
    }


def normalize_annotations(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["recipe_valid", "stratum", "source", "confidence"] + COMPONENT_COLS
    for col in cols:
        if col not in df.columns:
            raise SystemExit(f"Missing required column: {col}")
        df[col] = df[col].fillna("").astype(str).str.strip().str.lower()
    bad = sorted(set(df["recipe_valid"]) - VALID_RECIPE - {""})
    if bad:
        raise SystemExit(f"Unexpected recipe_valid values: {bad}")
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="verification_sample_v3.xlsx")
    parser.add_argument("--sheet", default="validation_sample")
    parser.add_argument("--output", default="validation_metrics.csv")
    args = parser.parse_args()

    df = pd.read_excel(args.input, sheet_name=args.sheet, dtype=str).fillna("")
    df = normalize_annotations(df)
    annotated = df[df["recipe_valid"] != ""].copy()
    if annotated.empty:
        raise SystemExit("No manual annotations found yet. Fill recipe_valid before scoring.")

    rows = [summarize(annotated, "overall")]
    for col in ["stratum", "source", "confidence"]:
        for value, group in annotated.groupby(col, dropna=False):
            rows.append(summarize(group, f"{col}={value or 'blank'}"))

    metrics = pd.DataFrame(rows)
    metrics.to_csv(args.output, index=False)

    print(metrics.to_string(index=False))
    print(f"\nSaved metrics -> {args.output}")

    print("\nComponent accuracy (yes / yes+partial):")
    for col in COMPONENT_COLS:
        valid = annotated[annotated[col] != ""]
        yes = (valid[col] == "yes").sum()
        partial = (valid[col] == "partial").sum()
        print(f"  {col}: strict {pct(yes, len(valid))}% | relaxed {pct(yes + partial, len(valid))}% (n={len(valid)})")


if __name__ == "__main__":
    main()
