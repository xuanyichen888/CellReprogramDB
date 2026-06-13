"""
为 coverage-gap staged 的单TF行分类 single_tf_status（摘要级 DeepSeek）。

安全边界（与 classify_single_tf.py 不同，后者对所有单TF行规则重算、会 clobber
已有的全文验证/人工分类）：
- 只处理 validation_notes 含 'coverage-gap fetch'、single_tf_flag=True、
  且 single_tf_status 为空的行
- 只回写这些行的 single_tf_status，绝不动其它任何行
- checkpoint 可断点续；写前 .bak 备份

判据同 verify_single_tf_fulltext：单个 TF 是否"单独即可驱动转化"(standalone_valid)
还是只是组合里的一个(cocktail_member)，摘要不足判断则 unclear。

运行: export DEEPSEEK_API_KEY=sk-...; python3 classify_staged_single_tf.py
"""
import csv, json, os, re, time, shutil
import pandas as pd
from openai import OpenAI

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com"
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
FILE = "recipes_master_v2.csv"
CHECKPOINT = "checkpoint_classify_staged_single_tf.json"
SLEEP = 0.6

SYSTEM_PROMPT = """\
You are a biomedical expert in cell reprogramming.

Given a reprogramming recipe whose factor list is a SINGLE transcription factor,
and the paper abstract, decide whether that single TF is reported as
sufficient ON ITS OWN to drive the source->target conversion, or whether it is
really one member of a larger factor cocktail.

Respond ONLY with valid JSON:
{"status": "standalone_valid" | "cocktail_member" | "unclear", "reasoning": "one sentence"}

- standalone_valid: the abstract indicates this single TF alone achieves the conversion.
- cocktail_member: the abstract indicates the conversion used additional factors
  (the single-TF entry is incomplete / part of a combination).
- unclear: the abstract does not give enough information to tell.
Judge ONLY from the abstract; do not use outside knowledge.
"""


def load_cp():
    return json.load(open(CHECKPOINT)) if os.path.exists(CHECKPOINT) else {}


def save_cp(d):
    json.dump(d, open(CHECKPOINT, "w"), ensure_ascii=False)


def call_api(client, r, abstract):
    user = (f"Single TF: {r['factors']}\nSource: {r['source_cell']}\n"
            f"Target: {r['target_cell']}\n\nABSTRACT:\n{abstract[:6000]}")
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": user}],
        temperature=0.0, max_tokens=1024,
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


def main():
    if not API_KEY:
        raise SystemExit("请先: export DEEPSEEK_API_KEY=sk-...")

    df = pd.read_csv(FILE, dtype=str).fillna("")
    abstracts = {p["pmid"]: p["abstract"] for p in csv.DictReader(open("papers.csv", encoding="utf-8"))}

    target = df[df["validation_notes"].str.contains("coverage-gap fetch", na=False)
                & (df["single_tf_flag"] == "True")
                & (df["single_tf_status"].str.strip() == "")]
    print(f"待分类 staged 单TF: {len(target)}")

    done = load_cp()
    todo = [(i, r) for i, r in target.iterrows() if str(i) not in done]
    print(f"已处理: {len(done)} | 待处理: {len(todo)}\n")

    if todo:
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        for n, (i, r) in enumerate(todo, 1):
            ab = abstracts.get(r["pmid"], "")
            print(f"[{n}/{len(todo)}] idx={i} PMID={r['pmid']} {r['factors'][:14]} {r['source_cell'][:16]}->{r['target_cell'][:16]} ... ",
                  end="", flush=True)
            if not ab.strip():
                done[str(i)] = {"status": "unclear", "reasoning": "no abstract"}
                save_cp(done); print("无摘要->unclear"); continue
            try:
                res = call_api(client, r, ab)
                st = res.get("status", "unclear")
                if st not in {"standalone_valid", "cocktail_member", "unclear"}:
                    st = "unclear"
                done[str(i)] = {"status": st, "reasoning": res.get("reasoning", "")}
                save_cp(done); print(st)
            except Exception as e:
                print(f"错误: {e}"); time.sleep(3)
            time.sleep(SLEEP)

    # 回写 single_tf_status（只动目标行）
    shutil.copy(FILE, FILE + ".bak")
    n = 0
    from collections import Counter
    dist = Counter()
    for idx_str, res in done.items():
        i = int(idx_str)
        st = res.get("status", "")
        if i in df.index and st:
            df.at[i, "single_tf_status"] = st
            dist[st] += 1
            n += 1
    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"\n回写 single_tf_status: {n} 条 -> {FILE}")
    print("分布:", dict(dist))
    print("（standalone_valid 的行后续可按 release-qualified 标准放出）")


if __name__ == "__main__":
    main()
