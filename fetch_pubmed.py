"""
Step 1: Download reprogramming paper abstracts from PubMed
Output: papers.csv with columns: pmid, title, abstract, year
"""

import time
import pandas as pd
from Bio import Entrez

Entrez.email = "xuanyichen888@gmail.com"

MAX_PER_QUERY = 500  # 每个查询最多抓多少篇（提高以覆盖早期文献）

# ── 搜索词分四批：TF / small molecule / miRNA+knockdown / 经典早期论文 ──────────

# 早期论文（2006-2015）常用 "defined factors" / "reprogramming factors"，
# 而非 "transcription factor"，因此所有TF查询都加入这两个备选词

TF_FACTOR_TERMS = (
    '(transcription factor OR defined factors OR reprogramming factors)'
)

TF_QUERIES = [
    # 主查询
    f'(cell reprogramming[Title/Abstract]) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(direct conversion OR direct reprogramming OR transdifferentiation OR induced pluripotent)[Title/Abstract]',

    # 神经元
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(neuron OR dopaminergic OR GABAergic OR motor neuron OR interneuron)[Title/Abstract]',

    # 心肌细胞
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(cardiomyocyte OR cardiac muscle OR induced cardiomyocyte)[Title/Abstract]',

    # 肝细胞
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(hepatocyte OR hepatic cell OR liver reprogramming)[Title/Abstract]',

    # 胰岛β细胞
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(beta cell OR pancreatic beta OR insulin-producing OR islet)[Title/Abstract]',

    # 内皮/造血
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(endothelial cell OR hematopoietic OR erythrocyte OR megakaryocyte)[Title/Abstract]',

    # 免疫细胞
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(dendritic cell OR macrophage OR T cell OR B cell OR natural killer)[Title/Abstract]',
]

SMALL_MOLECULE_QUERIES = [
    # chemical reprogramming 主查询
    '(chemical reprogramming[Title/Abstract]) AND '
    '(small molecule OR drug[Title/Abstract])',

    # 小分子诱导iPSC
    '(small molecule reprogramming[Title/Abstract]) AND '
    '(induced pluripotent OR iPSC OR pluripotency)[Title/Abstract]',

    # 小分子直接转分化
    '(small molecule[Title/Abstract]) AND (transdifferentiation OR direct conversion[Title/Abstract]) AND '
    '(neuron OR cardiomyocyte OR hepatocyte OR beta cell)[Title/Abstract]',

    # chemical-only reprogramming
    '(chemical-only reprogramming[Title/Abstract]) OR '
    '(chemically induced pluripotent[Title/Abstract])',
]

MIRNA_QUERIES = [
    # miRNA重编程
    '(miRNA reprogramming[Title/Abstract]) OR (microRNA reprogramming[Title/Abstract])',

    # miRNA + 直接转分化
    '(miRNA[Title/Abstract] OR microRNA[Title/Abstract]) AND '
    '(direct reprogramming OR transdifferentiation[Title/Abstract]) AND '
    '(neuron OR cardiomyocyte OR iPSC)[Title/Abstract]',

    # 基因敲除/knockdown辅助重编程
    '(knockdown OR knockout OR shRNA OR siRNA[Title/Abstract]) AND '
    '(reprogramming OR transdifferentiation[Title/Abstract]) AND '
    '(transcription factor OR cell conversion)[Title/Abstract]',
]

# ── 专门针对经典早期论文的查询（按landmark因子名称搜索）────────────────────────
# 这些查询覆盖2006-2015年的里程碑文章，这些文章通常用具体因子名而非通用术语

