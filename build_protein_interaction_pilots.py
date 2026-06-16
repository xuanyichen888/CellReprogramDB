"""
Build pilot protein interaction CSVs for the Wang extension plan.

Outputs:
  protein_interaction_outputs/de_novo_binder_pilot.csv
  protein_interaction_outputs/antibody_antigen_pilot.csv
  protein_interaction_outputs/manual_validation_sample.csv
  protein_interaction_outputs/protein_interaction_validation_memo.md

The first implementation uses the two HuggingFace datasets Dr. Wang shared:
  - yk0/proteinbase_interactions
  - yk0/litscrape

The antibody-antigen CSV is a sequence-positive seed built from antibody-derived
ProteinBase rows (Nanobody/scFv) plus any LitScrape rows that can be identified
by antibody-like identifiers. Patent/literature extraction is intentionally left
as a next stage because sequence-listing parsing needs a separate workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import urllib.request
from pathlib import Path

import pandas as pd


PROTEINBASE_URL = (
    "https://huggingface.co/datasets/yk0/proteinbase_interactions/resolve/main/data.csv"
)
LITSCRAPE_URL = "https://huggingface.co/datasets/yk0/litscrape/resolve/main/data.csv"

RAW_PROTEINBASE = "proteinbase_interactions.csv"
RAW_LITSCRAPE = "litscrape.csv"

AA_EXTENDED = set("ACDEFGHIKLMNPQRSTVWYBXZUO")
SHARED_COLUMNS = [
    "source_type",
    "binder_name",
    "binder_type",
    "binder_sequence",
    "target_name",
    "target_sequence",
    "interaction_label",
    "affinity_value",
    "affinity_unit",
    "evidence_text",
    "source_reference",
    "confidence",
    "needs_review",
]
EXTRA_COLUMNS = [
    "source_dataset",
    "source_id",
    "design_class",
    "qa_flags",
    "duplicate_group_id",
    "sequence_pair_key",
]
OUTPUT_COLUMNS = SHARED_COLUMNS + EXTRA_COLUMNS


def clean_text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def clean_sequence(value) -> str:
    text = clean_text(value).upper()
    return re.sub(r"\s+", "", text)


def valid_sequence(seq: str) -> bool:
    return bool(seq) and all(ch in AA_EXTENDED for ch in seq)


def normalized_target(value: str) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_float(value) -> str:
    text = clean_text(value)
    if not text:
        return ""
    try:
        return f"{float(text):.8g}"
    except ValueError:
        match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)
        return f"{float(match.group(0)):.8g}" if match else ""


def map_label(value) -> str:
    text = clean_text(value).lower()
    if text in {"1", "true", "yes", "weak", "medium", "strong", "positive", "binder"}:
        return "binder"
    if text in {"0", "false", "no", "none", "negative", "non-binder", "nonbinder"}:
        return "non-binder"
    return "unclear"


def map_binder_type(design_class: str, fallback: str = "") -> str:
    text = f"{clean_text(design_class)} {clean_text(fallback)}".lower()
    if "nanobody" in text or "vhh" in text:
        return "nanobody"
    if "scfv" in text or "scfv" in text.replace("-", ""):
        return "scFv"
    if "antibody" in text or re.search(r"\bmab\b|\bfab\b", text):
        return "antibody"
    if "mini" in text:
        return "miniprotein"
    if "peptide" in text:
        return "peptide"
    return "other"


def sequence_pair_key(binder_sequence: str, target_sequence: str) -> str:
    payload = f"{binder_sequence}|{target_sequence}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def append_flag(flags: list[str], condition: bool, flag: str) -> None:
    if condition:
        flags.append(flag)


def add_common_qa(row: dict, base_flags: list[str] | None = None) -> dict:
    flags = list(base_flags or [])
    binder_sequence = row["binder_sequence"]
    target_sequence = row["target_sequence"]
    append_flag(flags, not binder_sequence, "missing_binder_sequence")
    append_flag(flags, not target_sequence, "missing_target_sequence")
    append_flag(flags, bool(binder_sequence) and not valid_sequence(binder_sequence), "invalid_binder_sequence")
    append_flag(flags, bool(target_sequence) and not valid_sequence(target_sequence), "invalid_target_sequence")
    append_flag(flags, row["interaction_label"] == "unclear", "unclear_interaction_label")
    append_flag(flags, bool(binder_sequence) and len(binder_sequence) < 5, "binder_sequence_too_short")
    append_flag(flags, bool(target_sequence) and len(target_sequence) < 5, "target_sequence_too_short")

    low_confidence_flags = {
        "missing_binder_sequence",
        "missing_target_sequence",
        "invalid_binder_sequence",
        "invalid_target_sequence",
    }
    if any(flag in low_confidence_flags for flag in flags):
        row["confidence"] = "low"
        row["needs_review"] = "True"
    elif row["interaction_label"] == "unclear":
        row["confidence"] = "medium"
        row["needs_review"] = "True"
    else:
        row["needs_review"] = row.get("needs_review", "False")

    row["qa_flags"] = ";".join(dict.fromkeys(flags))
    row["sequence_pair_key"] = sequence_pair_key(binder_sequence, target_sequence)
    return row


def download(url: str, path: Path, force: bool = False) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as response:
        path.write_bytes(response.read())


def normalize_proteinbase(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str).fillna("")
    rows: list[dict] = []
    for _, r in raw.iterrows():
        design_class = clean_text(r.get("design_class", ""))
        binder_type = map_binder_type(design_class, r.get("binder_name", ""))
        label = map_label(r.get("label", ""))
        binding_strength = clean_text(r.get("binding_strength", ""))
        affinity = parse_float(r.get("value", ""))
        flags: list[str] = []
        append_flag(flags, bool(affinity) and not clean_text(r.get("value", "")), "affinity_parse_from_text")
        append_flag(flags, bool(affinity), "affinity_unit_unverified")

        row = {
            "source_type": "ProteinBase",
            "binder_name": clean_text(r.get("binder_name", "")) or clean_text(r.get("proteinbase_id", "")),
            "binder_type": binder_type,
            "binder_sequence": clean_sequence(r.get("binder_sequence", "")),
            "target_name": normalized_target(r.get("target", "")),
            "target_sequence": clean_sequence(r.get("target_sequence", "")),
            "interaction_label": label,
            "affinity_value": affinity,
            "affinity_unit": "source_KD" if affinity else "",
            "evidence_text": (
                f"ProteinBase row: binding_strength={binding_strength or 'not reported'}; "
                f"label={clean_text(r.get('label', '')) or 'not reported'}; "
                f"design_class={design_class or 'not reported'}."
            ),
            "source_reference": clean_text(r.get("proteinbase_id", "")),
            "confidence": "high",
            "needs_review": "False",
            "source_dataset": "yk0/proteinbase_interactions",
            "source_id": clean_text(r.get("proteinbase_id", "")),
            "design_class": design_class,
            "duplicate_group_id": "",
        }
        rows.append(add_common_qa(row, flags))
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def normalize_litscrape(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str).fillna("")
    rows: list[dict] = []
    for _, r in raw.iterrows():
        source_publication = clean_text(r.get("source_publication", ""))
        binder_id = clean_text(r.get("binder_id", ""))
        binder_type = map_binder_type("", f"{source_publication} {binder_id}")
        label = map_label(r.get("label", ""))
        affinity_nm = parse_float(r.get("binding_affinity_nm", ""))
        row = {
            "source_type": "LitScrape",
            "binder_name": binder_id,
            "binder_type": binder_type,
            "binder_sequence": clean_sequence(r.get("binder_sequence", "")),
            "target_name": normalized_target(r.get("target", "")),
            "target_sequence": clean_sequence(r.get("target_sequence", "")),
            "interaction_label": label,
            "affinity_value": affinity_nm,
            "affinity_unit": "nM" if affinity_nm else "",
            "evidence_text": (
                f"LitScrape author-designated label={clean_text(r.get('label', '')) or 'not reported'}; "
                f"source={source_publication or 'not reported'}."
            ),
            "source_reference": source_publication,
            "confidence": "medium",
            "needs_review": "False",
            "source_dataset": "yk0/litscrape",
            "source_id": binder_id,
            "design_class": "",
            "duplicate_group_id": "",
        }
        rows.append(add_common_qa(row))
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def resolve_duplicate_labels(labels: pd.Series) -> str:
    unique = set(labels.dropna().astype(str))
    if len(unique) == 1:
        return next(iter(unique))
    if "binder" in unique and "non-binder" in unique:
        return "unclear"
    if "binder" in unique:
        return "binder"
    if "non-binder" in unique:
        return "non-binder"
    return "unclear"


def deduplicate_sequence_pairs(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if df.empty:
        return df.copy(), 0

    work = df.copy()
    work["_source_rank"] = work["source_type"].map({"ProteinBase": 0, "LitScrape": 1}).fillna(9)
    work["_label_rank"] = work["interaction_label"].map({"binder": 0, "non-binder": 1, "unclear": 2}).fillna(3)
    work = work.sort_values(["_source_rank", "_label_rank", "target_name", "binder_name"])

    duplicate_count = int(work.duplicated("sequence_pair_key").sum())
    records = []
    for key, group in work.groupby("sequence_pair_key", sort=False):
        row = group.iloc[0].drop(labels=["_source_rank", "_label_rank"]).to_dict()
        resolved_label = resolve_duplicate_labels(group["interaction_label"])
        flags = [f for f in clean_text(row.get("qa_flags", "")).split(";") if f]
        if len(group) > 1:
            flags.append("duplicate_sequence_pair_collapsed")
            row["duplicate_group_id"] = f"seqpair_{key}"
            row["source_reference"] = ";".join(
                dict.fromkeys(clean_text(v) for v in group["source_reference"] if clean_text(v))
            )
        if resolved_label != row["interaction_label"]:
            flags.append("conflicting_duplicate_labels")
            row["interaction_label"] = resolved_label
            row["confidence"] = "medium"
            row["needs_review"] = "True"
        row["qa_flags"] = ";".join(dict.fromkeys(flags))
        records.append(row)
    return pd.DataFrame(records, columns=OUTPUT_COLUMNS), duplicate_count


def stratified_pilot(df: pd.DataFrame, max_rows: int, seed: int = 20260616) -> pd.DataFrame:
    if len(df) <= max_rows:
        return df.sort_values(["source_type", "interaction_label", "target_name", "binder_name"]).reset_index(drop=True)

    groups = list(df.groupby(["source_type", "interaction_label", "binder_type"], dropna=False))
    per_group = max(1, max_rows // max(1, len(groups)))
    selected = []
    selected_index = set()
    for _, group in groups:
        take = min(per_group, len(group))
        sample = group.sample(n=take, random_state=seed)
        selected.append(sample)
        selected_index.update(sample.index.tolist())

    out = pd.concat(selected, ignore_index=False) if selected else pd.DataFrame(columns=df.columns)
    if len(out) < max_rows:
        remaining = df[~df.index.isin(selected_index)]
        take = min(max_rows - len(out), len(remaining))
        if take:
            out = pd.concat([out, remaining.sample(n=take, random_state=seed + 1)], ignore_index=False)

    return out.sort_values(["source_type", "interaction_label", "target_name", "binder_name"]).head(max_rows).reset_index(drop=True)


def build_tracks(proteinbase: pd.DataFrame, litscrape: pd.DataFrame, max_rows: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    combined = pd.concat([proteinbase, litscrape], ignore_index=True)

    antibody_types = {"antibody", "nanobody", "scFv"}
    de_novo_pool = combined[~combined["binder_type"].isin(antibody_types)].copy()
    antibody_pool = combined[combined["binder_type"].isin(antibody_types)].copy()

    de_novo_dedup, de_novo_dupes = deduplicate_sequence_pairs(de_novo_pool)
    antibody_dedup, antibody_dupes = deduplicate_sequence_pairs(antibody_pool)

    de_novo_pilot = stratified_pilot(de_novo_dedup, max_rows)
    antibody_pilot = stratified_pilot(antibody_dedup, max_rows)

    stats = {
        "combined_rows": len(combined),
        "de_novo_pool_rows": len(de_novo_pool),
        "antibody_pool_rows": len(antibody_pool),
        "de_novo_duplicate_rows_collapsed": de_novo_dupes,
        "antibody_duplicate_rows_collapsed": antibody_dupes,
        "de_novo_dedup_rows": len(de_novo_dedup),
        "antibody_dedup_rows": len(antibody_dedup),
    }
    return de_novo_pilot, antibody_pilot, stats


def pct(num: int, den: int) -> float:
    return round(num / den * 100, 1) if den else 0.0


def track_metrics(df: pd.DataFrame) -> dict:
    both_valid = df.apply(
        lambda r: valid_sequence(r["binder_sequence"]) and valid_sequence(r["target_sequence"]),
        axis=1,
    )
    both_present = (df["binder_sequence"].astype(bool)) & (df["target_sequence"].astype(bool))
    affinity_parsed = df["affinity_value"].astype(str).str.strip().ne("")
    needs_review = df["needs_review"].astype(str).str.lower().eq("true")
    return {
        "rows": len(df),
        "valid_sequence_rows": int(both_valid.sum()),
        "valid_sequence_pct": pct(int(both_valid.sum()), len(df)),
        "both_sequence_rows": int(both_present.sum()),
        "both_sequence_pct": pct(int(both_present.sum()), len(df)),
        "affinity_rows": int(affinity_parsed.sum()),
        "affinity_pct": pct(int(affinity_parsed.sum()), len(df)),
        "needs_review_rows": int(needs_review.sum()),
        "needs_review_pct": pct(int(needs_review.sum()), len(df)),
        "source_counts": df["source_type"].value_counts().to_dict(),
        "label_counts": df["interaction_label"].value_counts().to_dict(),
        "binder_type_counts": df["binder_type"].value_counts().to_dict(),
    }


def manual_review_sample(de_novo: pd.DataFrame, antibody: pd.DataFrame, n_per_track: int = 25) -> pd.DataFrame:
    samples = []
    for track, frame in [("de_novo_binder", de_novo), ("antibody_antigen", antibody)]:
        sample = stratified_pilot(frame.copy(), min(n_per_track, len(frame)), seed=20260617)
        sample.insert(0, "track", track)
        sample.insert(1, "manual_pairing_correct", "")
        sample.insert(2, "manual_target_mapping_correct", "")
        sample.insert(3, "manual_label_supported", "")
        sample.insert(4, "manual_sequence_type", "")
        sample.insert(5, "manual_notes", "")
        samples.append(sample)
    return pd.concat(samples, ignore_index=True) if samples else pd.DataFrame()


def format_counts(counts: dict) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}: {value}" for key, value in counts.items())


def write_memo(path: Path, de_novo: pd.DataFrame, antibody: pd.DataFrame, manual_sample: pd.DataFrame, stats: dict) -> None:
    de = track_metrics(de_novo)
    ab = track_metrics(antibody)
    text = f"""# Protein Interaction Pilot Validation Memo

