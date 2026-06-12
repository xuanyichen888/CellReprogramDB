"""
用全文补全 factors 缺失的条目（research + high/medium confidence）。

对每条缺失 factors 的记录，找到对应 PMID 的全文，
让模型从 Methods/Results 里提取该 source→target 转化所用的具体因子。

运行前: export DEEPSEEK_API_KEY=sk-...
"""

import csv, json, os, re, time, shutil
import pandas as pd
from openai import OpenAI

API_KEY    = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL   = "https://api.deepseek.com"
MODEL      = "deepseek-v4-flash"
MASTER_CSV = "recipes_master_v2.csv"
FULLTEXT   = "fulltext.csv"
CHECKPOINT = "checkpoint_fill_factors.json"
SLEEP      = 1.0

SYSTEM_PROMPT = """\
You are a biomedical expert in cell reprogramming.

You will be given:
1. A reprogramming recipe with MISSING factors (source cell → target cell)
2. The Methods and Results sections of the source paper

Your task: find the specific factors (transcription factors, small molecules, miRNAs, etc.)
used to convert the source cell into the target cell in THIS paper.

Respond ONLY with valid JSON — no markdown:
{
  "found": true/false,
  "factors": "factor1, factor2, factor3",
  "factor_type": "TF, TF, small_molecule",
  "confidence": "high|medium|low",
  "reasoning": "one sentence"
}

Rules:
- found=false if the full text genuinely does not specify which factors were used
- factors: comma-separated, exact names as written in the paper
- factor_type: one label per factor in the same order — TF | small_molecule | miRNA | knockdown | cytokine | other
- confidence: high = factors explicitly listed with experimental evidence; medium = mentioned but partial
- Be specific: "OCT4, SOX2, KLF4, MYC" not "Yamanaka factors"
- If multiple factor combinations are tested, give the main/final combination
"""


def load_checkpoint() -> dict:
    if os.path.exists(CHECKPOINT):
        return json.load(open(CHECKPOINT))
    return {}


def save_checkpoint(done: dict):
    json.dump(done, open(CHECKPOINT, "w"), indent=2)


def call_api(client, pmid, source_cell, target_cell, methods, results) -> dict:
    user_content = (
        f"PMID: {pmid}\n"
        f"Source cell: {source_cell}\n"
        f"Target cell: {target_cell}\n"
        f"(factors are currently listed as 'not specified' — please find them)\n\n"
        f"[METHODS SECTION]\n{methods[:7000]}\n\n"
        f"[RESULTS SECTION]\n{results[:5000]}"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        temperature=0.0,
        max_tokens=4096,
    )
    raw = resp.choices[0].message.content.strip()
    if not raw:
        raise json.JSONDecodeError(f"empty response, finish_reason={resp.choices[0].finish_reason}", "", 0)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Salvage the first {...} object from a possibly truncated/noisy response
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise


def main():
    if not API_KEY:
        raise SystemExit("请先运行: export DEEPSEEK_API_KEY=sk-...")

    df = pd.read_csv(MASTER_CSV, dtype=str).fillna("")

    # Load fulltext
    ft_map = {}
    for row in csv.DictReader(open(FULLTEXT, encoding="utf-8")):
        if row.get("methods_text", "").strip() or row.get("results_text", "").strip():
            ft_map[row["pmid"]] = {
                "methods": row.get("methods_text", ""),
                "results": row.get("results_text", ""),
            }

    def factors_unspec(v):
        t = str(v).strip().lower()
        return not t or t in {"not specified", "unknown", "not specified in text"}

    # Target rows: missing factors + research + high/medium + have fulltext
    mask = (
        df["factors"].apply(factors_unspec) &
        (df["paper_type"] == "research") &
        (df["confidence"].isin(["high", "medium"])) &
        (df["pmid"].isin(ft_map))
    )
    targets = df[mask].copy()

    print(f"需要补全 factors 的条目: {len(targets)} 条 (来自 {targets['pmid'].nunique()} 篇文章)")

    done = load_checkpoint()
    todo = [(idx, row) for idx, row in targets.iterrows() if str(idx) not in done]
    print(f"已完成: {len(done)} | 待处理: {len(todo)}\n")

    if not todo:
        print("全部已处理，直接应用结果。")
    else:
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        found_count = not_found = err = 0

        for i, (idx, row) in enumerate(todo, 1):
            pmid = row["pmid"]
            ft = ft_map[pmid]
            print(f"[{i}/{len(todo)}] idx={idx} PMID={pmid}  {row['source_cell'][:30]} → {row['target_cell'][:35]} ... ",
                  end="", flush=True)
            try:
                result = call_api(
                    client, pmid,
                    row["source_cell"], row["target_cell"],
                    ft["methods"], ft["results"],
                )
                done[str(idx)] = result
                save_checkpoint(done)

                if result.get("found") and result.get("factors", "").strip():
                    print(f"✓ {result['factors'][:60]}  [{result.get('confidence','')}]")
                    found_count += 1
                else:
                    print(f"not found  ({result.get('reasoning','')[:60]})")
                    not_found += 1
            except json.JSONDecodeError as e:
                print(f"JSON错误: {e}")
                done[str(idx)] = {"found": False, "error": str(e)}
                save_checkpoint(done)
                err += 1
            except Exception as e:
                print(f"API错误: {e}")
                err += 1
                time.sleep(5)
                continue
            time.sleep(SLEEP)

        print(f"\n完成: {found_count} 找到 | {not_found} 未找到 | {err} 错误")

    # Apply: update factors, factor_type, confidence where found=True
    updated = 0
    for idx_str, res in done.items():
        if not res.get("found"):
            continue
        factors = res.get("factors", "").strip()
        if not factors:
            continue
        idx = int(idx_str)
        df.at[idx, "factors"] = factors
        if res.get("factor_type"):
            df.at[idx, "factor_type"] = res["factor_type"]
        # 如果模型给了更低置信度，降级
        if res.get("confidence") == "low" and df.at[idx, "confidence"] == "medium":
            df.at[idx, "confidence"] = "low"
        updated += 1

    shutil.copy(MASTER_CSV, MASTER_CSV + ".bak")
    df.to_csv(MASTER_CSV, index=False, encoding="utf-8")
    print(f"\n已更新 {updated} 条 factors → {MASTER_CSV}")

    # Summary
    total = len(done)
    found_n = sum(1 for v in done.values() if v.get("found") and v.get("factors","").strip())
    print(f"补全率: {found_n}/{total} ({found_n/total*100:.0f}%)" if total else "")


if __name__ == "__main__":
    main()
