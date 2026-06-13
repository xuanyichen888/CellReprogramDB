"""
Apply or preview immune model update proposals.

Default behavior is dry-run only. Use --apply to write recipes_master_v2.csv.
The script always writes a before/after preview CSV to qa_outputs/.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

MASTER_FILE = Path("recipes_master_v2.csv")
PROPOSAL_FILE = Path("qa_outputs/immune_model_update_proposal.csv")
PREVIEW_OUT = Path("qa_outputs/immune_model_update_apply_preview.csv")

RISK_ORDER = {"low": 1, "medium": 2, "high": 3}
DIRECT_FIELDS = {
    "validation_needs_review",
    "validation_action",
    "validation_resolution",
    "is_broad_duplicate",
    "broad_duplicate_reason",
    "single_tf_status",
}
APPEND_FIELDS = {
    "validation_notes_append": "validation_notes",
    "broad_duplicate_reason_append": "broad_duplicate_reason",
}


def clean(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def risk_allowed(risk: str, max_risk: str) -> bool:
    return RISK_ORDER.get(clean(risk), 999) <= RISK_ORDER[max_risk]


def parse_updates(value: str) -> dict:
    text = clean(value)
    if not text or text == "{}":
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        return {}
    return data


def append_value(old: str, addition: str) -> str:
    old = clean(old)
    addition = clean(addition)
    if not addition:
        return old
    if not old:
        return addition
    if addition in old:
        return old
    return f"{old} | {addition}"


def build_preview(master: pd.DataFrame, proposals: pd.DataFrame, max_risk: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    updated = master.copy()
    preview_rows = []

    eligible = proposals[
        proposals["write_risk"].apply(lambda risk: risk_allowed(risk, max_risk))
        & (proposals["proposed_action"] != "accept_no_change")
        & (proposals["proposed_action"] != "manual_review_required")
    ].copy()

    for _, proposal in eligible.iterrows():
        updates = parse_updates(proposal.get("proposed_updates", ""))
        if not updates:
            continue
        idx = int(proposal["row_index"])
        if idx < 0 or idx >= len(updated):
            continue
        if clean(updated.at[idx, "pmid"]) != clean(proposal.get("pmid")):
            preview_rows.append(
                {
                    "row_index": idx,
                    "pmid": clean(proposal.get("pmid")),
                    "proposed_action": clean(proposal.get("proposed_action")),
                    "write_risk": clean(proposal.get("write_risk")),
                    "field": "ERROR",
                    "old_value": clean(updated.at[idx, "pmid"]),
                    "new_value": "PMID mismatch; skipped",
                }
            )
            continue

        for key, value in updates.items():
            if key in DIRECT_FIELDS:
                field = key
                old = clean(updated.at[idx, field]) if field in updated.columns else ""
                new = clean(value)
                if old == new:
                    continue
                updated.at[idx, field] = new
            elif key in APPEND_FIELDS:
                field = APPEND_FIELDS[key]
                old = clean(updated.at[idx, field]) if field in updated.columns else ""
                new = append_value(old, value)
                if old == new:
                    continue
                updated.at[idx, field] = new
            else:
                continue

            preview_rows.append(
                {
                    "row_index": idx,
                    "pmid": clean(proposal.get("pmid")),
                    "proposed_action": clean(proposal.get("proposed_action")),
                    "write_risk": clean(proposal.get("write_risk")),
                    "field": field,
                    "old_value": old,
                    "new_value": new,
                    "model_decision": clean(proposal.get("model_decision")),
                    "model_confidence": clean(proposal.get("model_confidence")),
                    "model_rationale": clean(proposal.get("model_rationale")),
                }
            )

    return updated, pd.DataFrame(preview_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-risk", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--apply", action="store_true", help="Actually write recipes_master_v2.csv.")
    args = parser.parse_args()

    master = pd.read_csv(MASTER_FILE, dtype=str).fillna("")
    proposals = pd.read_csv(PROPOSAL_FILE, dtype=str).fillna("")
    updated, preview = build_preview(master, proposals, args.max_risk)
    PREVIEW_OUT.parent.mkdir(parents=True, exist_ok=True)
    preview.to_csv(PREVIEW_OUT, index=False, encoding="utf-8")

    changed_rows = preview["row_index"].nunique() if not preview.empty else 0
    print(f"Max risk selected: {args.max_risk}")
    print(f"Preview changes: {len(preview)} field edits across {changed_rows} rows")
    print(f"Wrote preview: {PREVIEW_OUT}")
    if not preview.empty:
        print("Fields:")
        print(preview["field"].value_counts().to_string())

    if args.apply:
        backup = Path(str(MASTER_FILE) + ".pre_model_update.bak")
        if not backup.exists():
            shutil.copy(MASTER_FILE, backup)
        updated.to_csv(MASTER_FILE, index=False, encoding="utf-8")
        print(f"APPLIED updates to {MASTER_FILE}")
        print(f"Backup: {backup}")
    else:
        print("Dry-run only. Re-run with --apply to write recipes_master_v2.csv.")


if __name__ == "__main__":
    main()