Generated by `build_protein_interaction_pilots.py`.

## Outputs

- `de_novo_binder_pilot.csv`: {de['rows']} non-antibody binder-target rows.
- `antibody_antigen_pilot.csv`: {ab['rows']} antibody-derived binder-target rows.
- `manual_validation_sample.csv`: {len(manual_sample)} stratified rows for human review.

## Source Datasets

- ProteinBase raw rows plus LitScrape raw rows: {stats['combined_rows']:,}
- De novo candidate pool before deduplication: {stats['de_novo_pool_rows']:,}
- Antibody-derived candidate pool before deduplication: {stats['antibody_pool_rows']:,}
- De novo duplicate sequence-pair rows collapsed: {stats['de_novo_duplicate_rows_collapsed']:,}
- Antibody duplicate sequence-pair rows collapsed: {stats['antibody_duplicate_rows_collapsed']:,}

## De Novo Binder Pilot QA

- Rows with both valid binder and target amino-acid sequences: {de['valid_sequence_rows']}/{de['rows']} ({de['valid_sequence_pct']}%)
- Rows with both sequence fields present: {de['both_sequence_rows']}/{de['rows']} ({de['both_sequence_pct']}%)
- Rows with parsed affinity values: {de['affinity_rows']}/{de['rows']} ({de['affinity_pct']}%)
- Rows flagged `needs_review`: {de['needs_review_rows']}/{de['rows']} ({de['needs_review_pct']}%)
- Source counts: {format_counts(de['source_counts'])}
- Label counts: {format_counts(de['label_counts'])}
- Binder type counts: {format_counts(de['binder_type_counts'])}

