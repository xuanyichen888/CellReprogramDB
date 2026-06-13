"""
Apply manual validation annotations from verification_sample_v3.xlsx.

The script is intentionally conservative:
- rows marked remove/no are flagged, not deleted;
- rows marked hide_single_tf are hidden through single_tf_flag;
- rows marked fix without corrected_* values are kept but marked needs_review.

Default behavior is dry-run (prints a summary, writes nothing). Pass --apply to
write recipes_master_v2.csv (a .bak backup is made first).
"""

import argparse
import shutil
from pathlib import Path

import pandas as pd


DATA_FILE = "recipes_master_v2.csv"
VALIDATION_FILE = "verification_sample_v3.xlsx"
KEY_COLUMNS = ["pmid", "source_cell", "target_cell", "factors", "source"]

ANNOTATION_TO_DATA = {
    "recipe_valid": "validation_recipe_valid",
    "source_cell_valid": "validation_source_cell_valid",
    "target_cell_valid": "validation_target_cell_valid",
    "factors_valid": "validation_factors_valid",
    "standalone_recipe": "validation_standalone_recipe",
    "duplicate_status": "validation_duplicate_status",
    "error_category": "validation_error_category",
    "action": "validation_action",
    "annotation_notes": "validation_notes",
}

VALIDATION_COLUMNS = list(ANNOTATION_TO_DATA.values()) + [
    "validation_needs_review",
    "validation_known_issue",
]

REVIEW_ACTIONS = {
    "fix",
    "hide_single_tf",
    "mark_duplicate",
    "remove",
    "needs_fulltext",
}


