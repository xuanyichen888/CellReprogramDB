"""
标准化 recipes_master_v2.csv 中的 factors 列（每个因子独立标准化）
原地更新文件
"""
import pandas as pd, re, shutil

FILE = "recipes_master_v2.csv"

# (正则模式, 标准名称)  —— 匹配整个因子字符串（去除首尾空格后）
# 顺序很重要：更具体的放前面
RULES = [
    # ── OCT4 / POU5F1 ────────────────────────────────────────────────────
    (r'OCT[-\s]?3/4',          'OCT4'),
    (r'Oct[-\s]?3/4',          'OCT4'),
    (r'OCT[-\s]?4',            'OCT4'),
    (r'Oct[-\s]?4',            'OCT4'),
    (r'POU5F1',                'OCT4'),
    (r'Oct4',                  'OCT4'),

    # ── SOX2 ─────────────────────────────────────────────────────────────
    (r'SOX[-\s]?2',            'SOX2'),
    (r'Sox[-\s]?2',            'SOX2'),

    # ── KLF4 ─────────────────────────────────────────────────────────────
    (r'KLF[-\s]?4',            'KLF4'),
    (r'Klf[-\s]?4',            'KLF4'),

    # ── c-MYC ────────────────────────────────────────────────────────────
    (r'c[-\s]?[Mm][Yy][Cc]',   'c-MYC'),
    (r'C[-\s]?MYC',            'c-MYC'),
    (r'cMyc',                  'c-MYC'),
    (r'cMYC',                  'c-MYC'),

    # ── NANOG ────────────────────────────────────────────────────────────
    (r'Nanog',                 'NANOG'),

    # ── ASCL1 ────────────────────────────────────────────────────────────
    (r'Ascl1',                 'ASCL1'),

    # ── NGN2 / Neurogenin-2 ──────────────────────────────────────────────
    (r'Ngn2',                  'NGN2'),
    (r'Neurog2',               'NGN2'),
    (r'NEUROG2',               'NGN2'),
    (r'Neurogenin[-\s]?2',     'NGN2'),

    # ── NGN3 ─────────────────────────────────────────────────────────────
    (r'Ngn3',                  'NGN3'),
    (r'Neurog3',               'NGN3'),
    (r'NEUROG3',               'NGN3'),

    # ── NEUROD1 ──────────────────────────────────────────────────────────
    (r'NeuroD1',               'NEUROD1'),

    # ── GATA4 ────────────────────────────────────────────────────────────
    (r'Gata4',                 'GATA4'),

    # ── TBX5 ─────────────────────────────────────────────────────────────
    (r'Tbx5',                  'TBX5'),

    # ── MEF2C ────────────────────────────────────────────────────────────
    (r'Mef2c',                 'MEF2C'),

    # ── HAND2 ────────────────────────────────────────────────────────────
    (r'Hand2',                 'HAND2'),

    # ── FOXA2 ────────────────────────────────────────────────────────────
    (r'Foxa2',                 'FOXA2'),

    # ── FOXA3 ────────────────────────────────────────────────────────────
    (r'Foxa3',                 'FOXA3'),

    # ── HNF4A ────────────────────────────────────────────────────────────
    (r'HNF4[Aaα]',        'HNF4A'),
    (r'Hnf4[aα]',         'HNF4A'),

    # ── HNF1A ────────────────────────────────────────────────────────────
    (r'HNF1[Aaα]',        'HNF1A'),
    (r'Hnf1[aα]',         'HNF1A'),

    # ── BRN2 ─────────────────────────────────────────────────────────────
    (r'Brn2',                  'BRN2'),

    # ── BRN3B ────────────────────────────────────────────────────────────
    (r'Brn3b',                 'BRN3B'),

    # ── PDX1 ─────────────────────────────────────────────────────────────
    (r'Pdx1',                  'PDX1'),

    # ── PAX6 ─────────────────────────────────────────────────────────────
    (r'Pax6',                  'PAX6'),

    # ── NURR1 ────────────────────────────────────────────────────────────
    (r'Nurr1',                 'NURR1'),

    # ── GATA1 ────────────────────────────────────────────────────────────
    (r'GATA[-\s]?1(?!\d)',     'GATA1'),

    # ── GFI1 ─────────────────────────────────────────────────────────────
    (r'Gfi1(?!B)',             'GFI1'),

    # ── DLX2 ─────────────────────────────────────────────────────────────
    (r'Dlx2',                  'DLX2'),

    # ── SOX10 ────────────────────────────────────────────────────────────
    (r'Sox10',                 'SOX10'),

    # ── SOX9 ─────────────────────────────────────────────────────────────
    (r'Sox9',                  'SOX9'),

    # ── ATOH1 ────────────────────────────────────────────────────────────
    (r'Atoh1',                 'ATOH1'),

    # ── Small molecules ──────────────────────────────────────────────────
    (r'CHIR[-\s]?99021',       'CHIR99021'),
    (r'A[-\s]83[-\s]01',       'A83-01'),
    (r'[Ff]orskolin',          'Forskolin'),
    (r'[Dd]orsomorphin',       'Dorsomorphin'),
    (r'Y[-\s]?27632',          'Y-27632'),
    (r'[Rr]ep[Ss]ox',          'RepSox'),
    (r'5[-\s]azacytidine',     '5-azacytidine'),
    (r'valproic acid',         'VPA'),
    (r'Activin A',             'Activin A'),
    (r'activin A',             'Activin A'),

    # ── Growth factors ───────────────────────────────────────────────────
    (r'basic fibroblast growth factor', 'bFGF'),
    (r'hepatocyte growth factor(?:\s*\(HGF\))?', 'HGF'),
    (r'oncostatin M',          'OSM'),
    (r'epidermal growth factor','EGF'),

    # ── TGF-β ────────────────────────────────────────────────────────────
    (r'TGF[-\s]?beta\b',       'TGF-β'),
    (r'TGF[-\s]?β\b',     'TGF-β'),

    # ── vitamin C / ascorbic acid ─────────────────────────────────────────
    (r'ascorbic acid',         'vitamin C'),
    (r'^Vc$',                  'vitamin C'),

    # ── shRNA / knockdown notation ────────────────────────────────────────
    (r'shp53',                 'p53 shRNA'),
]

