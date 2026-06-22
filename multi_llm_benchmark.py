"""
Multi-LLM extraction benchmark for CellReprogramDB.

Runs the SAME recipe-extraction prompt through several LLMs (DeepSeek, OpenAI,
Anthropic) on a small sample of papers, then compares their outputs and reports
inter-model agreement. Disagreement = an uncertainty signal; high agreement on a
field is a cheap proxy for reliability.

SECURITY: all API keys are read from environment variables. No keys are stored
in this file or written to any output.
    export DEEPSEEK_API_KEY=sk-...
    export OPENAI_API_KEY=sk-...
    export ANTHROPIC_API_KEY=sk-...

Usage:
    python3 multi_llm_benchmark.py --input papers.csv --limit 30 --dry-run
    python3 multi_llm_benchmark.py --input papers.csv --limit 30 \
        --output outputs/multi_llm_benchmark_30.csv

Model IDs are overridable:
    BENCH_DEEPSEEK_MODEL (default deepseek-chat)
    BENCH_OPENAI_MODEL   (default gpt-4o-mini)
    BENCH_ANTHROPIC_MODEL(default claude-sonnet-4-6)
"""
import argparse, json, os, re, time
import pandas as pd

DEEPSEEK_MODEL = os.environ.get("BENCH_DEEPSEEK_MODEL", "deepseek-chat")
OPENAI_MODEL = os.environ.get("BENCH_OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_MODEL = os.environ.get("BENCH_ANTHROPIC_MODEL", "claude-sonnet-4-6")

SYSTEM_PROMPT = """\
You extract a cell-reprogramming recipe from a paper's title + abstract.

Return ONLY valid JSON:
{"is_recipe": true/false, "source_cell": "...", "target_cell": "...", "factors": "f1, f2, ..."}

- is_recipe=false if this paper does not report a successful source->target cell
  conversion with defined factors (e.g. it is a review, method-only, or unrelated).
- factors: comma-separated names exactly as written; "" if none stated.
- Be concise and literal; do not infer beyond the abstract.
"""


def _norm(s):
    s = str(s or "").strip().lower()
    s = s.replace("β", "beta").replace("α", "alpha")
    s = re.sub(r"[-_/]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _factor_set(s):
    parts = [re.sub(r"[^a-z0-9]+", "", p) for p in _norm(s).split(",")]
    parts = [p for p in (x.strip() for x in re.split(r"[,; ]+", _norm(s))) if p]
    # token set, alias a couple common ones
    alias = {"oct34": "oct4", "pou5f1": "oct4", "cmyc": "myc", "oct3": "oct4"}
    return frozenset(alias.get(p, p) for p in parts if p)


def _parse_json(raw):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group()) if m else {}


def build_callers():
    """Return {name: callable(title, abstract)->dict} for each model whose key is set."""
    callers = {}
    if os.environ.get("DEEPSEEK_API_KEY"):
        from openai import OpenAI
        ds = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
        def call_ds(title, abstract):
            r = ds.chat.completions.create(model=DEEPSEEK_MODEL, temperature=0.0, max_tokens=400,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": f"TITLE: {title}\n\nABSTRACT: {abstract[:6000]}"}])
            return _parse_json(r.choices[0].message.content)
        callers[f"deepseek:{DEEPSEEK_MODEL}"] = call_ds
    if os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI
        oa = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        def call_oa(title, abstract):
            r = oa.chat.completions.create(model=OPENAI_MODEL, temperature=0.0, max_tokens=400,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": f"TITLE: {title}\n\nABSTRACT: {abstract[:6000]}"}])
            return _parse_json(r.choices[0].message.content)
        callers[f"openai:{OPENAI_MODEL}"] = call_oa
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        an = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        def call_an(title, abstract):
            r = an.messages.create(model=ANTHROPIC_MODEL, max_tokens=400,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"TITLE: {title}\n\nABSTRACT: {abstract[:6000]}"}])
            return _parse_json(r.content[0].text)
        callers[f"anthropic:{ANTHROPIC_MODEL}"] = call_an
    return callers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="papers.csv")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--sample", action="store_true", help="Random sample instead of first N.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output", default="outputs/multi_llm_benchmark.csv")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    df = pd.read_csv(args.input, dtype=str).fillna("")
    if "abstract" not in df.columns:
        raise SystemExit(f"{args.input} has no 'abstract' column.")
    sub = df.sample(args.limit, random_state=args.seed) if args.sample else df.head(args.limit)

    callers = build_callers()
    print(f"样本: {len(sub)} 篇 | 模型: {list(callers.keys()) or '(无——没设任何 key)'}")
    if args.dry_run:
        print("Dry-run：不调用 API、不写文件。设好 3 个 env key 后去掉 --dry-run 即可。")
        return
    if not callers:
        raise SystemExit("没有可用模型。请 export DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY。")

    rows = []
    for n, (_, p) in enumerate(sub.iterrows(), 1):
        rec = {"pmid": p["pmid"], "title": p["title"][:90]}
        per_model = {}
        for name, fn in callers.items():
            try:
                res = fn(p["title"], p["abstract"])
            except Exception as e:
                res = {"error": str(e)[:80]}
            per_model[name] = res
            rec[f"{name} | is_recipe"] = res.get("is_recipe", "")
            rec[f"{name} | source"] = res.get("source_cell", res.get("error", ""))
            rec[f"{name} | target"] = res.get("target_cell", "")
            rec[f"{name} | factors"] = res.get("factors", "")
        # agreement (only over models that returned a recipe)
        recs = [m for m in per_model.values() if m.get("is_recipe") is True]
        def agree(field, norm):
            vals = {norm(m.get(field, "")) for m in recs if m.get(field)}
            return "yes" if len(vals) == 1 and recs else ("n/a" if len(recs) < 2 else "no")
        rec["is_recipe_agree"] = "yes" if len({str(m.get("is_recipe")) for m in per_model.values() if "error" not in m}) == 1 else "no"
        rec["source_agree"] = agree("source_cell", _norm)
        rec["target_agree"] = agree("target_cell", _norm)
        rec["factors_agree"] = "yes" if recs and len({_factor_set(m.get("factors", "")) for m in recs}) == 1 else ("n/a" if len(recs) < 2 else "no")
        rows.append(rec)
        print(f"[{n}/{len(sub)}] {p['pmid']} | recipe_agree={rec['is_recipe_agree']} src={rec['source_agree']} tgt={rec['target_agree']} fac={rec['factors_agree']}")
        time.sleep(0.3)

    out = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    out.to_csv(args.output, index=False, encoding="utf-8")
    print(f"\n写出 {len(out)} 行 -> {args.output}")
    # summary
    for col in ["is_recipe_agree", "source_agree", "target_agree", "factors_agree"]:
        vc = out[col].value_counts().to_dict()
        print(f"  {col}: {vc}")


if __name__ == "__main__":
    main()
