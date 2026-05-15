"""
标准化 recipes_master_v2.csv 中的 source_cell 名称
策略：先合并明显同义变体，保留有意义的细分（cardiac fibroblast ≠ fibroblast）
"""
import pandas as pd, re, shutil

FILE = "recipes_master_v2.csv"

# (正则, 标准名)  — 匹配整个字段（忽略大小写，去首尾空格后）
# 更具体的规则放前面
RULES = [
    # ── iPSC / pluripotent ────────────────────────────────────────────────
    (r'h?i?ps[c]?s?\b',
     'induced pluripotent stem cell (iPSC)'),
    (r'induced pluripotent stem cells?(?:\s*\([^)]*\))?',
     'induced pluripotent stem cell (iPSC)'),
    (r'human i(?:nduced )?ps[c]?s?(?:\s*\([^)]*\))?',
     'induced pluripotent stem cell (iPSC)'),
    (r'mouse i(?:nduced )?ps[c]?s?(?:\s*\([^)]*\))?',
     'induced pluripotent stem cell (iPSC)'),
    (r'human pluripotent stem cells?(?:\s*\([^)]*\))?',
     'human pluripotent stem cell (hPSC)'),
    (r'h(?:uman )?ps[c]?s?(?:\s*\([^)]*\))?',
     'human pluripotent stem cell (hPSC)'),

    # ── MEF / mouse embryonic fibroblast ──────────────────────────────────
    (r'mouse embryonic fibroblasts?(?:\s*\([^)]*\))?',
     'mouse embryonic fibroblast (MEF)'),
    (r'MEFs?\b',
     'mouse embryonic fibroblast (MEF)'),

    # ── Cardiac fibroblast (多物种变体合并) ───────────────────────────────
    (r'(?:human |mouse |neonatal |adult |adult human |endogenous )?cardiac fibroblasts?',
     'cardiac fibroblast'),

    # ── Human fibroblast 变体 ─────────────────────────────────────────────
    (r'human (?:adult |primary |adult primary )?(?:dermal |skin |foreskin |embryonic lung )?fibroblasts?(?:\s*\([^)]*\))?',
     'human fibroblast'),
    (r'adult human (?:dermal |skin )?fibroblasts?',
     'human fibroblast'),

    # ── Mouse fibroblast 变体 ─────────────────────────────────────────────
    (r'mouse (?:tail[- ]tip |dermal |skin |adult )?fibroblasts?(?:\s*\([^)]*\))?',
     'mouse fibroblast'),
    (r'senescent mouse (?:tail skin )?fibroblasts?',
     'mouse fibroblast'),

    # ── 通用 fibroblast（无物种限定） ─────────────────────────────────────
    (r'(?:adult |dermal |skin |primary )?fibroblasts?',
     'fibroblast'),

    # ── Somatic cell ─────────────────────────────────────────────────────
    (r'(?:human |mouse )?somatic cells?',
     'somatic cell'),

    # ── PBMC ─────────────────────────────────────────────────────────────
    (r'peripheral blood mononuclear cells?(?:\s*\([^)]*\))?',
     'PBMC'),
    (r'PBMCs?\b',
     'PBMC'),

    # ── Mesenchymal stem cell ────────────────────────────────────────────
    (r'(?:human |mouse )?(?:bone marrow[- ]derived |bone marrow |umbilical cord[- ]derived |umbilical cord |adipose[- ]derived |dental )?mesenchymal stem cells?(?:\s*\([^)]*\))?',
     'mesenchymal stem cell (MSC)'),
    (r'(?:human |mouse )?(?:bone marrow[- ]derived |bone marrow |adipose[- ]derived )?MSCs?\b',
     'mesenchymal stem cell (MSC)'),
    (r'(?:human |mouse )?(?:adipose[- ]derived stem cells?|ADSCs?)(?:\s*\([^)]*\))?',
     'mesenchymal stem cell (MSC)'),

    # ── Embryonic stem cell ──────────────────────────────────────────────
    (r'human embryonic stem cells?(?:\s*\([^)]*\))?',
     'human embryonic stem cell (hESC)'),
    (r'hESCs?\b',
     'human embryonic stem cell (hESC)'),
    (r'mouse embryonic stem cells?(?:\s*\([^)]*\))?',
     'mouse embryonic stem cell (mESC)'),
    (r'mESCs?\b',
     'mouse embryonic stem cell (mESC)'),
    (r'embryonic stem cells?(?:\s*\([^)]*\))?',
     'embryonic stem cell (ESC)'),
    (r'ESCs?\b',
     'embryonic stem cell (ESC)'),

    # ── Hepatocyte ───────────────────────────────────────────────────────
    (r'(?:human |mouse |primary human |primary )?hepatocytes?',
     'hepatocyte'),
    (r'liver cells?',
     'hepatocyte'),

    # ── Astrocyte ────────────────────────────────────────────────────────
    (r'(?:human |mouse |cortical |reactive )?astrocytes?',
     'astrocyte'),

    # ── B cell ───────────────────────────────────────────────────────────
    (r'(?:CD19\+\s+)?B[\s-]?lymphocytes?',
     'B cell'),
    (r'pre[-\s]?B cells?',
     'pre-B cell'),

    # ── Urine cells ──────────────────────────────────────────────────────
    (r'human urine[- ]derived cells?',
     'urine cell'),
    (r'human urine cells?',
     'urine cell'),
    (r'urine cells?',
     'urine cell'),

    # ── Müller glia ──────────────────────────────────────────────────────
    (r'Müller glia(?:l cells?)?',
     'Müller glia'),

    # ── Pancreatic acinar cell ────────────────────────────────────────────
    (r'pancreatic (?:acinar|exocrine) cells?',
     'pancreatic acinar cell'),

    # ── HUVEC ────────────────────────────────────────────────────────────
    (r'human umbilical vein endothelial cells?(?:\s*\([^)]*\))?',
     'HUVEC'),
    (r'HUVECs?\b',
     'HUVEC'),

    # ── Spermatogonial stem cell ──────────────────────────────────────────
    (r'spermatogonial stem cells?',
     'spermatogonial stem cell'),

    # ── "unspecified" → 空字符串 ─────────────────────────────────────────
    (r'unspecified',
     ''),
]

COMPILED = [(re.compile(r'(?i)^' + p + r'$'), r) for p, r in RULES]


def standardize(name: str) -> str:
    name = name.strip()
    for pat, replacement in COMPILED:
        if pat.match(name):
            return replacement
    return name


def main():
    df = pd.read_csv(FILE, dtype=str).fillna('')

    before = df['source_cell'].nunique()
    df['source_cell'] = df['source_cell'].apply(standardize)
    after  = df['source_cell'].nunique()

    shutil.copy(FILE, FILE + '.bak')
    df.to_csv(FILE, index=False, encoding='utf-8')

    print(f'source_cell: {before} → {after} 种（减少 {before - after}）')
    print(f'保存至 {FILE}（备份：{FILE}.bak）')

    print('\nTop 20 source cell types (after):')
    print(df['source_cell'].value_counts().head(20).to_string())


if __name__ == '__main__':
    main()
