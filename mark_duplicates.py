"""
对 recipes_master_v2.csv 中相同 (source_cell, target_cell, factors) 的重复条目进行处理：
- 每组保留最早发表的 research 论文（year最小且paper_type=research），其余标记 is_duplicate=True
- 若组内无 research，则保留最早的那条
- 新增列 is_duplicate (True/False)
"""
import pandas as pd, shutil

FILE = "recipes_master_v2.csv"
KEY  = ["source_cell", "target_cell", "factors"]


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")
    df["year_int"] = pd.to_numeric(df["year"], errors="coerce").fillna(9999).astype(int)

    df["is_duplicate"] = False

    # 找出所有重复组
    dup_mask = df.duplicated(subset=KEY, keep=False)
    dup_df   = df[dup_mask].copy()

    n_groups = 0
    n_marked = 0

    for _, group in dup_df.groupby(KEY, sort=False):
        if len(group) < 2:
            continue
        n_groups += 1

        # 优先保留 research 中年份最小的
        research = group[group["paper_type"] == "research"]
        if len(research) > 0:
            keep_idx = research["year_int"].idxmin()
        else:
            keep_idx = group["year_int"].idxmin()

        dup_idx = group.index[group.index != keep_idx]
        df.loc[dup_idx, "is_duplicate"] = True
        n_marked += len(dup_idx)

    df = df.drop(columns=["year_int"])

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")

    total_dupes = df["is_duplicate"].sum()
    print(f"重复组数: {n_groups}")
    print(f"标记为 is_duplicate=True: {total_dupes} 条")
    print(f"保留的唯一 recipe: {(~df['is_duplicate']).sum()} 条")
    print(f"\n重复条目的 paper_type 分布:")
    print(df[df["is_duplicate"]]["paper_type"].value_counts().to_string())
    print(f"\n保存至 {FILE}")


if __name__ == "__main__":
    main()