CLASSIC_QUERIES = [
    # iPSC经典：Yamanaka (Oct4/Sox2/Klf4/c-Myc) + Thomson (OCT4/SOX2/NANOG/LIN28)
    '(Oct4 OR Sox2 OR Klf4[Title/Abstract]) AND '
    '(induced pluripotent OR iPSC OR pluripotent stem cell[Title/Abstract]) AND '
    '(reprogramming OR induction)[Title/Abstract]',

    # 心脏重编程：Ieda GMT (Gata4+Mef2c+Tbx5)
    '(Gata4[Title/Abstract] OR Mef2c[Title/Abstract] OR Tbx5[Title/Abstract]) AND '
    '(cardiomyocyte OR cardiac reprogramming OR cardiac fibroblast)[Title/Abstract]',

    # 神经元直接转化：Vierbuchen (Ascl1+Brn2+Myt1l), Pang (miR-9/137)
    '(Ascl1[Title/Abstract] OR Brn2[Title/Abstract] OR Ngn2[Title/Abstract]) AND '
    '(direct conversion OR direct reprogramming OR induced neuron)[Title/Abstract]',

    # 胰腺β细胞：Zhou (Ngn3+Pdx1+Mafa)
    '(Ngn3[Title/Abstract] OR Pdx1[Title/Abstract] OR Mafa[Title/Abstract]) AND '
    '(pancreatic reprogramming OR beta cell OR exocrine)[Title/Abstract]',

    # 肝细胞：Huang/Morris (Foxa1/2/3, Hnf4a)
    '(Foxa2[Title/Abstract] OR Hnf4a[Title/Abstract] OR Foxa1[Title/Abstract]) AND '
    '(hepatocyte OR hepatic reprogramming OR liver cell)[Title/Abstract]',

    # 广谱 "defined factors" 直接转化（早期论文核心词）
    # 加入 "induction" 覆盖 Huang 2011 (iHep) 等用 "induction" 而非 "reprogramming" 的文章
    '(defined factors[Title/Abstract]) AND '
    '(direct conversion OR reprogramming OR transdifferentiation OR induction)[Title/Abstract] AND '
    '(fibroblast OR somatic cell)[Title/Abstract]',

    # iHep / induced hepatocyte（Huang 2011: Gata4+Hnf1a+Foxa3）
    '(iHep OR induced hepatocyte[Title/Abstract] OR hepatocyte-like cells[Title/Abstract]) AND '
    '(fibroblast OR reprogramming OR conversion OR induction)[Title/Abstract]',

    # 胰腺外分泌细胞体内重编程（Zhou 2008: in vivo, exocrine→beta-cell）
    '(pancreatic exocrine[Title/Abstract] OR exocrine cells[Title/Abstract]) AND '
    '(reprogramming OR conversion OR beta-cell OR beta cell)[Title/Abstract]',

    # In vivo reprogramming（Kurita 2018 皮肤, Qian 2012 心脏等）
    # 这类论文标题/摘要用 "in vivo reprogramming" 而非 "direct reprogramming"
    '(in vivo reprogramming[Title/Abstract]) AND '
    '(transcription factor OR defined factors OR cocktail)[Title/Abstract]',

    # "fate conversion" / "cell fate switch" — 部分论文不用 reprogramming 一词
    '(fate conversion[Title/Abstract] OR cell fate switch[Title/Abstract] OR '
    'fate switching[Title/Abstract]) AND '
    '(transcription factor OR defined factors)[Title/Abstract] AND '
    '(fibroblast OR somatic cell OR neuron OR cardiomyocyte OR hepatocyte)[Title/Abstract]',

    # 皮肤/角质细胞重编程（Kurita: DNp63/GRHL2/TFAP2A）
    '(keratinocyte[Title/Abstract]) AND '
    '(direct reprogramming OR transdifferentiation OR lineage conversion OR in vivo reprogramming)'
    '[Title/Abstract] AND (fibroblast OR somatic cell)[Title/Abstract]',

    # 多巴胺能神经元：Caiazzo/Kim (Ascl1/Nurr1/Lmx1a)
    '(Nurr1[Title/Abstract] OR Lmx1a[Title/Abstract]) AND '
    '(dopaminergic OR dopamine neuron OR midbrain)[Title/Abstract] AND '
    '(reprogramming OR conversion)[Title/Abstract]',

    # 造血细胞：Riddell/Graf (C/EBPa, GATA1, PU.1)
    '(GATA1[Title/Abstract] OR PU.1[Title/Abstract] OR CEBPa[Title/Abstract]) AND '
    '(reprogramming OR conversion OR transdifferentiation)[Title/Abstract] AND '
    '(hematopoietic OR blood cell OR macrophage OR neutrophil)[Title/Abstract]',
]

