"""
Classify paper_type for default-visible "research" rows that look like
reviews / protocols / perspectives (清单 2: review/non-primary mixed-in).

Reads the paper title + abstract (papers.csv) and asks DeepSeek whether the
paper is a PRIMARY experimental study or not.

SAFETY: proposal-only by default. It writes a proposal CSV and NEVER touches
recipes_master_v2.csv unless you pass --apply. This matches the project rule
of not auto-flipping paper_type (false-positive risk on real experiments).

Run:
    export DEEPSEEK_API_KEY=sk-...
    python3 classify_paper_type_deepseek.py            # 仅生成提案
    python3 classify_paper_type_deepseek.py --apply    # 把高置信非原始结果写回

--apply behavior (conservative):
    proposed=review/other & confidence=high  -> set paper_type
    proposed=review/other & confidence=medium-> keep paper_type, set
                                                validation_needs_review=True
    proposed=research / confidence=low       -> no change
"""
import argparse
import json
import os
import shutil
import time
from pathlib import Path

import pandas as pd
from openai import OpenAI

FILE = Path("recipes_master_v2.csv")
PAPERS_FILE = Path("papers.csv")
CHECKPOINT = Path("qa_outputs/paper_type_checkpoint.json")
PROPOSAL_OUT = Path("qa_outputs/paper_type_proposal.csv")

BASE_URL = "https://api.deepseek.com"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
SLEEP_SECONDS = float(os.environ.get("DEEPSEEK_SLEEP_SECONDS", "0.6"))

# Pre-filter: only spend API calls on rows whose title/evidence already look
# non-primary. Keeps cost down and focuses on the real risk.
SIGNAL = r"\breview\b|protocol|perspective|therapeutic target|hypothesis|commentary|editorial|guideline|consensus|meta-analysis"

VALID = {"research", "review", "other"}

SYSTEM_PROMPT = """\
You are a biomedical curator deciding whether a paper is a PRIMARY experimental
study that performed a cell reprogramming / conversion experiment, or not.

Return ONLY valid JSON:
{"paper_type": "research|review|other", "confidence": "high|medium|low", "rationale": "one sentence"}

Definitions:
- research: the authors performed original wet-lab reprogramming/conversion
  experiments in this paper (even if it also reviews background).
- review: a review, perspective, commentary, editorial, hypothesis, or
  consensus/guideline article that does not report original reprogramming data.
- other: protocol/methods-only papers, datasets/resources, or clearly not a
  reprogramming study.

Rules:
- Judge ONLY from the provided title + abstract. Do not use outside knowledge.
- A "protocol" paper that only describes how to do a method (no new biological
  result) is "other", not "research".
- If the abstract clearly reports experiments/results, prefer "research".
- If the abstract is missing or too vague to tell, use confidence "low".
"""


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {}


def save_checkpoint(done: dict):
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(done, ensure_ascii=False, indent=2))


