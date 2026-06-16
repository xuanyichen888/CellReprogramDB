# Protein Interaction Pilot Pipeline

This pilot extends the CellReprogramDB workflow from cell-reprogramming recipes
to protein interaction pairs:

```text
binder / antibody -> target / antigen | amino-acid sequences | evidence / label
```

## Outputs

Run:

```bash
python3 build_protein_interaction_pilots.py
```

Generated files:

- `protein_interaction_outputs/de_novo_binder_pilot.csv`
- `protein_interaction_outputs/antibody_antigen_pilot.csv`
- `protein_interaction_outputs/manual_validation_sample.csv`
- `protein_interaction_outputs/protein_interaction_validation_memo.md`

The default run creates up to 300 rows per track. Use
`--max-rows-per-track N` to change the pilot size.

## Data Sources

- `yk0/proteinbase_interactions`: high-confidence seed data with binder and
  target sequences, binding labels, design classes, and antibody flags.
- `yk0/litscrape`: noisier literature-derived expansion data with binder and
  target sequences plus author-designated labels.

Raw CSVs are cached under `protein_interaction_outputs/raw/` so the generated
pilot can be inspected and reproduced.

## Shared Schema

Both pilot CSVs use the same core columns:

- `source_type`
- `binder_name`
- `binder_type`
- `binder_sequence`
- `target_name`
- `target_sequence`
- `interaction_label`
- `affinity_value`
- `affinity_unit`
- `evidence_text`
- `source_reference`
- `confidence`
- `needs_review`

Additional QA columns include `qa_flags`, `duplicate_group_id`, and
`sequence_pair_key`.

`manual_validation_sample.csv` contains stratified rows from both tracks plus
blank annotation columns for human review.

## Current Scope

The de novo binder pilot excludes antibody-derived rows and keeps miniprotein,
peptide, and other non-antibody binders.

The antibody-antigen pilot is a sequence-positive seed built from antibody-like
rows already present in the supplied datasets, mainly ProteinBase `Nanobody` and
`scFv` designs. It is not yet a full patent/literature antibody extraction.

Patent-scale antibody extraction still needs a separate parser for sequence
listings, `SEQ ID NO` mapping, and heavy/light-chain pairing validation.