def clean(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def append_value(existing: str, value: str) -> str:
    existing = clean(existing)
    value = clean(value)
    if not value:
        return existing
    parts = [part.strip() for part in existing.split(";") if part.strip()]
    if value not in parts:
        parts.append(value)
    return ";".join(parts)


def append_tags(existing: str, tags: list[str]) -> str:
    out = clean(existing)
    for tag in tags:
        out = append_value(out, tag)
    return out


def normalize_for_match(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for col in columns:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()
    return df


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in VALIDATION_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    for col in ["single_tf_flag", "is_duplicate", "duplicate_reason", "preferred_pmid", "duplicate_group_id"]:
        if col not in df.columns:
            df[col] = ""
    return df


def should_need_review(annotation: pd.Series) -> bool:
    action = clean(annotation.get("action", "")).lower()
    recipe_valid = clean(annotation.get("recipe_valid", "")).lower()
    standalone = clean(annotation.get("standalone_recipe", "")).lower()
    error_category = clean(annotation.get("error_category", ""))
    return (
        action in REVIEW_ACTIONS
        or recipe_valid in {"partial", "no", "unclear"}
        or standalone == "no_member_of_larger_recipe"
        or bool(error_category)
    )


def apply_corrected_fields(df: pd.DataFrame, idx, annotation: pd.Series) -> list[str]:
    changed = []
    corrections = {
        "corrected_source_cell": "source_cell",
        "corrected_target_cell": "target_cell",
        "corrected_factors": "factors",
    }
    for source_col, data_col in corrections.items():
        value = clean(annotation.get(source_col, ""))
        if value:
            df.loc[idx, data_col] = value
            changed.append(data_col)
    return changed


def apply_validation_sample(df: pd.DataFrame, validation: pd.DataFrame) -> dict:
    validation = normalize_for_match(validation, KEY_COLUMNS)
    df = normalize_for_match(df, KEY_COLUMNS)

    matched_rows = 0
    unmatched = []
    corrected_fields = []

    for _, annotation in validation.iterrows():
        mask = pd.Series(True, index=df.index)
        for col in KEY_COLUMNS:
            mask &= df[col] == clean(annotation.get(col, ""))
        idx = df.index[mask]
        if len(idx) == 0:
            unmatched.append(clean(annotation.get("no", "")) or clean(annotation.get("pmid", "")))
            continue

        matched_rows += len(idx)
        for annotation_col, data_col in ANNOTATION_TO_DATA.items():
            df.loc[idx, data_col] = clean(annotation.get(annotation_col, ""))

        needs_review = should_need_review(annotation)
        df.loc[idx, "validation_needs_review"] = str(needs_review)

        action = clean(annotation.get("action", "")).lower()
        recipe_valid = clean(annotation.get("recipe_valid", "")).lower()
        standalone = clean(annotation.get("standalone_recipe", "")).lower()
        duplicate_status = clean(annotation.get("duplicate_status", "")).lower()
        preferred_pmid = clean(annotation.get("preferred_pmid", ""))

        if action == "remove" or recipe_valid == "no":
            df.loc[idx, "validation_action"] = "remove"
            df.loc[idx, "validation_needs_review"] = "True"

        if action == "hide_single_tf" or standalone == "no_member_of_larger_recipe":
            df.loc[idx, "validation_action"] = "hide_single_tf"
            df.loc[idx, "single_tf_flag"] = True
            df.loc[idx, "validation_needs_review"] = "True"

        if action == "mark_duplicate" or duplicate_status.startswith("duplicate"):
            df.loc[idx, "is_duplicate"] = True
            df.loc[idx, "validation_needs_review"] = "True"
            df.loc[idx, "validation_action"] = "mark_duplicate"
            if preferred_pmid:
                df.loc[idx, "preferred_pmid"] = preferred_pmid
            for i in idx:
                df.at[i, "duplicate_reason"] = append_value(df.at[i, "duplicate_reason"], duplicate_status or "manual_validation")

        changed = apply_corrected_fields(df, idx, annotation)
        corrected_fields.extend(changed)

    return {
        "matched_rows": matched_rows,
        "unmatched": unmatched,
        "corrected_fields": corrected_fields,
    }


def apply_known_issues(df: pd.DataFrame, workbook: Path) -> dict:
    try:
        known = pd.read_excel(workbook, sheet_name="known_issues", dtype=str).fillna("")
    except ValueError:
        return {"known_rows": 0, "known_actions": {}}

    known = normalize_for_match(known, KEY_COLUMNS)
    df = normalize_for_match(df, KEY_COLUMNS)
    action_counts = {}
    known_rows = 0

    for _, row in known.iterrows():
        pmid = clean(row.get("pmid", ""))
        source = clean(row.get("source", ""))
        note = clean(row.get("known_issue_note", ""))

        mask = df["pmid"] == pmid
        if source:
            mask &= df["source"] == source
        if clean(row.get("source_cell", "")) or clean(row.get("factors", "")):
            for col in ["source_cell", "target_cell", "factors"]:
                mask &= df[col] == clean(row.get(col, ""))

        idx = df.index[mask]
        if len(idx) == 0:
            continue
        known_rows += len(idx)
        df.loc[idx, "validation_known_issue"] = note
        for i in idx:
            df.at[i, "validation_notes"] = append_value(df.at[i, "validation_notes"], note)

        action = ""
        if pmid == "32016422":
            action = "hide_single_tf"
            df.loc[idx, "single_tf_flag"] = True
            df.loc[idx, "validation_action"] = action
            df.loc[idx, "validation_standalone_recipe"] = "no_member_of_larger_recipe"
            df.loc[idx, "validation_error_category"] = df.loc[idx, "validation_error_category"].apply(
                lambda value: append_tags(value, ["single_tf_incomplete", "evidence_weak"])
            )
            df.loc[idx, "validation_needs_review"] = "True"
        elif pmid == "36993577":
            action = "mark_duplicate"
            df.loc[idx, "is_duplicate"] = True
            df.loc[idx, "validation_action"] = action
            df.loc[idx, "validation_duplicate_status"] = "duplicate_preprint"
            df.loc[idx, "preferred_pmid"] = "37421991"
            df.loc[idx, "validation_needs_review"] = "True"
            for i in idx:
                df.at[i, "duplicate_reason"] = append_value(df.at[i, "duplicate_reason"], "preprint_peer_review")
                df.at[i, "duplicate_group_id"] = append_value(df.at[i, "duplicate_group_id"], "known_issue_preprint")
        elif pmid == "31089332" and source == "abstract":
            action = "remove"
            df.loc[idx, "validation_action"] = action
            df.loc[idx, "validation_recipe_valid"] = "no"
            df.loc[idx, "validation_source_cell_valid"] = "no"
            df.loc[idx, "validation_factors_valid"] = "no"
            df.loc[idx, "validation_error_category"] = df.loc[idx, "validation_error_category"].apply(
                lambda value: append_tags(value, ["missing_source", "factors_unspecified"])
            )
            df.loc[idx, "validation_needs_review"] = "True"
        elif pmid in {"37421991", "31089332"}:
            action = "keep"
            if not df.loc[idx, "validation_action"].astype(str).str.strip().any():
                df.loc[idx, "validation_action"] = action
            df.loc[idx, "validation_needs_review"] = df.loc[idx, "validation_needs_review"].replace("", "False")

        if action:
            action_counts[action] = action_counts.get(action, 0) + len(idx)

    return {"known_rows": known_rows, "known_actions": action_counts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=DATA_FILE)
    parser.add_argument("--validation", default=VALIDATION_FILE)
    parser.add_argument("--apply", action="store_true",
                        help="Write changes to the data file. Default is dry-run (no write).")
    args = parser.parse_args()

    data_path = Path(args.data)
    validation_path = Path(args.validation)

    df = pd.read_csv(data_path, dtype=str).fillna("")
    df = ensure_columns(df)
    validation = pd.read_excel(validation_path, sheet_name="validation_sample", dtype=str).fillna("")

    sample_report = apply_validation_sample(df, validation)
    known_report = apply_known_issues(df, validation_path)

    action_counts = df["validation_action"].replace("", "(blank)").value_counts(dropna=False).to_dict()
    review_count = (df["validation_needs_review"].astype(str).str.lower() == "true").sum()
    rejected_count = (
        (df["validation_action"].astype(str).str.lower() == "remove")
        | (df["validation_recipe_valid"].astype(str).str.lower() == "no")
    ).sum()

    print(f"Matched validation sample rows in data: {sample_report['matched_rows']}")
    print(f"Unmatched validation sample rows: {len(sample_report['unmatched'])}")
    if sample_report["unmatched"]:
        print("  " + ", ".join(sample_report["unmatched"]))
    print(f"Known issue rows touched: {known_report['known_rows']}")
    print(f"Known issue actions: {known_report['known_actions']}")
    print(f"Validation actions in data: {action_counts}")
    print(f"validation_needs_review=True: {review_count}")
    print(f"validation rejected/remove rows: {rejected_count}")
    if sample_report["corrected_fields"]:
        print(f"Corrected fields applied: {sorted(set(sample_report['corrected_fields']))}")
    else:
        print("Corrected fields applied: none")

    if not args.apply:
        print(f"Dry run (default); no file written. Re-run with --apply to write {data_path}.")
        return

    backup = str(data_path) + ".bak"
    shutil.copy(data_path, backup)
    df.to_csv(data_path, index=False, encoding="utf-8")
    print(f"Saved {data_path} (backup: {backup})")


if __name__ == "__main__":
    main()
