# 🧬 CellReprogramDB

**A curated, searchable database of cell reprogramming recipes extracted from the PubMed literature.**

🔗 **Live app:** https://cellreprogramdb-sasemujyaflw3tbf9kk4pk.streamlit.app/

---

## Overview

Cell reprogramming — converting one cell type into another using defined factors — has generated thousands of papers since Yamanaka's 2006 iPSC discovery. CellReprogramDB systematically mines this literature and presents each protocol as a structured *recipe*:

```
Source cell  →  Target cell  |  Factors  |  Species  |  Confidence
```

Built from ~3,050 papers (1992–2026), the database currently presents **2,347 curated recipes** in its default view — covering transcription factor cocktails, small molecules, cytokines, miRNAs, and knockdown-based conversions. (4,650 total rows including lower-confidence and staged entries.)

---

## Pipeline

```
PubMed E-utilities
      │
      ▼
 fetch_pubmed.py          — Query 30+ search terms; download abstracts → papers.csv
      │
      ▼
 extract_recipes.py       — LLM-based structured extraction (DeepSeek API)
      │                     → source_cell / target_cell / factors / confidence
      ▼
 fetch_fulltext.py        — Retrieve PMC full-text for low-evidence entries
 extract_evidence.py      — Extract verbatim evidence sentences
      │
      ▼
QA / curation pipeline
  ├─ mark_duplicates.py         — Deduplicate across abstract & fulltext sources
  ├─ mark_broad_duplicates.py   — Optional broad cell-type merge annotations
  ├─ flag_single_tf.py          — Detect single-TF entries
  ├─ classify_single_tf.py      — standalone_valid vs cocktail_member vs unclear
  ├─ normalize_celltypes_std.py — Standardize cell type names
  ├─ fix_species.py             — Conservative rule-based species cleanup
  ├─ fill_species_deepseek.py   — Checkpointed LLM pass for remaining species blanks
  ├─ fill_missing_factors_deepseek.py — Checkpointed LLM pass for missing factors
  ├─ fix_factor_types.py        — Normalize and infer factor type labels
  ├─ fill_conversion_scope_deepseek.py — Checkpointed LLM pass for unclear conversion scope
  ├─ reextract_evidence_flagged.py — Re-extract weak/missing evidence sentences
  └─ apply_v3_curation_fixes.py — Manual QA annotation integration
      │
      ▼
 recipes_master_v2.csv    — Final curated master table
      │
      ▼
 app.py                   — Streamlit web app
```

---

## Key Features

**Structured extraction via LLM**
- Prompt-engineered extraction of source cell, target cell, factor cocktail, species, and conversion type from free-text abstracts
- Filters prior-work citations, failed experiments, and methods-only sentences at extraction time
- Multi-round QA: evidence re-extraction with fulltext fallback; `needs_review` flagging; manual annotation integration

**Recipe selection criteria**
- Included entries must report a successful source-cell to target-cell conversion in the cited paper itself
- A recipe must have named factor(s), chemicals, cytokines, miRNAs, knockdowns, or other defined perturbations
- Prior-work citations, failed attempts, methods-only descriptions, review-only claims, and entries without specific factors are excluded or flagged for review
- Evidence text should support actual conversion, not only expression, culture setup, or experimental intention

**Confidence tiering**
- `high`: source, target, and named factors all explicitly stated as a successful result
- `medium`: likely conversion but source, target, factors, or evidence provenance are partial, vague, or need full-text confirmation
- `low`: inferred or weakly supported; hidden by default

**Single-TF classification**
- Distinguishes standalone valid single-factor conversions (NGN2, ASCL1, MYOD1, ETV2 …) from cocktail-member studies (OCT4, SOX2, KLF4 alone in an OSKM context)
- Cocktail members and unclear single-TF entries are hidden by default; standalone entries remain visible

**Species cleanup**
- Existing species labels are normalized into stable values such as `human`, `mouse`, `human, mouse`, `rat`, and `porcine`
- Remaining blank species rows are processed by a checkpointed DeepSeek pass; low-confidence or unclear model outputs are left blank for manual review

**Factor cleanup**
- Missing factor lists are first filled from strict same-paper/source/target matches, then by a checkpointed DeepSeek pass for high/medium-confidence research rows
- Vague outputs such as `defined factors`, `factor pairs`, or unnamed chemical cocktails remain hidden as `not specified`

**Conversion scope cleanup**
- Recipes are classified as classical reprogramming, lineage conversion, directed differentiation, cell-state modulation, or unclear
- Remaining unclear rows are processed by a checkpointed DeepSeek pass, with low-confidence cases left as `unclear`

**Validation (honest, not over-claimed)**
- Manual QA on a stratified sample (n=50): ~62% *strict* precision (source, target, and factors all exactly correct) and ~96% *relaxed* precision (reprogramming event correctly identified)
- TFcomb (Kamimoto et al. 2023): 7/7 experimental reference recipes covered; Taiji-reprogram landmark recipes mostly covered, with some liver-cocktail gaps

**Interactive Streamlit app**
- Filter by target/source cell type, factors, species, confidence, paper type, journal, year range
- Switch between **Unique recipes** and **All paper records**; unique recipe rows list supporting PMIDs rather than silently hiding duplicate papers
- Switch between exact recipe deduplication and broad cell-type merge mode
- Filter by factor class (contains TF, chemical/small-molecule, non-TF only) and broad cell category (immune, neural, hepatic, cardiac, pancreatic, etc.)
- Inspect evidence source and local full-text cache status (`abstract`, `full text`, `manual`; full text available vs recipe extracted from full text)
- Dynamic charts: recipes by year, top target cell types, factor type distribution
- Downloadable filtered CSV

