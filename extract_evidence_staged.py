"""
为 coverage-gap fetch 暂存的新 recipe 补 evidence_sentence。

安全边界（与 extract_evidence.py 不同，后者读旧 recipes_master.csv 会覆盖主表）：
- 直接读写当前 recipes_master_v2.csv
- 只处理 validation_notes 含 'coverage-gap fetch' 且 evidence 为空的行
- 只回写 evidence_sentence 这一列，不动其它任何列/行
- 不清 needs_review（放出由人工/后续步骤单独决定）
- checkpoint 可断点续；写前自动 .bak 备份

运行: export DEEPSEEK_API_KEY=sk-...; python3 extract_evidence_staged.py
"""
import argparse, csv, json, os, re, time, shutil
import pandas as pd
from openai import OpenAI

RELEASE_TAG = "evidence_staged_release_2026-06-13"

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"
FILE = "recipes_master_v2.csv"
CHECKPOINT = "checkpoint_evidence_staged.json"
SLEEP = 0.6

SYSTEM_PROMPT = """\
You are a biomedical text mining expert specializing in cell reprogramming literature.

Given a reprogramming recipe (source cell -> target cell, using specific factors) and the original text,
find the single best sentence that directly demonstrates the conversion was SUCCESSFULLY achieved in THIS paper.

Respond ONLY with valid JSON:
{"evidence_sentence": "...", "quality": "good|weak|none"}

REQUIRED -- the chosen sentence MUST describe a RESULT or OUTCOME:
  - Generated / produced / obtained / derived target cell type from source
  - Source cell was converted / reprogrammed / transdifferentiated into target
  - target-cell colonies appeared / were observed / confirmed; efficiency of X% achieved
  - Cells acquired / expressed markers of the target cell type
  - "Here we report/describe the generation of X from Y" is acceptable

FORBIDDEN:
  - Prior-work citations ("We previously showed...", "It has been shown that...")
  - Setup-only sentences ("Cells were transduced with...", "To test whether...")
  - Failed/negative results

Rules:
- Copy the sentence EXACTLY word-for-word from the text; do NOT paraphrase.
- Prefer the sentence naming source cell, target cell, AND factors together.
- quality="good" clear outcome; "weak" partial/indirect; "none" no suitable sentence (return "").
"""


def load_cp():
    return json.load(open(CHECKPOINT)) if os.path.exists(CHECKPOINT) else {}


def save_cp(d):
    json.dump(d, open(CHECKPOINT, "w"), ensure_ascii=False)


def call_api(client, recipe, text):
    user = (
        f"RECIPE: {recipe['source_cell']} -> {recipe['target_cell']} "
        f"using {recipe['factors']}\n\nTEXT:\n{text[:7000]}"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": user}],
        temperature=0.0, max_tokens=2048,
    )
    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        raise json.JSONDecodeError(f"empty, finish={resp.choices[0].finish_reason}", "", 0)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise


def is_unspec(v):
    t = str(v).strip().lower()
    return not t or t in {"not specified", "unknown", "not specified in text"}


def release_qualified(df):
    """Clear needs_review for staged coverage-gap rows that are fully curated.

    Criteria (a row only enters the default view if ALL hold):
      research paper · factors present · evidence present · not a single-TF
      entry pending classification · species present · conversion_scope != unclear
    Held-back rows keep needs_review=True with a reason note.
    """
    staged = df["validation_notes"].str.contains("coverage-gap fetch", na=False) \
             & (df["validation_needs_review"].str.lower() == "true")
    blank = lambda v: str(v).strip() == ""
    qualified = staged \
        & (df["paper_type"] == "research") \
        & (~df["factors"].apply(is_unspec)) \
        & (df["evidence_sentence"].str.strip() != "") \
        & (df["single_tf_flag"] != "True") \
        & (~df["species"].apply(blank)) \
        & (df["conversion_scope"] != "unclear")
    df.loc[qualified, "validation_needs_review"] = "False"
    df.loc[qualified, "validation_resolution"] = "curated_coverage_expansion"
    df.loc[qualified, "validation_notes"] = (
        df.loc[qualified, "validation_notes"].str.rstrip(" |") + " | " + RELEASE_TAG
    )
    return int(qualified.sum()), int((staged & ~qualified).sum())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-qualified", action="store_true",
                        help="After evidence is filled, clear needs_review for fully-curated staged rows.")
    args = parser.parse_args()

    df = pd.read_csv(FILE, dtype=str).fillna("")
    abstracts = {p["pmid"]: p["abstract"] for p in csv.DictReader(open("papers.csv", encoding="utf-8"))}
    ft = {}
    if os.path.exists("fulltext.csv"):
        for r in csv.DictReader(open("fulltext.csv", encoding="utf-8")):
            ft[r["pmid"]] = (r.get("methods_text", "") + " " + r.get("results_text", "")).strip()

    staged = df[df["validation_notes"].str.contains("coverage-gap fetch", na=False)
                & (df["evidence_sentence"].str.strip() == "")]
    print(f"待补 evidence 的 staged 行: {len(staged)}")

    done = load_cp()
    todo = [(i, r) for i, r in staged.iterrows() if str(i) not in done]
    print(f"已处理: {len(done)} | 待处理: {len(todo)}\n")

    if todo:
        if not API_KEY:
            raise SystemExit("请先: export DEEPSEEK_API_KEY=sk-...")
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        good = weak = none = err = 0
        for n, (i, r) in enumerate(todo, 1):
            pmid = r["pmid"]
            text = ft.get(pmid) or abstracts.get(pmid, "")
            print(f"[{n}/{len(todo)}] idx={i} PMID={pmid} {r['source_cell'][:18]}->{r['target_cell'][:18]} ... ", end="", flush=True)
            if not text.strip():
                done[str(i)] = {"evidence_sentence": "", "quality": "none"}
                save_cp(done); none += 1; print("无原文"); continue
            try:
                res = call_api(client, r, text)
                q = res.get("quality", "none")
                done[str(i)] = {"evidence_sentence": res.get("evidence_sentence", ""), "quality": q}
                save_cp(done)
                print(f"{q}")
                good += q == "good"; weak += q == "weak"; none += q == "none"
            except Exception as e:
                print(f"错误: {e}"); err += 1; time.sleep(3)
            time.sleep(SLEEP)
        print(f"\ngood {good} | weak {weak} | none {none} | err {err}")

    # 回写 evidence_sentence（只动 staged 行）
    shutil.copy(FILE, FILE + ".bak")
    n_written = 0
    for idx_str, res in done.items():
        i = int(idx_str)
        ev = res.get("evidence_sentence", "").strip()
        if i in df.index and ev:
            df.at[i, "evidence_sentence"] = ev
            n_written += 1
    if args.release_qualified:
        released, held = release_qualified(df)
        print(f"\n--release-qualified: 放出 {released} 条 (清 needs_review, 标 {RELEASE_TAG}) | 留 staged {held} 条")

    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"\n回写 evidence_sentence: {n_written} 条 -> {FILE}")
    if not args.release_qualified:
        print("（needs_review 未清；加 --release-qualified 放出已完全 curation 的行）")


if __name__ == "__main__":
    main()
