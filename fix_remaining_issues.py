"""
Targeted fixes for remaining database quality issues:

1. Resolve the 9 'possible_negative_needs_review' entries individually:
   - 7 are FALSE positives (the recipe is valid)
   - 2 are TRUE negatives (recipe should be removed)

2. Hide medium-confidence entries with poor evidence quality
   (evidence_cites_prior_work or evidence_is_methods_sentence + medium confidence)
   → These are already questionable and their evidence doesn't confirm the recipe.
"""

import shutil
import pandas as pd

FILE = "recipes_master_v2.csv"


def append_note(existing: str, note: str) -> str:
    existing = str(existing).strip()
    note = str(note).strip()
    if not note:
        return existing
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing} || {note}"


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")

    # ── 1. Resolve false-positive 'possible_negative_needs_review' entries ──────

    neg_mask = df["validation_resolution"] == "possible_negative_needs_review"
    print(f"Total possible_negative entries: {neg_mask.sum()}")

    # ── 1a. TRUE NEGATIVES: remove these (the recipe factor genuinely failed) ──

    true_neg = {
        # GATA3 alone cannot induce neurogenesis — clearly stated
        ("30809125", "GATA3"):
            "GATA3 overexpression failed to induce neurogenesis alone; required for enhancing neurogenic potential but not sufficient.",
        # C/EBPβ LIP isoform (dominant-negative) explicitly failed myeloid conversion
        ("23755188", "C/EBPβ LIP isoform"):
            "LIP isoform (dominant-negative) explicitly failed to induce myeloid conversion and failed to down-regulate B cell markers.",
    }

    removed = 0
    for (pmid, factors), note in true_neg.items():
        mask = (
            (df["pmid"] == pmid) &
            (df["factors"] == factors) &
            (df["validation_resolution"] == "possible_negative_needs_review")
        )
        n = mask.sum()
        if n == 0:
            print(f"  WARNING: no match for pmid={pmid} factors={factors}")
            continue
        df.loc[mask, "validation_action"] = "remove"
        df.loc[mask, "validation_needs_review"] = "False"
        df.loc[mask, "validation_resolution"] = "negative_result_confirmed"
        for i in df.index[mask]:
            df.at[i, "validation_notes"] = append_note(df.at[i, "validation_notes"], note)
        removed += n
        print(f"  Removed (true negative): pmid={pmid} factors={factors[:40]}")

    # ── 1b. FALSE POSITIVES: clear the negative flag (recipe is valid) ──────────

    false_neg_clear = [
        # 34306986: "Loss of Dkk3 ENHANCES iPSC generation" — positive result
        {"pmid": "34306986",
         "note": "Resolved: evidence says Dkk3 loss ENHANCES iPSC generation; 'does not affect' refers to ESC derivation, not iPSC.",
         "resolution": "resolved_not_negative"},

        # 36289433: elevated NeuroD1 DRAMATICALLY IMPROVED reprogramming
        {"pmid": "36289433",
         "note": "Resolved: elevated NeuroD1 expression dramatically improved reprogramming efficiency; low expression fails but elevated works.",
         "resolution": "resolved_not_negative_dose_dependent"},

        # 19008347: "Oct4, Sox2, Klf4, and c-Myc are SUFFICIENT" — positive
        {"pmid": "19008347",
         "note": "Resolved: evidence confirms OSKM sufficient to trigger fibroblast reprogramming; 'no gene targeted more than once' refers to insertion analysis.",
         "resolution": "resolved_not_negative"},

        # 33144328: Sall4 achieved ~16% iPSC efficiency — positive
        {"pmid": "33144328",
         "note": "Resolved: Sall4+OKS achieved ~16% iPSC efficiency at day 7; control group had 0 colonies but that is the DsRed control, not Sall4.",
         "resolution": "resolved_not_negative"},

        # 39171140: CpG-STAT3d treatment worked; control failed
        {"pmid": "39171140",
         "note": "Resolved: CpG-STAT3d induced myeloid differentiation; 'failed' refers to the control (CpG-scrODN), not the treatment.",
         "resolution": "resolved_not_negative"},
    ]

    # 23755188 C/EBPα p42: works equally with/without C/EBPβ — that's positive
    false_neg_clear.append({
        "pmid": "23755188",
        "factors": "C/EBPα p42 isoform",
        "note": "Resolved: C/EBPα p42 reprogramming capacity is unaffected by C/EBPβ loss; this confirms p42 works.",
        "resolution": "resolved_not_negative",
    })

    cleared = 0
    for item in false_neg_clear:
        mask = (
            (df["pmid"] == item["pmid"]) &
            (df["validation_resolution"] == "possible_negative_needs_review")
        )
        if "factors" in item:
            mask = mask & (df["factors"] == item["factors"])
        n = mask.sum()
        if n == 0:
            print(f"  WARNING: no match for pmid={item['pmid']}")
            continue
        df.loc[mask, "validation_needs_review"] = "False"
        df.loc[mask, "validation_resolution"] = item["resolution"]
        for i in df.index[mask]:
            df.at[i, "validation_notes"] = append_note(df.at[i, "validation_notes"], item["note"])
        cleared += n
        print(f"  Cleared (false positive): pmid={item['pmid']}")

    # 31412725: not negative — it's a methods sentence
    mask_methods = (
        (df["pmid"] == "31412725") &
        (df["validation_resolution"] == "possible_negative_needs_review")
    )
    if mask_methods.sum():
        df.loc[mask_methods, "validation_needs_review"] = "True"
        df.loc[mask_methods, "validation_resolution"] = "evidence_is_methods_sentence"
        for i in df.index[mask_methods]:
            df.at[i, "validation_notes"] = append_note(
                df.at[i, "validation_notes"],
                "Re-classified: evidence sentence is a methods sentence (describes experimental setup), not a negative result.",
            )
        print("  Reclassified pmid=31412725 as evidence_is_methods_sentence")

    print(f"\nTrue negatives removed: {removed}")
    print(f"False positives cleared: {cleared}")
    print(f"Reclassified as methods: 1")

    # ── 2. Hide medium-confidence entries with unreliable evidence ────────────

    # Active entries only (not already hidden/removed/duplicate)
    already_handled = (
        (df["is_duplicate"].astype(str).str.lower() == "true") |
        (df["validation_action"].isin(["remove", "hide_incomplete_recipe", "hide_single_tf"]))
    )
    active = ~already_handled

    # Medium confidence + bad evidence type → hide
    medium_bad_evidence = (
        active &
        (df["confidence"] == "medium") &
        (df["validation_resolution"].isin(["evidence_cites_prior_work", "evidence_is_methods_sentence"]))
    )
    n_hide = medium_bad_evidence.sum()
    df.loc[medium_bad_evidence, "validation_action"] = "hide_incomplete_recipe"
    df.loc[medium_bad_evidence, "validation_needs_review"] = "False"
    for i in df.index[medium_bad_evidence]:
        df.at[i, "validation_notes"] = append_note(
            df.at[i, "validation_notes"],
            "Hidden: medium-confidence entry with weak/indirect evidence sentence; cannot confirm recipe from available evidence.",
        )
    print(f"\nHidden (medium confidence + bad evidence): {n_hide}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    total_review = (df["validation_needs_review"].astype(str).str.lower() == "true").sum()
    total_remove = (df["validation_action"] == "remove").sum()
    total_dup = (df["is_duplicate"].astype(str).str.lower() == "true").sum()

    shown = df[
        (df["is_duplicate"].str.lower() != "true") &
        (~df["validation_action"].isin(["remove", "hide_incomplete_recipe", "hide_single_tf"])) &
        (df["factors"] != "not specified") &
        (df["single_tf_flag"].str.lower() != "true") &
        (df["paper_type"] == "research") &
        (df["confidence"].isin(["high", "medium"]))
    ]
    print(f"validation_needs_review=True:  {total_review}")
    print(f"validation_action=remove:      {total_remove}")
    print(f"is_duplicate=True:             {total_dup}")
    print(f"默认显示条目数:                {len(shown)}")

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"\n保存至 {FILE}")


if __name__ == "__main__":
    main()