def call_api(client, title, abstract) -> dict:
    user = f"TITLE: {title}\n\nABSTRACT: {abstract[:6000] or '(no abstract available)'}"
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        max_tokens=400,
    )
    raw = (resp.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Process at most N papers; 0 = all.")
    parser.add_argument("--apply", action="store_true",
                        help="Write high-confidence non-research results back to recipes_master_v2.csv.")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise SystemExit("请先: export DEEPSEEK_API_KEY=sk-...")

    df = pd.read_csv(FILE, dtype=str).fillna("")

    def unspec(v):
        t = str(v).strip().lower()
        return not t or t in {"not specified", "unknown", "not specified in text"}

    visible = (
        df["confidence"].isin(["high", "medium"])
        & (df["paper_type"] == "research")
        & (df["is_broad_duplicate"].str.lower() != "true")
        & (~df["factors"].apply(unspec))
        & (~df["single_tf_status"].isin(["cocktail_member", "unclear"]))
        & (df["validation_needs_review"].str.lower() != "true")
        & (~df["validation_action"].str.lower().isin(
            ["remove", "hide_incomplete_recipe", "hide_single_tf", "hide_model_adjudicated"]))
        & (df["validation_recipe_valid"].str.lower() != "no")
    )
    blob = df["title"].astype(str) + " " + df["evidence_sentence"].astype(str)
    candidates = df[visible & blob.str.contains(SIGNAL, case=False, na=False)]

    # paper_type is per-PMID; classify each unique PMID once.
    papers = pd.read_csv(PAPERS_FILE, dtype=str).fillna("")
    abshort = papers.set_index("pmid")[["title", "abstract"]].to_dict("index")

    pmids = list(dict.fromkeys(candidates["pmid"].tolist()))
    print(f"默认可见 review-like 候选: {len(candidates)} 行 / {len(pmids)} 篇 PMID")

    done = load_checkpoint()
    todo = [p for p in pmids if p not in done]
    if args.limit > 0:
        todo = todo[: args.limit]
    print(f"已判定: {len(done)} | 本次待判: {len(todo)}\n")

    if todo:
        client = OpenAI(api_key=api_key, base_url=BASE_URL)
        for i, pmid in enumerate(todo, 1):
            meta = abshort.get(pmid, {})
            title = meta.get("title", "") or candidates[candidates["pmid"] == pmid]["title"].iloc[0]
            abstract = meta.get("abstract", "")
            print(f"[{i}/{len(todo)}] PMID {pmid} {title[:55]} ... ", end="", flush=True)
            try:
                res = call_api(client, title, abstract)
                pt = str(res.get("paper_type", "")).strip().lower()
                if pt not in VALID:
                    pt = "research"
                    res["paper_type"] = pt
                done[pmid] = res
                save_checkpoint(done)
                print(f"{pt} [{res.get('confidence','')}]")
            except Exception as e:
                print(f"错误: {e}")
                time.sleep(3)
            time.sleep(SLEEP_SECONDS)

    # Build proposal table (one row per candidate PMID)
    rows = []
    for pmid in pmids:
        res = done.get(pmid, {})
        cur = candidates[candidates["pmid"] == pmid].iloc[0]
        rows.append({
            "pmid": pmid,
            "current_paper_type": "research",
            "proposed_paper_type": res.get("paper_type", ""),
            "model_confidence": res.get("confidence", ""),
            "rationale": res.get("rationale", ""),
            "title": cur["title"],
            "n_recipe_rows": int((df["pmid"] == pmid).sum()),
        })
    proposal = pd.DataFrame(rows)
    PROPOSAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    proposal.to_csv(PROPOSAL_OUT, index=False, encoding="utf-8")
    print(f"\n提案已写出: {PROPOSAL_OUT}")
    if len(proposal):
        print(proposal["proposed_paper_type"].value_counts().to_string())

    if not args.apply:
        print("\n(仅提案模式：未改动 recipes_master_v2.csv。确认提案后加 --apply 写回。)")
        return

    # --apply: conservative writeback. Only touch the current candidate set,
    # never the full checkpoint history (a stale PMID could otherwise be
    # re-flagged on a later run with different candidates).
    changed_pt = flagged = 0
    for pmid in pmids:
        res = done.get(pmid, {})
        pt = str(res.get("paper_type", "")).strip().lower()
        conf = str(res.get("confidence", "")).strip().lower()
        mask = df["pmid"] == pmid
        if pt in {"review", "other"} and conf == "high":
            df.loc[mask, "paper_type"] = pt
            df.loc[mask, "validation_notes"] = (df.loc[mask, "validation_notes"].astype(str)
                                                + " | paper_type→" + pt + " (deepseek high: "
                                                + str(res.get("rationale", ""))[:80] + ")").str.strip(" |")
            changed_pt += int(mask.sum())
        elif pt in {"review", "other"} and conf == "medium":
            df.loc[mask, "validation_needs_review"] = "True"
            flagged += int(mask.sum())

    backup = Path(str(FILE) + ".pre_papertype.bak")
    if not backup.exists():
        shutil.copy(FILE, backup)
    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"\n已写回: paper_type 改动 {changed_pt} 行 | 标 needs_review {flagged} 行 (备份 {backup})")
    print("提醒：写回后重跑 mark_duplicates.py / mark_broad_duplicates.py 保持一致。")


if __name__ == "__main__":
    main()
