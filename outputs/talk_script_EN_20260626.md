# CellReprogramDB Progress Update — Talk Script (English)
# June 26, 2026 · ~5 minutes

---

## Opening / Slide 1 (30s)

Hi everyone, I'm Xuanyi Chen. Today I'll walk you through the latest updates on CellReprogramDB.

CellReprogramDB is an open, structured database of cell reprogramming recipes curated from PubMed. We systematically retrieve literature, extract experimental protocols using large language models, apply a human QA workflow, and publish everything as a free interactive web app.

---

## What is CellReprogramDB / Slide 2 (45s)

The pipeline has four stages.

First, we run a structured PubMed search to retrieve papers on cell reprogramming — iPSC reprogramming, direct conversion, directed differentiation, and transdifferentiation.

Second, we use LLMs — primarily DeepSeek and Claude — to extract "recipes" from abstracts or full text: what is the source cell, what is the target cell, which transcription factors or small molecules were used, and what species was studied.

Third, we run those extractions through a three-layer human QA workflow to catch and correct LLM errors.

Finally, the validated database is deployed as a Streamlit web app — fully filterable, searchable, and exportable, free for anyone to use.

---

## Database Growth / Slide 3 (45s)

Let me show you the numbers from this session.

The database now has **4,814 recipes** covering approximately 4,700 papers. After deduplication, that's **3,351 unique recipes**.

The biggest data boost this time came from review-reference mining: we scanned the reference lists of major review articles to find primary studies we had missed, ran them through our pipeline, and merged in **174 validated new entries** — growing the master database from 4,650 to 4,814 entries.

Factor fill rate is now at **85%** — meaning 85% of recipes have an explicitly recorded reprogramming factor list.

---

## QA Progress / Slide 4 (1 min)

This is the core of this session's work: systematic quality assurance.

We track entries that need human review using a `validation_needs_review` flag. At the start of this session, **407 entries** needed review. After two days of intensive QA, that's down to **146 entries** — a **64% reduction**.

We processed entries through three layers.

**Layer A**: Entries with an explicit correction action already recorded — things like "change factor_type from TF to small_molecule." We auto-executed these — 22 entries resolved.

**Layer B**: Entries with missing factors. We ran Claude Haiku extraction on their abstracts, then reviewed each result manually. Of 39 reviewed: 6 accepted as-is, 12 corrected, 17 rejected (abstract was genuinely uninformative), and 4 identified as duplicates.

**Layer C**: Entries with descriptive review notes — weak evidence, species ambiguity, missing source cell — classified and fixed systematically. 43 entries resolved.

We also deleted 10 cross-PMID duplicate rows. The remaining 146 entries mostly need PMC full text for factor information — we're fetching those asynchronously.

---

## App Stability / Slide 5 (45s)

On the deployment side, our Streamlit app runs on Python 3.14 in Streamlit Community Cloud.

During deployment we hit a critical bug: when a user's filter combination returned zero recipes, the app crashed with a `KeyError`.

The root cause was subtle. Python 3.14 ships pandas 3.x, which introduced Copy-on-Write semantics — and zero-row DataFrames lose normal column access under those semantics. Ironically, the `pandas<3.0.0` upper-bound constraint we had added for "safety" made things worse: pandas 2.x can't install on Python 3.14 at all, so the deployment failed entirely before the app could even start.

The fix was clean: detect the zero-match case early, display the zero-count metrics, then call `st.stop()` to halt script execution before any downstream column access. We also updated `requirements.txt` to `pandas>=2.2.0` with no upper bound. App has been stable since.

---

## Data Composition / Slide 6 (30s)

Looking at what's in the database: **53% human studies**, **41% mouse**, the rest dual-species or other. On confidence: **71% of entries are high-confidence**, reflecting solid LLM extraction quality. Medium and low confidence entries are queued for further review or full-text supplementation.

---

## Next Steps / Slide 7 (30s)

Four priorities ahead.

One, use PMC open-access full text to fill factor information for the 135 remaining factor-missing entries — auto-applying high-confidence LLM results.

Two, resolve the 9 entries where source cell is still missing.

Three, schema expansion — adding culture condition detail, finer factor type annotations, and co-factor relationships.

Four, after a final QA pass: public release on GitHub and Streamlit, with a data paper.

Thanks — happy to take questions.

---

## Anticipated Q&A

**Q: How reliable is the data?**
A: All sources are PubMed-indexed, peer-reviewed papers. LLM extractions go through a three-layer human QA workflow. High-confidence entries are backed by abstract or full-text evidence.

**Q: How does this compare to existing databases like the iPSC Database?**
A: We cover a broader scope — not just iPSC but direct conversion, differentiation, transdifferentiation. The pipeline is automated and updatable. The schema is structured for computational analysis.

**Q: Is it publicly available?**
A: Yes — the Streamlit app is open access now. The dataset and codebase will be open-sourced on GitHub, and we're planning a data descriptor paper for formal citation.