---

## Repository Structure

```
app.py                     Streamlit web application
fetch_pubmed.py            PubMed search and abstract download
extract_recipes.py         LLM recipe extraction (abstract pass)
fetch_fulltext.py          PMC full-text retrieval
extract_evidence.py        Evidence sentence extraction
reextract_evidence_flagged.py  Re-extraction for weak/missing evidence
mark_duplicates.py         Duplicate detection
mark_broad_duplicates.py   Broad cell-type duplicate detection
classify_single_tf.py      Single-TF classification
normalize_celltypes_std.py Cell type name standardization
fix_species.py             Rule-based species cleanup
fill_species_deepseek.py   LLM-assisted species cleanup
fill_missing_factors_deepseek.py  LLM-assisted factor cleanup
fix_factor_types.py        Factor type normalization
fill_conversion_scope_deepseek.py LLM-assisted conversion scope cleanup
recipes_master_v2.csv      Final curated database
papers.csv                 Raw PubMed abstracts
fulltext.csv               PMC full-text excerpts
requirements.txt           Python dependencies
```

## Protein Interaction Pilot

This repository now also includes a small pilot pipeline for Wang Lab's proposed
extension from cell-reprogramming recipes to protein interaction pairs:

```
binder / antibody → target / antigen | amino-acid sequences | evidence / label
```

Run:

```bash
python3 build_protein_interaction_pilots.py
```

This creates:

- `protein_interaction_outputs/de_novo_binder_pilot.csv`
- `protein_interaction_outputs/antibody_antigen_pilot.csv`
- `protein_interaction_outputs/manual_validation_sample.csv`
- `protein_interaction_outputs/protein_interaction_validation_memo.md`

See `PROTEIN_INTERACTION_PIPELINE.md` for scope, schema, and validation notes.

---

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

The extraction pipeline requires a [DeepSeek API key](https://platform.deepseek.com/):

```bash
export DEEPSEEK_API_KEY=sk-...
python fetch_pubmed.py       # Step 1: download abstracts
python extract_recipes.py    # Step 2: extract recipes
python extract_evidence.py   # Step 3: extract evidence sentences
```

### Broad Search Expansion

The widened PubMed search terms can be previewed without an API key or file writes:

```bash
python fetch_pubmed.py --preview-only --categories broad_discovery
```

To fetch the first capped broad-search candidate set into a separate CSV without
touching the main `papers.csv`:

```bash
python fetch_pubmed.py \
  --categories broad_discovery \
  --output outputs/broad_discovery_candidates_YYYYMMDD.csv
```

The script compares against `recipes_master_v2.csv` and `papers.csv` by default,
so preview counts are reported as new candidate PMIDs rather than already-covered
papers.

To run a small, cost-controlled extraction pilot on a candidate CSV, write to a
separate output and checkpoint first:

```bash
python extract_recipes.py \
  --input outputs/broad_discovery_priority_YYYYMMDD.csv \
  --limit 300 \
  --output outputs/recipes_broad_priority_300_YYYYMMDD.csv \
  --checkpoint outputs/checkpoint_extract_broad_priority_300_YYYYMMDD.json \
  --dry-run
```

After confirming the selected PMIDs, remove `--dry-run` and run with
`DEEPSEEK_API_KEY` set. Do not merge this pilot into `recipes_master_v2.csv`
until a manual precision check passes.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Literature retrieval | Biopython `Entrez`, PubMed E-utilities |
| LLM extraction | DeepSeek API (OpenAI-compatible), prompt engineering |
| Full-text access | PubMed Central OA FTP / E-utilities |
| Data processing | Python, pandas |
| Web app | Streamlit |
| Deployment | Streamlit Community Cloud |

---

## Validation

Manual QA was performed on a stratified random sample (n=50) of high/medium confidence entries on the curated default view. **Strict precision ~62%** (source cell, target cell, AND factors all exactly correct and supported by the cited paper); **relaxed precision ~96%** (the reprogramming event is correctly identified, allowing minor source/target/factor specificity differences). The main quality risk is exact source/target/factor specificity, not failing to detect reprogramming.

Benchmark coverage against published prediction tool datasets:
- **TFcomb** (Kamimoto et al. 2023): 7/7 experimental reference recipes covered
- **Taiji-reprogram**: major landmark recipes covered; liver cocktail variants partially matched

---

## Limitations

- Abstracts only for ~60% of entries; full-text extraction improves coverage for recent open-access papers
- Cell type names are partially standardized; ontology mapping (Cell Ontology) is planned
- Factor names use author-reported nomenclature; cross-species gene symbol normalization is incomplete
- ~270 entries remain flagged `needs_review` and are hidden from the default view pending manual QA
- `full text available` means the paper has text in the local cache; it does not mean every recipe from that paper has been manually verified from the full text

---

## Citation

If you use CellReprogramDB in your research, please cite:

> Chen X. CellReprogramDB: a curated database of cell reprogramming recipes extracted from the PubMed literature. 2025. https://cellreprogramdb-sasemujyaflw3tbf9kk4pk.streamlit.app/

---

*Pre-release v0.9.*
