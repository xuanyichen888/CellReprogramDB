"""
标记单因子 TF recipe（可能是不完整的 recipe）：
- factors 只含 1 个 TF（factor_type 全为 TF 且 factors 只有 1 个）
- 新增列 single_tf_flag = True/False
- 这类条目在 app 里默认过滤，提示用户可能是完整 recipe 的一部分
"""
import pandas as pd, shutil

FILE = "recipes_master_v2.csv"


def is_single_tf(row) -> bool:
    factors = [f.strip() for f in row["factors"].split(",")
               if f.strip() and f.strip() not in ("not specified", "")]
    ft_list = [f.strip() for f in row["factor_type"].split(",") if f.strip()]
    if len(factors) != 1:
        return False
    # factor_type 是 TF（或只含 TF 标签）
    return all("TF" in ft for ft in ft_list) if ft_list else False


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")

    df["single_tf_flag"] = df.apply(is_single_tf, axis=1)
    n = df["single_tf_flag"].sum()

    print(f"标记 single_tf_flag=True: {n} 条")
    print(f"  confidence 分布:")
    print(df[df["single_tf_flag"]]["confidence"].value_counts().to_string())
    print(f"\n  常见 factors:")
    print(df[df["single_tf_flag"]]["factors"].value_counts().head(15).to_string())

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")
    print(f"\n保存至 {FILE}")


if __name__ == "__main__":
    main()
