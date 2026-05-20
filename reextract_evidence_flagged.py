"""
Re-extract evidence sentences for the 146 'needs_review' entries
using an improved prompt that explicitly avoids:
  1. Pure methods/setup sentences (cells were transduced with...)
  2. Sentences that cite prior/published work (we previously showed...)

After re-extraction:
  - If a better sentence is found → update evidence_sentence, clear needs_review flag
  - If still no good sentence found → keep needs_review, note in validation_notes
"""

import json
import os
import time
import shutil

import pandas as pd
from openai import OpenAI

API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com"
MODEL    = "deepseek-v4-flash"
FILE     = "recipes_master_v2.csv"
SLEEP    = 0.8

# ── Improved evidence extraction prompt ──────────────────────────────────────
SYSTEM_PROMPT = """\
You are a biomedical text mining expert specializing in cell reprogramming literature.

Given a reprogramming recipe (source cell → target cell, using specific factors) and the original text,
find the single best sentence that directly demonstrates the conversion was successfully achieved in THIS paper.

Respond ONLY with valid JSON:
{
  "evidence_sentence": "...",
  "quality": "good|weak|none",
  "reason": "one sentence explaining your choice"
}

REQUIRED: The chosen sentence MUST describe a RESULT or OUTCOME, such as:
  ✓ Successfully generated / produced / obtained / derived X from Y
  ✓ X was converted / reprogrammed / transdifferentiated into Y
  ✓ TRA-1-60+ / OCT4+ / iPSC colonies appeared / were observed
  ✓ Efficiency of X% was achieved
  ✓ Cells acquired/expressed markers of the target cell type
  ✓ "Here we report the generation of X from Y using Z" (announcements of a new result ARE acceptable)

FORBIDDEN — do NOT select sentences that:
  ✗ Cite prior or previously published work:
      "We previously showed...", "We have previously demonstrated...",
      "Recently, we identified...", "As reported before...",
      "It has been shown that...", "As described previously..."
  ✗ Describe only the experimental setup/methods without stating an outcome:
      "Cells were transduced with...", "We used X to investigate...",
      "Our objective was to determine...", "We treated cells with..."
      "To test whether...", "We aimed to explore..."
  ✗ Describe negative / failed results:
      "X failed to induce Y", "No colonies were observed", "Did not produce Z"

Scoring guide:
  quality="good"  — sentence clearly names source cell, target cell, and factor(s), and states the conversion occurred
  quality="weak"  — sentence partially supports the recipe (names only 1-2 components, or uses indirect language)
  quality="none"  — no sentence in the text directly supports the recipe as described; return evidence_sentence=""

Return the exact sentence copied word-for-word from the text. Do NOT paraphrase.
"""


def append_note(existing: str, note: str) -> str:
    existing = str(existing).strip()
    note = str(note).strip()
    if not note or note in existing:
        return existing
    return f"{existing} || {note}" if existing else note


def call_api(client, recipe: dict, text: str) -> dict:
    user_msg = (
        f"Recipe:\n"
        f"  source_cell: {recipe['source_cell']}\n"
        f"  target_cell: {recipe['target_cell']}\n"
        f"  factors: {recipe['factors']}\n\n"
        f"Original text:\n{text[:10000]}"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        temperature=0.0,
        max_tokens=512,
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def main():
    if not API_KEY:
        raise SystemExit("请先运行: export DEEPSEEK_API_KEY=sk-...")

    df = pd.read_csv(FILE, dtype=str).fillna("")

    # Load source texts
    abstracts = {
        p["pmid"]: p["abstract"]
        for p in pd.read_csv("papers.csv", dtype=str).fillna("").to_dict("records")
    }
    fulltext_map = {}
    if os.path.exists("fulltext.csv"):
        for row in pd.read_csv("fulltext.csv", dtype=str).fillna("").to_dict("records"):
            fulltext_map[row["pmid"]] = (row.get("methods_text","") + " " + row.get("results_text","")).strip()

    # Target rows: needs_review = True, not already removed/hidden
    target = df[
        (df["validation_needs_review"].str.lower() == "true") &
        (~df["validation_action"].isin(["remove", "hide_incomplete_recipe", "hide_single_tf"]))
    ].copy()

    print(f"Target needs_review entries: {len(target)}")
    print(f"By resolution:")
    print(target["validation_resolution"].value_counts().to_string())
    print()

    updated    = 0
    no_better  = 0
    errors     = 0

    for idx in target.index:
        row  = df.loc[idx]
        pmid = row["pmid"]
        src  = row.get("source", "abstract")

        # Get source text
        if src == "fulltext" and pmid in fulltext_map:
            text = fulltext_map[pmid]
        else:
            text = abstracts.get(pmid, "")

        if not text.strip():
            print(f"  [{idx}] PMID {pmid}: no text, skip")
            no_better += 1
            continue

        print(f"  [{idx}] PMID {pmid} | {row['source_cell'][:20]} -> {row['target_cell'][:20]} ... ", end="", flush=True)

        try:
            result = call_api(client if 'client' in dir() else (
                setattr(__builtins__, '_cl', OpenAI(api_key=API_KEY, base_url=BASE_URL)) or
                OpenAI(api_key=API_KEY, base_url=BASE_URL)
            ), row.to_dict(), text)
            new_ev  = result.get("evidence_sentence", "").strip()
            quality = result.get("quality", "none")
            reason  = result.get("reason", "")
        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1
            time.sleep(3)
            continue

        old_ev = str(row.get("evidence_sentence", "")).strip()

        if quality == "good" and new_ev and new_ev != old_ev:
            df.at[idx, "evidence_sentence"] = new_ev
            df.at[idx, "validation_needs_review"] = "False"
            df.at[idx, "validation_resolution"] = "resolved_evidence_updated_v2"
            df.at[idx, "validation_notes"] = append_note(
                row["validation_notes"],
                f"Evidence updated (v2 prompt): {reason}"
            )
            print(f"✓ UPDATED (quality={quality})")
            updated += 1
        elif quality == "weak" and new_ev and new_ev != old_ev:
            df.at[idx, "evidence_sentence"] = new_ev
            df.at[idx, "validation_resolution"] = df.at[idx, "validation_resolution"]  # keep
            df.at[idx, "validation_notes"] = append_note(
                row["validation_notes"],
                f"Evidence replaced with best-available weak sentence (v2 prompt): {reason}"
            )
            print(f"~ weak updated")
            updated += 1
        else:
            df.at[idx, "validation_notes"] = append_note(
                row["validation_notes"],
                f"Re-extraction v2: still no good evidence sentence in source text. {reason}"
            )
            print(f"✗ no better sentence")
            no_better += 1

        time.sleep(SLEEP)

    print(f"\nUpdated (evidence improved): {updated}")
    print(f"No better sentence found:    {no_better}")
    print(f"Errors:                      {errors}")
    print(f"Final needs_review=True:     {(df['validation_needs_review'].str.lower()=='true').sum()}")

    # Recount default view
    shown = df[
        (df["is_duplicate"].str.lower() != "true") &
        (~df["validation_action"].isin(["remove","hide_incomplete_recipe","hide_single_tf"])) &
        (df["factors"] != "not specified") &
        (df["single_tf_flag"].str.lower() != "true") &
        (df["paper_type"] == "research") &
        (df["confidence"].isin(["high","medium"]))
    ]
    print(f"默认显示条目数: {len(shown)}")

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"\n保存至 {FILE}")


if __name__ == "__main__":
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    main()