# 编译为 (compiled_pattern, replacement)
COMPILED = [(re.compile(r'^' + p + r'$'), r) for p, r in RULES]


def standardize_factor(name: str) -> str:
    name = name.strip()
    for pat, replacement in COMPILED:
        if pat.match(name):
            return replacement
    return name


def standardize_factors_field(field: str) -> str:
    """处理逗号分隔的因子列表"""
    parts = [f.strip() for f in field.split(',')]
    standardized = [standardize_factor(p) for p in parts if p]
    return ', '.join(standardized)


def main():
    df = pd.read_csv(FILE, dtype=str).fillna('')

    # 统计变化
    from collections import Counter
    before = Counter()
    for row in df['factors']:
        for f in row.split(','):
            f = f.strip()
            if f:
                before[f] += 1

    df['factors'] = df['factors'].apply(standardize_factors_field)

    after = Counter()
    for row in df['factors']:
        for f in row.split(','):
            f = f.strip()
            if f:
                after[f] += 1

    n_before = len(before)
    n_after  = len(after)

    # 备份
    shutil.copy(FILE, FILE + '.bak')
    df.to_csv(FILE, index=False, encoding='utf-8')

    print(f'factors 唯一值: {n_before} → {n_after} 种（减少 {n_before - n_after}）')
    print(f'保存至 {FILE}（备份：{FILE}.bak）')

    print('\nTop 30 factors (after):')
    for name, cnt in after.most_common(30):
        print(f'  {cnt:5d}  {name}')


if __name__ == '__main__':
    main()
