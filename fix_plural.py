"""
统一 target_cell / source_cell 的单复数变体：
- 去掉末尾 's'（如果单数版本也存在于数据中）
- 处理 'cells' → 'cell' 等复合情况
"""
import pandas as pd, shutil, re

FILE = "recipes_master_v2.csv"


def singularize(series: pd.Series) -> pd.Series:
    vals = set(series.unique())
    mapping = {}
    for name in vals:
        if not name:
            continue
        # Try stripping trailing 's'
        candidate = name.rstrip('s') if name.endswith('s') else None
        if candidate and candidate != name and candidate in vals:
            mapping[name] = candidate
        # Special: trailing 'es' → no 'e' at end (e.g. "processes"→ not needed here)
        # Also handle case variations like CiPSC vs ciPSC
        lower_map = {v.lower(): v for v in vals}
        name_lower = name.lower()
        if name_lower in lower_map and lower_map[name_lower] != name:
            # keep the shorter/simpler one (prefer no parens, prefer lowercase standard)
            canonical = lower_map[name_lower]
            if len(canonical) <= len(name):
                mapping[name] = canonical

    return series.map(lambda x: mapping.get(x, x))


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")

    before_t = df["target_cell"].nunique()
    before_s = df["source_cell"].nunique()

    df["target_cell"] = singularize(df["target_cell"])
    df["source_cell"]  = singularize(df["source_cell"])

    after_t = df["target_cell"].nunique()
    after_s = df["source_cell"].nunique()

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")

    print(f"target_cell: {before_t} → {after_t} 种（-{before_t-after_t}）")
    print(f"source_cell: {before_s} → {after_s} 种（-{before_s-after_s}）")
    print(f"保存至 {FILE}")


if __name__ == "__main__":
    main()
