"""
把 T 细胞清单(tcell_review_checklist.xlsx)的人工核对判断写回主表。

按 (pmid, source_cell, target_cell[, factors]) 精确匹配。四类动作：
  1) 删除(人工 KEEP=N)  -> validation_recipe_valid=no + validation_action=remove
  2) 补因子              -> 写入 factors / factor_type，清旧的 hide 标记
  3) 单TF存疑确认        -> single_tf_status: unclear -> standalone_valid
  4) 保留并加注          -> 仅写 validation_notes

所有改动行标记 validation_resolution=human_reviewed 并清 needs_review。
运行后需重跑 mark_duplicates.py 与 mark_broad_duplicates.py。
"""
import pandas as pd
import shutil

FILE = "recipes_master_v2.csv"
df = pd.read_csv(FILE, dtype=str).fillna("")


def find(pmid, src, tgt, fac=None):
    m = (df["pmid"] == pmid) & (df["source_cell"] == src) & (df["target_cell"] == tgt)
    if fac is not None:
        m = m & (df["factors"] == fac)
    idx = df.index[m].tolist()
    if len(idx) != 1:
        raise SystemExit(f"匹配异常 {pmid}|{src[:25]}->{tgt[:25]} fac={fac}: 命中 {len(idx)} 行")
    return idx[0]


def note(i, txt):
    cur = str(df.at[i, "validation_notes"]).strip()
    df.at[i, "validation_notes"] = (cur + " | 人工核对: " + txt) if cur else "人工核对: " + txt
    df.at[i, "validation_needs_review"] = "False"
    df.at[i, "validation_resolution"] = "human_reviewed"


# 1) 删除 (KEEP=N)
drops = [
    ("33188841", "PDAC organoid-derived CD44(-) cancer cell", "CD44-expressing cancer-initiating cells (CICs)",
     "非TF重编程；CD44可塑性由Wnt/Notch旁分泌驱动，无外源因子"),
    ("28290453", "Th17 cell (IL-17A+Foxp3neg)", "regulatory T cell (Foxp3+)",
     "肿瘤微环境(TGF-β,PGE2)驱动体内自发转化，无可重复外源因子"),
    ("30548289", "Sox2-positive cardiac c-kit cells (CCs)", "cardiomyocyte",
     "依赖MSC共培养旁分泌+地塞米松，无定义TF cocktail"),
    ("30846549", "naive conventional CD4 T cell", "FOXP3 lineage regulatory T cell (Treg)",
     "TCR刺激+IL-2/TGF-β/PGE2耐受微环境，无特定TF"),
    ("40251434", "somatic cell", "pluripotent cells, totipotent cell",
     "化学重编程综述非原始实验；source笼统，factors不明"),
    ("41691922", "T cell", "induced pluripotent stem cell (iPSC)",
     "GMP制造工艺文，摘要未列Sendai具体TF，无法核实"),
    ("34403862", "myelin-specific Th17 cell", "encephalitogenic Th1-like cell",
     "AS1842856实为抑制Th17→Th1(负向)，方向错误，不构成促进recipe"),
]
for pmid, src, tgt, why in drops:
    i = find(pmid, src, tgt)
    df.at[i, "validation_recipe_valid"] = "no"
    df.at[i, "validation_action"] = "remove"
    note(i, why)

# 2) 补因子 (37885129) — 同时清掉旧的 hide_incomplete_recipe，置 recipe_valid=yes
i = find("37885129", "induced pluripotent stem cell", "pancreatic islet cells (beta and alpha)")
df.at[i, "factors"] = "sodium butyrate (NaB), retinoic acid (RA), KGF, LDN193189, ILV, T3, RepSox, nicotinamide"
df.at[i, "factor_type"] = ", ".join(["small_molecule"] * 8)
df.at[i, "factor_inference_method"] = "manual_fulltext"
df.at[i, "validation_action"] = ""
df.at[i, "validation_recipe_valid"] = "yes"
note(i, "iPSC→胰岛β细胞29天多步骤小分子方案；原'not specified'有误，已补代表性小分子")

# 3) 单TF存疑 -> standalone_valid + 注
singles = [
    ("15315119", "adult human pancreatic duct cell", "insulin-producing cell (β-cell)", "neurogenin 3",
     "NGN3单独启动内分泌分化但胰岛素低且不葡萄糖敏感，部分转化"),
    ("20215568", "Panc-1 pancreatic duct cell", "insulin-producing cell (β-cell)", "INSM1",
     "INSM1单独诱导~9.66%胰岛素+，联合Pdx-1+NeuroD1更高，单独不完整"),
    ("22606327", "adult human pancreatic duct cell", "insulin-producing cell (β-cell)", "NGN3",
     "NGN3单独<10%，Notch抑制或联Myt1增强，部分转化"),
    ("26288179", "acinar and duct cells of human exocrine pancreas", "insulin-producing cell (β-cell)", "Neurogenin 3 (NGN3)",
     "NGN3为内分泌祖标志，需21天pancosphere多步骤，非单独驱动"),
    ("27526291", "pancreatic acinar cell", "pancreatic duct cell", "PDX1",
     "PDX1单TF独立驱动腺泡→导管转分化(原文明确)，standalone确证"),
    ("31178416", "human embryonic stem cell", "hematopoietic stem cell-like progenitor (CD34+CD45+CD90+CD38-)", "GATA2 overexpression",
     "GATA2在AGM-S3基质共培养下促hESC→HSC样，需基质支持"),
    ("17355210", "transformed mouse pancreatic adenocarcinoma (mPAC) cell", "islet cell", "neurogenin3 (Ngn3)",
     "Ngn3效果依赖宿主内源TF背景(Pax4/6,Nkx6.1)，部分转化"),
    ("17355210", "mouse embryonic stem cell", "islet cell", "neurogenin3 (Ngn3)",
     "mESC对Ngn3响应弱于PDEC，依赖细胞TF背景"),
    ("22945304", "pancreatic duct cell", "insulin-producing cell (β-cell)", "TCF7L2",
     "TCF7L2诱增殖与ICC但依赖JAK2/STAT3，部分转化"),
]
for pmid, src, tgt, fac, why in singles:
    i = find(pmid, src, tgt, fac)
    df.at[i, "single_tf_status"] = "standalone_valid"
    note(i, why)

# 4) 保留 + 加注
keeps = [
    ("20808872", "human fetal fibroblast", "hESC-like pluripotent cell",
     "DNMT/HDAC抑制剂预处理+hESC提取物+KOSR部分重编程(OCT4/NANOG/SOX2上调，未完全)"),
    ("34403862", "CD4 T cell", "functional iTreg cell",
     "AS1842856(FoxO1抑制剂)在TGFβ下剂量依赖促Foxp3+iTreg；factors正确"),
]
for pmid, src, tgt, why in keeps:
    i = find(pmid, src, tgt)
    note(i, why)

shutil.copy(FILE, FILE + ".bak")
df.to_csv(FILE, index=False, encoding="utf-8")
print(f"写回完成：删除 {len(drops)} | 补因子 1 | 单TF→standalone {len(singles)} | 保留加注 {len(keeps)}")