# ── 覆盖缺口补检索 ──────────────────────────────────────────────────────────
# 针对 postdoc 指出的遗漏术语：in vivo / lineage reprogramming、wound-resident
# 细胞、in situ 命运转换等。这些老/特殊术语不被上面的主查询覆盖。
COVERAGE_GAP_QUERIES = [
    '("wound-resident"[Title/Abstract] OR "wound resident"[Title/Abstract]) AND '
    '(reprogramming OR conversion OR transdifferentiation OR fate)[Title/Abstract]',

    '"in vivo reprogramming"[Title/Abstract]',

    '("in vivo"[Title/Abstract]) AND '
    '("direct reprogramming"[Title/Abstract] OR "lineage reprogramming"[Title/Abstract])',

    '"lineage reprogramming"[Title/Abstract]',

    '("in vivo"[Title/Abstract]) AND (transdifferentiation[Title/Abstract])',

    '("in situ"[Title/Abstract]) AND (reprogramming OR "fate conversion")[Title/Abstract] AND '
    '(transcription factor OR defined factors)[Title/Abstract]',
]

ALL_QUERIES = [
    ("TF",             q) for q in TF_QUERIES
] + [
    ("small_molecule", q) for q in SMALL_MOLECULE_QUERIES
] + [
    ("miRNA",          q) for q in MIRNA_QUERIES
] + [
    ("classic",        q) for q in CLASSIC_QUERIES
] + [
    ("coverage_gap",   q) for q in COVERAGE_GAP_QUERIES
]


def search_pubmed(query, max_results):
    handle = Entrez.esearch(db="pubmed", term=query, retmax=max_results)
    record = Entrez.read(handle)
    handle.close()
    return record["IdList"]


def fetch_abstracts(pmids, batch_size=100):
    all_papers = []
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        handle = Entrez.efetch(db="pubmed", id=batch, rettype="xml", retmode="xml")
        records = Entrez.read(handle)
        handle.close()
        for article in records["PubmedArticle"]:
            try:
                medline = article["MedlineCitation"]
                pmid    = str(medline["PMID"])
                title   = str(medline["Article"]["ArticleTitle"])
                abstract = ""
                if "Abstract" in medline["Article"]:
                    texts = medline["Article"]["Abstract"]["AbstractText"]
                    abstract = " ".join(str(t) for t in texts) if isinstance(texts, list) else str(texts)
                year = ""
                try:
                    year = str(medline["Article"]["Journal"]["JournalIssue"]["PubDate"]["Year"])
                except Exception:
                    try:
                        year = str(medline["Article"]["Journal"]["JournalIssue"]["PubDate"]["MedlineDate"])[:4]
                    except Exception:
                        year = "unknown"
                all_papers.append({"pmid": pmid, "title": title, "abstract": abstract, "year": year})
            except Exception as e:
                print(f"  Parse error: {e}")
        time.sleep(0.5)
    return all_papers


def main():
    # 读已有pmid
    existing_pmids = set()
    try:
        existing_df   = pd.read_csv("papers.csv", dtype=str)
        existing_pmids = set(existing_df["pmid"].tolist())
        print(f"已有论文: {len(existing_pmids)} 篇")
    except FileNotFoundError:
        existing_df = pd.DataFrame()

    all_new = []
    seen    = set()

    for category, query in ALL_QUERIES:
        print(f"\n[{category}] 搜索: {query[:80]}...")
        pmids     = search_pubmed(query, MAX_PER_QUERY)
        new_pmids = [p for p in pmids if p not in existing_pmids and p not in seen]
        print(f"  找到 {len(pmids)} 篇，新增 {len(new_pmids)} 篇")
        if not new_pmids:
            continue
        papers = fetch_abstracts(new_pmids)
        all_new.extend(papers)
        seen.update(p["pmid"] for p in papers)

    if not all_new:
        print("\n没有新论文。")
        return

    new_df   = pd.DataFrame(all_new)
    combined = pd.concat([existing_df, new_df], ignore_index=True)
    combined.drop_duplicates(subset="pmid", inplace=True)
    combined.to_csv("papers.csv", index=False, encoding="utf-8")
    print(f"\n完成！新增 {len(new_df)} 篇，总计 {len(combined)} 篇 → papers.csv")


if __name__ == "__main__":
    main()
