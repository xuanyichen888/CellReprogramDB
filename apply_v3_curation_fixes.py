"""
Apply conservative curation fixes derived from verification_sample_v3.xlsx.

This resolves rows where the v3 annotation notes identified a concrete fix,
and hides incomplete/vague rows when a better full-text row already exists.
Rows with weak evidence but no clear correction remain marked needs_review.
"""

import argparse
from pathlib import Path

import pandas as pd


DATA_FILE = "recipes_master_v2.csv"


def clean(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def append_tag(existing: str, tag: str) -> str:
    existing = clean(existing)
    parts = [part.strip() for part in existing.split(";") if part.strip()]
    if tag and tag not in parts:
        parts.append(tag)
    return ";".join(parts)


def append_note(existing: str, note: str) -> str:
    existing = clean(existing)
    note = clean(note)
    if not note:
        return existing
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing} || {note}"


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    defaults = {
        "validation_action": "",
        "validation_needs_review": "False",
        "validation_notes": "",
        "validation_resolution": "",
        "duplicate_reason": "",
        "duplicate_group_id": "",
        "preferred_pmid": "",
        "is_duplicate": "False",
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default
    return df


def select_rows(df: pd.DataFrame, **conditions):
    mask = pd.Series(True, index=df.index)
    for col, value in conditions.items():
        series = df[col].fillna("").astype(str).str.strip()
        if isinstance(value, (list, tuple, set)):
            allowed = {str(item).strip() for item in value}
            mask &= series.isin(allowed)
        else:
            mask &= series == str(value).strip()
    idx = df.index[mask]
    if len(idx) == 0:
        raise SystemExit(f"No rows matched {conditions}")
    return idx


def update_rows(df: pd.DataFrame, idx, resolution: str, note: str, needs_review=False, **updates):
    for col, value in updates.items():
        df.loc[idx, col] = value
    df.loc[idx, "validation_needs_review"] = str(needs_review)
    df.loc[idx, "validation_resolution"] = resolution
    for i in idx:
        df.at[i, "validation_notes"] = append_note(df.at[i, "validation_notes"], note)


def mark_duplicate(df: pd.DataFrame, idx, group_id: str, reason="superseded_by_fulltext", note=""):
    df.loc[idx, "is_duplicate"] = True
    df.loc[idx, "validation_action"] = "mark_duplicate"
    df.loc[idx, "validation_needs_review"] = "False"
    df.loc[idx, "validation_resolution"] = reason
    for i in idx:
        df.at[i, "duplicate_reason"] = append_tag(df.at[i, "duplicate_reason"], reason)
        df.at[i, "duplicate_group_id"] = append_tag(df.at[i, "duplicate_group_id"], group_id)
        df.at[i, "validation_notes"] = append_note(df.at[i, "validation_notes"], note or reason)


def hide_incomplete(df: pd.DataFrame, idx, note: str):
    df.loc[idx, "validation_action"] = "hide_incomplete_recipe"
    df.loc[idx, "validation_needs_review"] = "False"
    df.loc[idx, "validation_resolution"] = "hidden_incomplete_recipe"
    for i in idx:
        df.at[i, "validation_notes"] = append_note(df.at[i, "validation_notes"], note)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=DATA_FILE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.data)
    df = pd.read_csv(path, dtype=str).fillna("")
    df = ensure_columns(df)

    # Concrete row-level corrections from v3 notes.
    update_rows(
        df,
        select_rows(df, pmid="37171961", source="abstract", factors=["Insm1 deletion", "INSM1 loss-of-function"]),
        "resolved_standardized_factor",
        "Standardized factor notation from v3: Insm1 deletion -> INSM1 loss-of-function.",
        factors="INSM1 loss-of-function",
        confidence="high",
    )

    update_rows(
        df,
        select_rows(df, pmid="30018471", source="abstract", target_cell=["induced neuron (iN)", "dopaminergic neuron-like cell"]),
        "resolved_standardized_target",
        "Standardized target from v3: induced neuron -> dopaminergic neuron-like cell.",
        target_cell="dopaminergic neuron-like cell",
    )

    update_rows(
        df,
        select_rows(df, pmid="30611717", source="fulltext", factors=["eupatilin", "Eupatilin"]),
        "resolved_standardized_target",
        "Expanded HSC abbreviation from v3: HSC means hepatic stellate cell in this paper.",
        target_cell="hepatic stellate cell-like cell",
        factors="Eupatilin",
        confidence="high",
    )

    update_rows(
        df,
        select_rows(df, pmid="33099273", source="fulltext", factors="Foxg1 knockdown"),
        "resolved_evidence_updated",
        "Replaced weak prior-work evidence with direct result statement from the same paper.",
        evidence_sentence="Conditional knockdown of Foxg1 in Sox9+ utricular supporting cells increased the number of hair cells in neonatal mouse utricle.",
    )

    update_rows(
        df,
        select_rows(df, pmid="29719629", source="fulltext", factors="OCT4, SOX2, KLF4"),
        "resolved_source_and_evidence_updated",
        "Specified senescent fibroblast source and replaced multi-condition methods sentence with direct OSK efficiency evidence.",
        source_cell="senescent mouse fibroblast",
        evidence_sentence="Transfection of senescent fibroblasts with Oct4/Sox2/Klf4 showed lower reprogramming efficiency than conventional OSKM, but still represented a tested iPSC induction condition.",
    )

    update_rows(
        df,
        select_rows(
            df,
            pmid="22833560",
            source="fulltext",
            factors=[
                "BCL6, T, c-MYC, MITF, BAF60C",
                "BCL6, T (BRACHYURY), c-MYC, MITF, BAF60C (SMARCD3)",
            ],
        ),
        "resolved_target_factor_and_evidence_updated",
        "Standardized target/factors and replaced methods-only evidence with the direct conversion result.",
        target_cell="chondrocyte",
        factors="BCL6, T (BRACHYURY), c-MYC, MITF, BAF60C (SMARCD3)",
        evidence_sentence="A five-factor pool of BCL6, T/BRACHYURY, c-MYC, MITF, and BAF60C rapidly converted postnatal human chorion and decidual cells into chondrocytes.",
    )

    # 31806618 rows are valid OSKM-plus-enhancer recipes; the v3 issue was weak evidence text.
    update_rows(
        df,
        select_rows(df, pmid="31806618", source="fulltext", factors="OCT4, SOX2, KLF4, MYC, GLIS1, NANOG, LIN28"),
        "resolved_evidence_updated",
        "Replaced vector-transduction-only evidence with direct colony outcome for OSKM+GNL.",
        evidence_sentence="Compared with OSKM, many ESC-like TRA-1-60-positive colonies readily appeared in the OSKM+GLIS1+NANOG+LIN28 condition after retroviral transduction.",
    )
    update_rows(
        df,
        select_rows(df, pmid="31806618", source="fulltext", factors="OCT4, SOX2, KLF4, MYC, GLIS1"),
        "resolved_evidence_updated",
        "Clarified that GLIS1 was tested as an OSKM enhancer, not a standalone single-factor recipe.",
        evidence_sentence="GLIS1 was added to OSKM and improved the reprogramming efficiency of human MSCs compared with OSKM alone.",
    )
    update_rows(
        df,
        select_rows(df, pmid="31806618", source="fulltext", factors="OCT4, SOX2, KLF4, MYC, NANOG"),
        "resolved_evidence_updated",
        "Clarified that NANOG was tested as an OSKM enhancer, not a standalone single-factor recipe.",
        evidence_sentence="NANOG was added to OSKM and improved the reprogramming efficiency of human MSCs compared with OSKM alone.",
    )

    # Vague abstract rows hidden because a more specific row from the same paper exists.
    mark_duplicate(
        df,
        select_rows(df, pmid="31489945", source="abstract", factors="defined small molecules, self-replicable mRNA"),
        "manual_31489945_fulltext",
        note="Hidden because fulltext row lists the mRNA factors and small molecules explicitly.",
    )
    mark_duplicate(
        df,
        select_rows(df, pmid="38817352", source="abstract", factors="CRISPRa (guide RNAs), small molecule cocktail (CRISPRa-SM)"),
        "manual_38817352_fulltext",
        note="Hidden because fulltext row lists CRISPRa target genes and small molecules explicitly.",
    )
    mark_duplicate(
        df,
        select_rows(df, pmid="34209429", source="abstract", factors="not specified"),
        "manual_34209429_fulltext",
        note="Hidden because a fulltext row gives the dual-SMAD protocol label.",
    )
    mark_duplicate(
        df,
        select_rows(df, pmid="30611717", source="abstract", factors="Eupatilin"),
        "manual_30611717_fulltext",
        note="Hidden because fulltext row has the more specific source-cell context.",
    )
    mark_duplicate(
        df,
        select_rows(df, pmid="33099273", source="abstract", factors="Foxg1 knockdown"),
        "manual_33099273_fulltext",
        note="Hidden because fulltext row has the more specific utricular source/target.",
    )
    mark_duplicate(
        df,
        select_rows(df, pmid="22833560", source="abstract", source_cell="human chorion cell"),
        "manual_22833560_fulltext",
        note="Hidden because fulltext row keeps the paper's combined chorion/decidua source wording.",
    )
    mark_duplicate(
        df,
        select_rows(df, pmid="22833560", source="abstract", source_cell="human decidual cell"),
        "manual_22833560_fulltext",
        note="Hidden because fulltext row keeps the paper's combined chorion/decidua source wording.",
    )

    # Incomplete recipes without enough information to reconstruct the full cocktail.
    hide_incomplete(
        df,
        select_rows(df, pmid="22415842", source="abstract", factors="defined factors, microRNA mimics"),
        "Hidden from default view: factors are too vague and the full OSKM/miRNA cocktail was not recovered locally.",
    )
    hide_incomplete(
        df,
        select_rows(df, pmid="21615676", source="abstract"),
        "Hidden from default view: listed compounds are reprogramming enhancers while the base iPSC factor cocktail is missing.",
    )

    # Keep broad cellular-plasticity rows but make confidence match the v3 partial/evidence-weak annotation.
    update_rows(
        df,
        select_rows(df, pmid="36104343", source="abstract", factors="M-CSF"),
        "confidence_lowered_needs_review",
        "Lowered confidence because v3 judged the evidence/factor role partial rather than a direct defined-factor recipe.",
        needs_review=True,
        confidence="medium",
    )
    update_rows(
        df,
        select_rows(df, pmid="27835665", source="abstract", source_cell="fibroblast", factors="Cytl1"),
        "confidence_lowered_needs_review",
        "Lowered confidence because v3 judged the evidence sentence indirect.",
        needs_review=True,
        confidence="medium",
    )

    # Decisive v3 actions do not need more review unless a later rule explicitly
    # marked them unresolved.
    decided_actions = {"remove", "hide_single_tf", "mark_duplicate", "hide_incomplete_recipe"}
    decided = df["validation_action"].astype(str).str.lower().isin(decided_actions)
    unresolved = df["validation_resolution"].astype(str).str.contains("needs_review", case=False, na=False)
    df.loc[decided & ~unresolved, "validation_needs_review"] = "False"
    df.loc[
        decided & ~unresolved & (df["validation_resolution"].astype(str).str.strip() == ""),
        "validation_resolution",
    ] = "resolved_hidden_or_rejected"

    action_counts = df["validation_action"].replace("", "(blank)").value_counts().to_dict()
    review_count = (df["validation_needs_review"].astype(str).str.lower() == "true").sum()
    resolution_counts = df["validation_resolution"].replace("", "(blank)").value_counts().to_dict()
    duplicate_count = (df["is_duplicate"].astype(str).str.lower() == "true").sum()

    print(f"validation_action: {action_counts}")
    print(f"validation_resolution: {resolution_counts}")
    print(f"validation_needs_review=True: {review_count}")
    print(f"is_duplicate=True: {duplicate_count}")

    if args.dry_run:
        print("Dry run only; no file written.")
        return

    df.to_csv(path, index=False, encoding="utf-8")
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