## Antibody-Antigen Seed QA

- Rows with both valid antibody/binder and target amino-acid sequences: {ab['valid_sequence_rows']}/{ab['rows']} ({ab['valid_sequence_pct']}%)
- Rows with both sequence fields present: {ab['both_sequence_rows']}/{ab['rows']} ({ab['both_sequence_pct']}%)
- Rows with parsed affinity values: {ab['affinity_rows']}/{ab['rows']} ({ab['affinity_pct']}%)
- Rows flagged `needs_review`: {ab['needs_review_rows']}/{ab['rows']} ({ab['needs_review_pct']}%)
- Source counts: {format_counts(ab['source_counts'])}
- Label counts: {format_counts(ab['label_counts'])}
- Binder type counts: {format_counts(ab['binder_type_counts'])}

## Interpretation

The de novo binder pilot is ready for manual inspection because the supplied datasets
already contain paired binder and target amino-acid sequences. ProteinBase rows are
treated as higher-confidence seed data; LitScrape rows are treated as noisier
literature-derived expansion data.

The antibody-antigen file is a sequence-positive seed, not a full patent/literature
extraction. It contains antibody-derived binders identified from dataset metadata
(`Nanobody`, `scFv`, or antibody-like identifiers). A patent-scale antibody pipeline
still needs sequence-listing parsing, SEQ ID NO mapping, and heavy/light-chain
pairing validation.

