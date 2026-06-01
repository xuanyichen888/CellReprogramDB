"""
用全文验证 single_tf_status=unclear 的条目。

对每个 unclear 单TF条目：
  - 如果该 PMID 在 fulltext.csv 里有全文 → 让模型判断该 TF 是否为独立配方
  - 如果没有全文 → 保持 unclear 不变

输出: 直接更新 recipes_master_v2.csv 中的 single_tf_status 字段
  standalone_valid  → 全文确认是独立转化
  cocktail_member   → 全文确认是 cocktail 成员之一
  unclear           → 全文也无法判断（保持原样）

运行前请先: export DEEPSEEK_API_KEY=sk-...
"""

import csv, json, os, time
import pandas as pd
from openai import OpenAI

API_KEY    = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL   = "https://api.deepseek.com"
MODEL      = "deepseek-v4-flash"
MASTER_CSV = "recipes_master_v2.csv"
FULLTEXT   = "fulltext.csv"
CHECKPOINT = "checkpoint_verify_single_tf.json"
SLEEP      = 1.2

SYSTEM_PROMPT = """\
You are a biomedical expert in cell reprogramming.

You will be given:
1. A reprogramming recipe entry with ONE transcription factor (TF)
2. The Methods and Results sections of the source paper

Your task: determine whether this single TF is used as a STANDALONE reprogramming factor
(sufficient by itself to convert the source cell to the target cell), or whether it is
only one member of a larger TF cocktail in this paper.

Respond ONLY with valid JSON — no markdown:
{
  "status": "standalone_valid" | "cocktail_member" | "unclear",
  "reasoning": "one sentence explanation"
}

Definitions:
- standalone_valid: The paper explicitly demonstrates that this single TF alone can
  convert source → target, with experimental evidence (e.g., efficiency data, marker expression).
- cocktail_member: The TF is tested as part of a multi-factor cocktail in this paper;
  it may be a key factor but is NOT used alone for the conversion.
- unclear: The full text still does not provide enough information to determine.
"""


def load_checkpoint() -> dict:
    if os.path.exists(CHECKPOINT):
        return json.load(open(CHECKPOINT))
    return {}


def save_checkpoint(done: dict):
    json.dump(done, open(CHECKPOINT, "w"), indent=2)


def call_api(client, pmid, source_cell, target_cell, factor, methods, results) -> dict:
    entry_desc = (
        f"PMID: {pmid}\n"
        f"Source cell: {source_cell}\n"
        f"Target cell: {target_cell}\n"
        f"Single TF: {factor}\n\n"
        f"[METHODS SECTION]\n{methods[:6000]}\n\n"
        f"[RESULTS SECTION]\n{results[:6000]}"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": entry_desc},
        ],
        temperature=0.0,
        max_tokens=1024,
    )
    raw = resp.choices[0].message.content.strip()

    # 调试：如果为空或解析失败，打印原始内容
    if not raw:
        finish = resp.choices[0].finish_reason
        raise json.JSONDecodeError(f"API返回空内容 finish_reason={finish}", "", 0)

    # 去掉 markdown 代码块
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 打印原始内容帮助调试
        print(f"\n[原始返回] {repr(raw[:200])}")
        raise


def main():
    if not API_KEY:
        raise SystemExit("请先运行: export DEEPSEEK_API_KEY=sk-...")

    # Load master data
    df = pd.read_csv(MASTER_CSV, dtype=str).fillna("")

    # Load fulltext
    ft_map = {}
    for row in csv.DictReader(open(FULLTEXT, encoding="utf-8")):
        pmid = row["pmid"]
        if row.get("methods_text", "").strip() or row.get("results_text", "").strip():
            ft_map[pmid] = {
                "methods": row.get("methods_text", ""),
                "results": row.get("results_text", ""),
            }

    # Select unclear single-TF entries that have fulltext
    mask = (df["single_tf_flag"] == "True") & (df["single_tf_status"] == "unclear")
    unclear_df = df[mask].copy()
    has_ft = unclear_df["pmid"].isin(ft_map)
    targets = unclear_df[has_ft]

    print(f"Single-TF unclear 条目: {len(unclear_df)} 条")
    print(f"  有全文: {len(targets)} 条 (来自 {targets['pmid'].nunique()} 篇文章)")
    print(f"  无全文: {len(unclear_df) - len(targets)} 条 (保持 unclear)")
    print()

    done = load_checkpoint()  # {index_str: {status, reasoning}}
    todo = [(idx, row) for idx, row in targets.iterrows() if str(idx) not in done]
    print(f"已完成: {len(done)} | 待处理: {len(todo)}\n")

    if not todo:
        print("全部完成，直接应用结果。")
    else:
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        ok = err = 0

        for i, (idx, row) in enumerate(todo, 1):
            pmid = row["pmid"]
            ft = ft_map[pmid]
            print(f"[{i}/{len(todo)}] idx={idx} PMID={pmid}  {row['factors']}  {row['source_cell']} → {row['target_cell'][:40]} ... ",
                  end="", flush=True)
            try:
                result = call_api(
                    client, pmid,
                    row["source_cell"], row["target_cell"], row["factors"],
                    ft["methods"], ft["results"],
                )
                status = result.get("status", "unclear")
                reasoning = result.get("reasoning", "")
                done[str(idx)] = {"status": status, "reasoning": reasoning}
                save_checkpoint(done)
                print(f"{status}  ({reasoning[:60]})")
                ok += 1
            except json.JSONDecodeError as e:
                print(f"JSON错误: {e}")
                err += 1
                done[str(idx)] = {"status": "unclear", "reasoning": "parse error"}
                save_checkpoint(done)
            except Exception as e:
                print(f"API错误: {e}")
                err += 1
                time.sleep(5)
                continue
            time.sleep(SLEEP)

        print(f"\n完成: {ok} 成功 | {err} 错误")

    # Apply results back to dataframe
    updated = 0
    for idx_str, res in done.items():
        idx = int(idx_str)
        new_status = res["status"]
        if df.at[idx, "single_tf_status"] != new_status:
            df.at[idx, "single_tf_status"] = new_status
            updated += 1

    import shutil
    shutil.copy(MASTER_CSV, MASTER_CSV + ".bak")
    df.to_csv(MASTER_CSV, index=False, encoding="utf-8")
    print(f"\n状态更新: {updated} 条")
    print(f"结果已写入 {MASTER_CSV}")

    # Summary
    new_standalone = sum(1 for v in done.values() if v["status"] == "standalone_valid")
    new_cocktail   = sum(1 for v in done.values() if v["status"] == "cocktail_member")
    still_unclear  = sum(1 for v in done.values() if v["status"] == "unclear")
    print(f"\n验证结果汇总:")
    print(f"  standalone_valid: {new_standalone}")
    print(f"  cocktail_member:  {new_cocktail}")
    print(f"  仍不确定:         {still_unclear}")


if __name__ == "__main__":
    main()
