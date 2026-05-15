"""
标准化 recipes_master_v2.csv 中的 source_cell / target_cell 名称
原地更新文件
"""
import pandas as pd, re, shutil

FILE = "recipes_master_v2.csv"

# (正则模式, 标准名称)
# 规则顺序重要：更具体的放前面
RULES = [
    # ── iPSC ──────────────────────────────────────────────────────────────────
    (r'h?i?ps[c]?s?\b',
     'induced pluripotent stem cell (iPSC)'),
    (r'induced pluripotent stem cells?(?:\s*\([^)]+\))?',
     'induced pluripotent stem cell (iPSC)'),
    (r'human induced pluripotent stem cells?(?:\s*\([^)]+\))?',
     'induced pluripotent stem cell (iPSC)'),
    (r'pluripotent stem cells?',
     'induced pluripotent stem cell (iPSC)'),

    # ── 心肌细胞 ───────────────────────────────────────────────────────────────
    (r'induced cardiomyocytes?(?:-like cells?)?(?:\s*\([^)]+\))?',
     'cardiomyocyte'),
    (r'cardiomyocyte-like cells?',
     'cardiomyocyte'),
    (r'cardiomyocytes\b',
     'cardiomyocyte'),

    # ── 肝细胞 ─────────────────────────────────────────────────────────────────
    (r'hepatocyte-like cells?(?:\s*\([^)]+\))?',
     'hepatocyte-like cell'),
    (r'ihep\b',
     'hepatocyte-like cell'),

    # ── 神经元（保留细分：dopaminergic/motor/GABAergic 等） ───────────────────
    (r'induced neurons?\b(?!\s*\()',
     'induced neuron (iN)'),
    (r'neuronal cells?',
     'induced neuron (iN)'),
    (r'neuron-like cells?',
     'induced neuron (iN)'),
    (r'functional neurons?',
     'induced neuron (iN)'),
    (r'induced neuronal cells?',
     'induced neuron (iN)'),
    (r'induced neurons\b',
     'induced neuron (iN)'),

    # ── β细胞 ──────────────────────────────────────────────────────────────────
    (r'insulin-producing cells?',
     'insulin-producing cell (β-cell)'),
    (r'pancreatic [βb][\s\-]?cells?',
     'insulin-producing cell (β-cell)'),
    (r'beta[\s\-]?cells?',
     'insulin-producing cell (β-cell)'),

    # ── 巨噬细胞 ───────────────────────────────────────────────────────────────
    (r'macrophage-like cells?',
     'macrophage'),
    (r'monocyte-derived macrophages?',
     'macrophage'),

    # ── 毛细胞 ─────────────────────────────────────────────────────────────────
    (r'hair cells\b',
     'hair cell'),
]

# 编译为 (compiled_pattern, replacement) 列表
COMPILED = [(re.compile(r'(?i)^' + p + r'$'), r) for p, r in RULES]

def standardize(name: str) -> str:
    name = name.strip()
    for pat, replacement in COMPILED:
        if pat.match(name):
            return replacement
    return name


def main():
    df = pd.read_csv(FILE, dtype=str).fillna('')

    before_t = df['target_cell'].nunique()
    before_s = df['source_cell'].nunique()

    df['target_cell'] = df['target_cell'].apply(standardize)
    df['source_cell'] = df['source_cell'].apply(standardize)

    after_t = df['target_cell'].nunique()
    after_s = df['source_cell'].nunique()

    # 备份
    shutil.copy(FILE, FILE + '.bak')
    df.to_csv(FILE, index=False, encoding='utf-8')

    print(f'target_cell: {before_t} → {after_t} 种（减少 {before_t-after_t}）')
    print(f'source_cell: {before_s} → {after_s} 种（减少 {before_s-after_s}）')
    print(f'保存至 {FILE}（备份：{FILE}.bak）')

    print('\nTop 15 target cell types:')
    print(df['target_cell'].value_counts().head(15).to_string())

if __name__ == '__main__':
    main()