## Manual Validation Checklist

- Confirm that target names map to the intended antigen/protein.
- Confirm binder-target labels against the original source for a stratified sample.
- For antibody rows, classify whether the sequence is nanobody, scFv, heavy chain,
  light chain, CDR-only, or other antibody fragment.
- For future patent rows, require every sequence to be mapped to a SEQ ID NO and
  evidence sentence before marking confidence as high.
"""
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="protein_interaction_outputs")
    parser.add_argument("--max-rows-per-track", type=int, default=300)
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    proteinbase_path = raw_dir / RAW_PROTEINBASE
    litscrape_path = raw_dir / RAW_LITSCRAPE
    download(PROTEINBASE_URL, proteinbase_path, force=args.force_download)
    download(LITSCRAPE_URL, litscrape_path, force=args.force_download)

    proteinbase = normalize_proteinbase(proteinbase_path)
    litscrape = normalize_litscrape(litscrape_path)
    de_novo, antibody, stats = build_tracks(proteinbase, litscrape, args.max_rows_per_track)

    de_novo_path = output_dir / "de_novo_binder_pilot.csv"
    antibody_path = output_dir / "antibody_antigen_pilot.csv"
    manual_sample_path = output_dir / "manual_validation_sample.csv"
    memo_path = output_dir / "protein_interaction_validation_memo.md"

    manual_sample = manual_review_sample(de_novo, antibody)
    de_novo.to_csv(de_novo_path, index=False)
    antibody.to_csv(antibody_path, index=False)
    manual_sample.to_csv(manual_sample_path, index=False)
    write_memo(memo_path, de_novo, antibody, manual_sample, stats)

    print(f"Wrote {de_novo_path} ({len(de_novo)} rows)")
    print(f"Wrote {antibody_path} ({len(antibody)} rows)")
    print(f"Wrote {manual_sample_path} ({len(manual_sample)} rows)")
    print(f"Wrote {memo_path}")


if __name__ == "__main__":
    main()
